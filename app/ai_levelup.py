"""AI-assisted level-up (Phase 8, GitHub issue #55).

Serializes a character's full current sheet, sends it plus a free-text note
to an LLM, and gets back a structured proposal — never written directly to
the live character. The player can reply in plain text as many times as they
like to refine the proposal before finally applying it. Pure-ish module (a
DB session is passed in, but there's no FastAPI import) mirroring
app/pathbuilder.py's shape: one dedicated exception class whose message is
safe to show the user directly, and a hard rule against fabricating anything
not confidently derivable (never propose attack_bonus — it depends on the
wielder's own stats, not the item/spell itself).

Proposals only ever add brand-new Spell/Equipment/Feature/Note rows — never
modify or remove anything that already exists on the sheet — matching this
app's existing "copy, don't link" precedent (reference-library prefill,
Pathbuilder import both only ever add new rows).

Two interchangeable backends (AI_LEVELUP_PROVIDER, see app/config.py):
"anthropic" (default) uses Claude's official SDK and its schema-guaranteed
structured outputs; "ollama" targets a self-hosted Ollama/Open WebUI
server's OpenAI-compatible chat-completions endpoint via plain httpx
(already a project dependency — no new SDK for a single JSON POST). Local
open-weight models follow a JSON schema far less reliably than Claude, so
the Ollama path retries once with a sharper instruction before giving up,
rather than failing on the first malformed response.
"""

import json
import logging

import anthropic
import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import func
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.config import AI_LEVELUP_PROVIDER, ANTHROPIC_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL
from app.models import Character, Equipment, Feature, LevelUpSession, Note, Proficiency, Spell
from app.templating import clean_rich_text

logger = logging.getLogger("app")

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


class AiLevelUpError(Exception):
    """Any Claude API failure. Message is safe to show the user directly."""


# --- Structured output schema ---------------------------------------------


class ProposedCharacterUpdates(BaseModel):
    """Scalar Character field updates only. Every field optional — absent/
    None means "no change proposed" for that field. Deliberately excludes
    hp_current (current damage state, not something a level-up should
    touch) and hero_points (a per-session resource, unrelated to leveling).

    No _mod fields — a real 400 from Claude ("the compiled grammar is too
    large... reduce the number of strict tools") forced trimming this
    schema; the 6 ability modifiers were the clearest cut, since they're
    always mechanically derived from score under standard PF2e rules (the
    same (score - 10) // 2 formula app/pathbuilder.py's _mod() already
    uses) — archive_and_apply() computes them itself when a score is
    accepted, rather than trusting the model to keep score/mod consistent
    across a much larger schema.
    """

    level: int | None = None
    hp_max: int | None = None
    ac: int | None = None
    class_dc: int | None = None
    spell_dc: int | None = None
    spell_atk: int | None = None
    perception: int | None = None
    str_score: int | None = None
    dex_score: int | None = None
    con_score: int | None = None
    int_score: int | None = None
    wis_score: int | None = None
    cha_score: int | None = None


class ProposedSpell(BaseModel):
    """A brand-new Spell row to add — never a modification of an existing
    one. No attack_bonus (depends on the caster's own stats, not the
    spell), no reference_id/reference_version (not sourced from
    reference_library). No uses_current (a just-added spell has no
    meaningful "already used some today" state) or flags (low-value for an
    AI-authored row; add it by hand afterward if wanted) — both trimmed
    for the same grammar-size reason as ProposedCharacterUpdates above.
    """

    name: str
    rank: str | None = None
    uses: str | None = None
    uses_max: int | None = None
    action_cost: str | None = None
    range: str | None = None
    effect: str | None = None
    damage_formula: str | None = None


class ProposedEquipment(BaseModel):
    """A brand-new Equipment row to add. No attack_bonus, no
    reference_id/reference_version — same reasoning as ProposedSpell. No
    container (low-value for a newly-added item; same grammar-size
    trimming as above)."""

    name: str
    description: str | None = None
    notes: str | None = None
    qty: int | None = None
    damage_formula: str | None = None
    agile: bool = False


class ProposedFeature(BaseModel):
    """A brand-new Feature row to add. No reference_id/reference_version."""

    source: str | None = None
    name: str
    effect: str | None = None
    level_gained: int | None = None


class ProposedNote(BaseModel):
    """A brand-new Note to add — e.g. a quick combat reference for the
    character's current abilities, only proposed when it's clearly useful
    or the player asked for one. `body` goes through this app's normal
    rich-text sanitizer before saving (same as every other Note), so only
    <strong>/plain newlines survive — see SYSTEM_INSTRUCTIONS."""

    title: str | None = None
    body: str


