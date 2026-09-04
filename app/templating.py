import re
from pathlib import Path

import nh3
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Notes (Phase 5, issue #29) were the first field in this app to render as
# HTML rather than escaped plain text; Phase 7 (reference library) extends
# the same allowlist to Spell.effect, Equipment.description/notes, and
# Feature.effect, since reference-library entries carry real formatting
# (bold, lists) copied straight from Foundry's own rich-text descriptions.
# Everything else in the app stays plain text by design (see CLAUDE.md's
# free-form philosophy) — this is deliberately not the default.
#
# This allowlist covers exactly what Quill's basic toolbar produces
# (verified directly against a real Quill instance, not assumed from docs):
# bold/italic/underline, both list types (Quill uses a single <ol> for both,
# distinguished per-<li> by `data-list="bullet"` vs `"ordered"`, with an
# empty <span class="ql-ui"> marker it uses internally — both are inert,
# kept so lists round-trip correctly through re-editing and so the
# read-only display, wrapped in a .ql-editor div, picks up Quill's own CSS
# for the bullet/number glyphs), and links (Quill also adds
# target="_blank"; nh3/ammonia auto-adds rel="noopener noreferrer"
# regardless of what's requested). Shared (not duplicated) with
# app/routers/notes.py, spells.py, equipment.py, and features.py's
# save-time sanitization via clean_rich_text() below — these must never be
# allowed to drift apart from each other.
# "hr" is here for reference-library content (Phase 7) — real Foundry
# descriptions use it as a plain divider (e.g. Fireball's "...6d6 fire
# damage.<hr /><strong>Heightened (+1)</strong>...", verified against a
# real fetched sample). Quill's toolbar never produces one, so this is a
# no-op for Notes. It must live in this shared allowlist, not only in the
# ingestion script's own copy — clean_rich_text() below runs on every save
# a user makes too (not just ingestion), so a narrower save-time allowlist
# would silently strip it back out the first time a copied spell/item/
# feature was actually saved, even though the prefilled edit form showed it.
RICH_TEXT_TAGS = {"p", "br", "hr", "strong", "em", "u", "ol", "li", "a", "span"}
RICH_TEXT_ATTRIBUTES = {"a": {"href", "target"}, "li": {"data-list"}, "span": {"class"}}
RICH_TEXT_URL_SCHEMES = {"http", "https", "mailto"}

_EMPTY_RICH_TEXT_RE = re.compile(r"^\s*(<p>\s*(<br\s*/?>)?\s*</p>\s*)*$", re.IGNORECASE)


def rich_text(value: str | None) -> Markup:
    """Sanitizes a rich-text field for display: Note.body, Spell.effect,
    Equipment.description/notes, Feature.effect. Handles both Quill/
    reference-library-authored HTML and plain newline-joined text
    uniformly — hand-typed content from before this filter existed, and
    every Pathbuilder-import summary note (app/pathbuilder.py writes plain
    text directly and is deliberately not touched by this feature) both
    flow through here. nh3 turns a stray '<'/'>' in plain text into safe
    visible output rather than misparsing it, so no separate code path is
    needed to tell the two apart.

    Returns Markup so callers never need a separate |safe — removes any
    future risk of that being pasted onto some other, unsanitized field.
    """
    if not value:
        return Markup("")
    normalized = value.replace("\n", "<br>")
    cleaned = nh3.clean(
        normalized,
        tags=RICH_TEXT_TAGS,
        attributes=RICH_TEXT_ATTRIBUTES,
        url_schemes=RICH_TEXT_URL_SCHEMES,
    )
    return Markup(cleaned)


def clean_rich_text(value: str) -> str | None:
    """Sanitizes a rich-text field on the way in, shared by every router
    that writes one of the fields listed above (belt-and-suspenders — the
    rich_text() filter above is what actually matters for safety, since
    it's the one place every source converges, but this keeps the DB
    itself holding clean HTML for the Quill/reference-library-authored
    path specifically). Quill serializes an empty editor as "<p><br></p>",
    which is truthy, so without collapsing that to None, an emptied field
    would never go back to actually empty.
    """
    cleaned = nh3.clean(
        value, tags=RICH_TEXT_TAGS, attributes=RICH_TEXT_ATTRIBUTES, url_schemes=RICH_TEXT_URL_SCHEMES
    )
    return None if _EMPTY_RICH_TEXT_RE.match(cleaned) else cleaned


templates.env.filters["rich_text"] = rich_text


def render_fragment(name: str, **context) -> str:
    return templates.env.get_template(name).render(**context)
