from pathlib import Path

import nh3
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Notes (Phase 5, issue #29) are the one field in this app that renders as
# HTML rather than escaped plain text — everything else stays plain text by
# design (see CLAUDE.md's free-form philosophy). This allowlist covers
# exactly what Quill's basic toolbar produces (verified directly against a
# real Quill instance, not assumed from docs): bold/italic/underline, both
# list types (Quill uses a single <ol> for both, distinguished per-<li> by
# `data-list="bullet"` vs `"ordered"`, with an empty <span class="ql-ui">
# marker it uses internally — both are inert, kept so lists round-trip
# correctly through re-editing and so the read-only display, wrapped in a
# .ql-editor div, picks up Quill's own CSS for the bullet/number glyphs),
# and links (Quill also adds target="_blank"; nh3/ammonia auto-adds
# rel="noopener noreferrer" regardless of what's requested). Shared (not
# duplicated) with app/routers/notes.py's save-time sanitization — these
# two allowlists must never be allowed to drift apart from each other.
NOTE_TAGS = {"p", "br", "strong", "em", "u", "ol", "li", "a", "span"}
NOTE_ATTRIBUTES = {"a": {"href", "target"}, "li": {"data-list"}, "span": {"class"}}
NOTE_URL_SCHEMES = {"http", "https", "mailto"}


def note_html(value: str | None) -> Markup:
    """Sanitizes a Note.body for display. Handles both Quill-authored HTML
    and plain newline-joined text uniformly — hand-typed notes from before
    this feature existed, and every Pathbuilder-import summary note
    (app/pathbuilder.py writes plain text directly and is deliberately not
    touched by this feature) both flow through here. nh3 turns a stray
    '<'/'>' in plain text into safe visible output rather than misparsing
    it, so no separate code path is needed to tell the two apart.

    Returns Markup so callers never need a separate |safe — removes any
    future risk of that being pasted onto some other, unsanitized field.
    """
    if not value:
        return Markup("")
    normalized = value.replace("\n", "<br>")
    cleaned = nh3.clean(
        normalized,
        tags=NOTE_TAGS,
        attributes=NOTE_ATTRIBUTES,
        url_schemes=NOTE_URL_SCHEMES,
    )
    return Markup(cleaned)


templates.env.filters["note_html"] = note_html


def render_fragment(name: str, **context) -> str:
    return templates.env.get_template(name).render(**context)