class LevelUpProposal(BaseModel):
    summary: str
    character_updates: ProposedCharacterUpdates = ProposedCharacterUpdates()
    new_spells: list[ProposedSpell] = []
    new_equipment: list[ProposedEquipment] = []
    new_features: list[ProposedFeature] = []
    new_notes: list[ProposedNote] = []


class LevelUpTurn(BaseModel):
    """The one schema used for every turn. `message` is always populated —
    the chat reply, whether a clarifying question or an explanation of
    changes. `proposal` is only set once Claude has something concrete to
    show; it's fine for several turns to go by with just a message while
    Claude asks clarifying questions first."""

    message: str
    proposal: LevelUpProposal | None = None


# --- Character serialization ------------------------------------------------


def character_to_dict(character: Character) -> dict:
    """The full current sheet as a structured dict — deterministic, cheap
    to keep in sync with the data model (one field list per section), and
    more token-efficient than hand-formatted prose. Omits attack_bonus/
    reference_id/reference_version — not needed for Claude's job."""
    return {
        "name": character.name,
        "ancestry": character.ancestry,
        "class": character.character_class,
        "level": character.level,
        "size": character.size,
        "speed": character.speed,
        "languages": character.languages,
        "alignment": character.alignment,
        "hp_current": character.hp_current,
        "hp_max": character.hp_max,
        "ac": character.ac,
        "class_dc": character.class_dc,
        "spell_dc": character.spell_dc,
        "spell_atk": character.spell_atk,
        "perception": character.perception,
        "hero_points": character.hero_points,
        "money": {"pp": character.pp, "gp": character.gp, "sp": character.sp, "cp": character.cp},
        "abilities": {
            ab: {"score": getattr(character, f"{ab}_score"), "mod": getattr(character, f"{ab}_mod")}
            for ab in ("str", "dex", "con", "int", "wis", "cha")
        },
        "proficiencies": [
            {"name": p.name, "rank": p.rank, "bonus": p.bonus} for p in character.proficiencies
        ],
        "spells": [
            {
                "name": s.name, "rank": s.rank, "uses": s.uses, "uses_current": s.uses_current,
                "uses_max": s.uses_max, "action_cost": s.action_cost, "range": s.range,
                "effect": s.effect, "flags": s.flags, "damage_formula": s.damage_formula,
            }
            for s in character.spells
        ],
        "equipment": [
            {
                "name": e.name, "description": e.description, "notes": e.notes, "qty": e.qty,
                "container": e.container, "damage_formula": e.damage_formula, "agile": e.agile,
            }
            for e in character.equipment
        ],
        "features": [
            {"source": f.source, "name": f.name, "effect": f.effect, "level_gained": f.level_gained}
            for f in character.features
        ],
        "notes": [{"title": n.title, "body": n.body} for n in character.notes],
    }


_LEVEL_UP_TURN_JSON_SCHEMA = LevelUpTurn.model_json_schema()

SYSTEM_INSTRUCTIONS = f"""You are helping a Pathfinder 2e player level up their homebrew-heavy
tabletop character. You will see the character's full current sheet as JSON, followed by the
player's free-text note about what happened (new level, DM-granted items, feat choices, etc.).

Rules:
- Propose only NEW rows to add (spells/equipment/features/notes) — never modify or remove
  anything that already exists on the sheet. The player's existing rows are read-only context
  for judging what's already known/owned, not something you can edit or delete.
- Never invent a numeric attack_bonus for a new spell or equipment row — that depends on the
  wielder's own proficiency/ability modifiers, which you don't have precise enough context to
  compute reliably. Leave it out entirely.
- You may propose a new_notes entry when it's clearly useful (e.g. a quick combat reference
  summarizing strong action combos for the character's current spells/abilities) or the player
  explicitly asks for one — don't add one for every level-up unprompted. A note's `body` is
  plain text with real newlines for line breaks, except you may use <strong>...</strong> around
  short phrases for emphasis — no other HTML tags, no markdown (no #, *, -, etc.), since only
  <strong> and newlines survive this app's sanitizer and anything else is silently stripped.
- Only set a Character field (level, hp_max, ac, class_dc, spell_dc, spell_atk, perception,
  ability scores) when you are proposing an actual change to it — leave everything else
  null/absent. Ability modifiers aren't part of this schema; they're derived automatically
  from any proposed score using the standard (score - 10) // 2 formula.
- This is a homebrew table: the DM's stated grants always take precedence over published rules.
  If the player's note conflicts with what you'd expect from the rulebook, follow the note.
- Always fill in `message` with a short, plain-language explanation of what you're proposing (or
  a clarifying question if you need more information before proposing anything concrete).
- Only fill in `proposal` once you have a concrete, complete-enough leveled-up state to show the
  player as a diff. It's fine to ask one or two clarifying questions first via `message` alone.

Respond with ONLY a single JSON object matching this exact schema — no other text, no markdown
code fences, no explanation outside the JSON:

{json.dumps(_LEVEL_UP_TURN_JSON_SCHEMA, separators=(",", ":"))}
"""


def build_system_blocks(character: Character) -> list[dict]:
    sheet_json = json.dumps(character_to_dict(character), separators=(",", ":"))
    return [
        {"type": "text", "text": SYSTEM_INSTRUCTIONS},
        {
            "type": "text",
            "text": f"Current character sheet (JSON):\n{sheet_json}",
            # A real, free win for a back-and-forth conversation that
            # resends this same block every turn — not load-bearing to
            # correctness, just cheaper.
            "cache_control": {"type": "ephemeral"},
        },
    ]


# --- LLM call, dispatched by AI_LEVELUP_PROVIDER ----------------------------
#
# Neither provider uses grammar-constrained structured output (Claude's
# output_format=/output_config.format, or Ollama's response_format.json_schema)
# — a real request against the live API returned a 400 ("the compiled
# grammar is too large... reduce the number of strict tools") even after
# trimming the schema significantly, and reading the SDK's own source
# (anthropic/lib/_parse/_transform.py) confirmed output_format genuinely
# compiles to that same grammar-constrained path, not some separate,
# lighter-weight mechanism — so trimming further wasn't a reliable fix, only
# a smaller and equally fragile version of the same problem. Both providers
# now use the identical mechanism instead: the schema is embedded as plain
# text in the system prompt (SYSTEM_INSTRUCTIONS above), the reply is parsed
# as ordinary JSON, and a malformed response gets one retry with a sharper
# instruction before giving up — the same graceful-degradation design
# already agreed for Ollama's less-reliable local models, just now also
# covering Claude's real, encountered failure mode.

_anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

JSON_RETRY_INSTRUCTION = (
    "Your last response wasn't valid JSON matching the required schema. Respond with ONLY a "
    "single JSON object matching the schema — no other text, no markdown code fences, no "
    "explanation outside the JSON."
)


def _parse_turn(assistant_text: str) -> LevelUpTurn:
    """Strips a markdown code fence if the model wrapped its JSON in one
    despite being told not to (a common habit neither provider is immune
    to now that neither uses grammar-constrained generation), then
    validates. Raises pydantic.ValidationError on anything unparseable —
    callers use that to trigger the one-retry-then-give-up pattern."""
    text = assistant_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    return LevelUpTurn.model_validate_json(text)


def send_turn(character: Character, session: LevelUpSession, user_message: str) -> LevelUpTurn:
    """Appends user_message to the conversation, calls the active provider,
    appends the reply, updates session.messages_json/latest_proposal_json in
    place, and returns the parsed turn. Caller still does db.commit() — this
    only stages the change onto the passed-in `session` object, matching
    this app's established "helper mutates, router commits" split.
    """
    history = json.loads(session.messages_json)
    history.append({"role": "user", "content": user_message})

    try:
        if AI_LEVELUP_PROVIDER == "ollama":
            turn, assistant_text = _send_turn_ollama(character, history)
        else:
            turn, assistant_text = _send_turn_anthropic(character, history)
    except AiLevelUpError:
        raise
    except Exception:
        # Anything neither provider path anticipated (e.g. a self-hosted
        # Ollama/Open WebUI server responding in a shape this integration
        # hasn't seen yet — unlike the Anthropic path, this one isn't
        # verified against a real server). Logged in full here, since a
        # raw exception message isn't necessarily safe to show a player,
        # but this must never surface as an unhandled 500 — every /level-up
        # route only knows how to render AiLevelUpError as an inline
        # message in the transcript.
        logger.exception("Unexpected error calling the %s AI level-up provider", AI_LEVELUP_PROVIDER)
        raise AiLevelUpError(
            "Something went wrong talking to the AI provider. The details have been logged."
        ) from None

    history.append({"role": "assistant", "content": assistant_text})
    session.messages_json = json.dumps(history)
    session.latest_proposal_json = turn.proposal.model_dump_json() if turn.proposal else None
    return turn


def _call_anthropic(character: Character, history: list[dict]) -> str:
    if _anthropic_client is None:
        raise AiLevelUpError("AI level-up isn't configured on this server.")

    try:
        response = _anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=8000,
            system=build_system_blocks(character),
            messages=history,
            output_config={"effort": "high"},
        )
    except anthropic.RateLimitError as exc:
        raise AiLevelUpError("Claude is rate-limited right now — wait a moment and try again.") from exc
    except anthropic.BadRequestError as exc:
        raise AiLevelUpError(f"Claude rejected the request: {exc.message}") from exc
    except anthropic.APIStatusError as exc:
        raise AiLevelUpError(f"Claude API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AiLevelUpError("Couldn't reach Claude — check the network and try again.") from exc

    return next(b.text for b in response.content if b.type == "text")


def _send_turn_anthropic(character: Character, history: list[dict]) -> tuple[LevelUpTurn, str]:
    assistant_text = _call_anthropic(character, history)
    try:
        return _parse_turn(assistant_text), assistant_text
    except ValidationError:
        pass  # one retry below, with a sharper instruction — not persisted into history either way

    retry_history = [*history, {"role": "user", "content": JSON_RETRY_INSTRUCTION}]
    assistant_text = _call_anthropic(character, retry_history)
    try:
        return _parse_turn(assistant_text), assistant_text
    except ValidationError as exc:
        raise AiLevelUpError(
            "Claude couldn't produce a usable response after two tries. Try rephrasing your message."
        ) from exc


def _call_ollama(character: Character, history: list[dict]) -> str:
    """One request to an OpenAI-compatible chat-completions endpoint (raw
    Ollama, or Open WebUI's own compatible API). NOT verified against a
    real server — Ollama's response_format/json_schema support varies by
    version and model; the retry-and-validate wrapper in
    _send_turn_ollama() exists precisely because this can't be assumed to
    just work the way Claude's structured outputs do.
    """
    sheet_json = json.dumps(character_to_dict(character), separators=(",", ":"))
    system_text = f"{SYSTEM_INSTRUCTIONS}\n\nCurrent character sheet (JSON):\n{sheet_json}"
    messages = [{"role": "system", "content": system_text}, *history]

    try:
        response = httpx.post(
            f"{OLLAMA_BASE_URL}/chat/completions",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "LevelUpTurn", "schema": _LEVEL_UP_TURN_JSON_SCHEMA, "strict": True},
                },
                "stream": False,
            },
            # Local models, especially on CPU, can be far slower than a
            # hosted API — a much longer timeout than pathbuilder.py's 15s
            # is deliberate here, not an oversight.
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        raise AiLevelUpError(f"Couldn't reach the local AI server: {exc}") from exc
    except ValueError as exc:
        raise AiLevelUpError("The local AI server returned something that wasn't valid JSON.") from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        # Ollama/Open WebUI's exact response shape isn't verified against a
        # real server (see the PR this shipped in) — this is the specific,
        # anticipated "shape didn't match" case. logger.exception below
        # (via the caller's broader net) still records the real `data` for
        # diagnosis; this message alone stays generic since raw server
        # payloads aren't necessarily safe to show a player verbatim.
        raise AiLevelUpError("The local AI server's response didn't look like a chat completion.") from exc


def _send_turn_ollama(character: Character, history: list[dict]) -> tuple[LevelUpTurn, str]:
    if not (OLLAMA_BASE_URL and OLLAMA_MODEL):
        raise AiLevelUpError("AI level-up isn't configured on this server.")

    assistant_text = _call_ollama(character, history)
    try:
        return _parse_turn(assistant_text), assistant_text
    except ValidationError:
        pass  # one retry below, with a sharper instruction — not persisted into history either way

    retry_history = [*history, {"role": "user", "content": JSON_RETRY_INSTRUCTION}]
    assistant_text = _call_ollama(character, retry_history)
    try:
        return _parse_turn(assistant_text), assistant_text
    except ValidationError as exc:
        raise AiLevelUpError(
            "The local AI model couldn't produce a usable response after two tries. Try "
            "rephrasing your message, or ask your admin to check the Ollama/model configuration."
        ) from exc


# --- Archive-on-accept -------------------------------------------------------


def _clone_columns(source, model_cls, **overrides):
    """Copies every column from `source` (except `id`) into a brand-new
    `model_cls` instance — a real copy with its own future primary key,
    never a re-parenting of the original row."""
    cols = {c.key for c in sa_inspect(model_cls).mapper.column_attrs}
    data = {c: getattr(source, c) for c in cols if c != "id"}
    data.update(overrides)
    return model_cls(**data)


def _next_sort_order(db: Session, model_cls, character_id: int) -> int:
    """Appends to the end of the current display order — one past whatever
    the highest sort_order is for this character's rows of this type. Same
    "one past current max" pattern already used in spells.py/equipment.py/
    features.py's own _next_sort_order helpers, duplicated here rather than
    imported from those router files (matches this codebase's established
    convention of not sharing trivial per-resource helpers across files)."""
    max_order = db.query(func.max(model_cls.sort_order)).filter(
        model_cls.character_id == character_id
    ).scalar()
    return (max_order or 0) + 1


def archive_and_apply(
    character: Character,
    proposal: LevelUpProposal,
    accept_fields: list[str],
    accept_spell_indices: list[int],
    accept_equipment_indices: list[int],
    accept_feature_indices: list[int],
    accept_note_indices: list[int],
    session: LevelUpSession,
    db: Session,
) -> None:
    """Clones the character's CURRENT state into an archived row, then
    applies the accepted subset of the proposal onto the live character.
    Only ever called once, at final "Apply" — never per-field as-you-go.
    Stages everything via db.add()/db.flush()/db.delete(); the caller
    still does the final db.commit().
    """
    archive = _clone_columns(character, Character, is_archived=True, parent_character_id=character.id)
    db.add(archive)
    db.flush()  # assigns archive.id, needed as the FK target for cloned children below

    for child_attr, model_cls in (
        ("proficiencies", Proficiency),
        ("spells", Spell),
        ("equipment", Equipment),
        ("features", Feature),
        ("notes", Note),
    ):
        for row in getattr(character, child_attr):
            db.add(_clone_columns(row, model_cls, character_id=archive.id))

    updates = proposal.character_updates
    for field in accept_fields:
        if hasattr(updates, field):
            new_value = getattr(updates, field)
            setattr(character, field, new_value)
            # ProposedCharacterUpdates has no _mod fields (trimmed for
            # Claude's structured-output grammar-size limit) — derive the
            # modifier ourselves using the same formula app/pathbuilder.py's
            # _mod() already relies on, rather than trust the model to keep
            # score/mod consistent across a larger schema.
            if field.endswith("_score") and new_value is not None:
                setattr(character, field[: -len("score")] + "mod", (new_value - 10) // 2)

    # A running local counter, not a repeated _next_sort_order() query — this
    # app's SessionLocal disables autoflush, so a query-per-row here would
    # never see this same loop's own prior db.add()s and every accepted row
    # in one batch would collide on the same sort_order (the relationship's
    # `Spell.id` tiebreaker would incidentally paper over it, but that's
    # luck, not correctness).
    next_spell_order = _next_sort_order(db, Spell, character.id)
    for idx in accept_spell_indices:
        if 0 <= idx < len(proposal.new_spells):
            p = proposal.new_spells[idx]
            db.add(Spell(
                character_id=character.id, name=p.name, rank=p.rank, uses=p.uses,
                uses_max=p.uses_max, action_cost=p.action_cost,
                range=p.range, effect=p.effect, damage_formula=p.damage_formula,
                sort_order=next_spell_order,
            ))
            next_spell_order += 1

    next_equipment_order = _next_sort_order(db, Equipment, character.id)
    for idx in accept_equipment_indices:
        if 0 <= idx < len(proposal.new_equipment):
            p = proposal.new_equipment[idx]
            db.add(Equipment(
                character_id=character.id, name=p.name, description=p.description, notes=p.notes,
                qty=p.qty, damage_formula=p.damage_formula, agile=p.agile,
                sort_order=next_equipment_order,
            ))
            next_equipment_order += 1

    next_feature_order = _next_sort_order(db, Feature, character.id)
    for idx in accept_feature_indices:
        if 0 <= idx < len(proposal.new_features):
            p = proposal.new_features[idx]
            db.add(Feature(
                character_id=character.id, source=p.source, name=p.name, effect=p.effect,
                level_gained=p.level_gained, sort_order=next_feature_order,
            ))
            next_feature_order += 1

    for idx in accept_note_indices:
        if 0 <= idx < len(proposal.new_notes):
            p = proposal.new_notes[idx]
            # Sanitized the same way every other Note is on save (see
            # app/routers/notes.py) — a proposal's body is trusted no more
            # than anything else that ends up in this field.
            db.add(Note(character_id=character.id, title=p.title, body=clean_rich_text(p.body)))

    db.delete(session)
