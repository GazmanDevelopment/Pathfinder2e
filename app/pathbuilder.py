"""Import a character from Pathbuilder 2e's JSON export, and export our
sheet back to a Pathbuilder-shaped JSON file (GitHub issue #34).

Pure module, no FastAPI/DB session imports — mirrors app/authelia_sync.py's
precedent for a concern that talks to a distinct external system, keeping
app/routers/characters.py a thin orchestrator.

The real schema was confirmed by fetching an actual live Pathbuilder export
(pathbuilder2e.com/json.php?id=<build>) and inspecting it directly, not
guessed. Both directions are explicitly a prefill/best-effort translation,
never an authority — matches this project's free-form, not-a-rules-engine
design (see CLAUDE.md). Anything Pathbuilder-derived we're not fully
confident about (skill/save totals, damage formulas) is left blank rather
than computed, and export is a one-way, lossy snapshot, not a round-trip —
this app tracks less structured detail than Pathbuilder does.
"""

import httpx

from app.models import Character, Equipment, Feature, Note, Proficiency, Spell

RANK_NAMES = ["Untrained", "Trained", "Expert", "Master", "Legendary"]

# Pathbuilder's proficiencies dict key -> this app's DEFAULT_PROFICIENCY_NAMES
# display name (app/seed_data.py). Only these 20 (3 saves + 17 skills) become
# Proficiency rows; the rest (classDC, perception, casting traditions, armor/
# weapon categories) have no matching row in "Saves & Skills" and go into the
# import summary Note instead.
STANDARD_PROFICIENCY_KEYS = {
    "fortitude": "Fortitude",
    "reflex": "Reflex",
    "will": "Will",
    "acrobatics": "Acrobatics",
    "arcana": "Arcana",
    "athletics": "Athletics",
    "crafting": "Crafting",
    "deception": "Deception",
    "diplomacy": "Diplomacy",
    "intimidation": "Intimidation",
    "medicine": "Medicine",
    "nature": "Nature",
    "occultism": "Occultism",
    "performance": "Performance",
    "religion": "Religion",
    "society": "Society",
    "stealth": "Stealth",
    "survival": "Survival",
    "thievery": "Thievery",
}

USER_AGENT = "Mozilla/5.0 (compatible; PathfinderCharacterSheet/1.0)"


class PathbuilderImportError(Exception):
    """Any fetch/parse failure. Message is safe to show the user directly."""


def _rank_name(value) -> str:
    try:
        idx = int(value) // 2
    except (TypeError, ValueError):
        return RANK_NAMES[0]
    return RANK_NAMES[idx] if 0 <= idx < len(RANK_NAMES) else RANK_NAMES[0]


def _rank_value(rank_text) -> int:
    if not isinstance(rank_text, str):
        return 0
    normalized = rank_text.strip().lower()
    for idx, name in enumerate(RANK_NAMES):
        if name.lower() == normalized:
            return idx * 2
    return 0


def _mod(score) -> int | None:
    return (score - 10) // 2 if isinstance(score, int) else None


def _ordinal(n: int) -> str:
    if n == 0:
        return "Cantrip"
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _spell_level(rank_text) -> int:
    if not rank_text:
        return 0
    text = rank_text.strip().lower()
    if text.startswith("cantrip"):
        return 0
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else 0


def fetch_build(build_id: str) -> dict:
    """Fetch and validate a Pathbuilder export. Raises PathbuilderImportError
    (message safe to show the user) on any failure."""
    build_id = build_id.strip()
    if not build_id.isdigit():
        raise PathbuilderImportError(
            "That doesn't look like a Pathbuilder build ID - it should be all digits."
        )
    try:
        response = httpx.get(
            "https://www.pathbuilder2e.com/json.php",
            params={"id": build_id},
            # Confirmed required — Pathbuilder's API 403s without a
            # browser-like User-Agent.
            headers={"User-Agent": USER_AGENT},
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        # Confirmed by testing against a real, nonexistent build ID:
        # Pathbuilder's API answers an unknown/invalid ID with a bare 403,
        # not a clean {"success": false} — a wrong ID looks identical to a
        # server-side block from here, so this covers both with one message
        # rather than surfacing a confusing raw "403 Forbidden".
        if exc.response.status_code == 403:
            raise PathbuilderImportError(
                "Pathbuilder didn't recognize that build ID. Double-check it, and "
                "make sure the character was exported from Pathbuilder via "
                "Export Character -> Export to Foundry VTT (JSON) - that's what "
                "generates this ID."
            ) from exc
        raise PathbuilderImportError(f"Couldn't reach Pathbuilder: {exc}") from exc
    except httpx.HTTPError as exc:
        raise PathbuilderImportError(f"Couldn't reach Pathbuilder: {exc}") from exc
    except ValueError as exc:
        raise PathbuilderImportError("Pathbuilder returned something that wasn't valid JSON.") from exc

    if not data.get("success"):
        raise PathbuilderImportError("No Pathbuilder character found with that build ID.")
    build = data.get("build")
    if not isinstance(build, dict):
        raise PathbuilderImportError("Pathbuilder's response didn't include character data.")
    return build


def apply_import(character: Character, build: dict, build_id: str) -> list[object]:
    """Sets scalar fields directly on `character` and returns a list of
    unsaved child rows (Proficiency/Equipment/Feature/Spell/Note) for the
    caller to add + commit.

    All parsing happens here, before any row is constructed — front-loads
    the fragile indexing (variable-length feats tuples, nested spell lists)
    so a malformed optional field can't leave a half-built character
    part-way through a series of db.add() calls.
    """
    character.name = build.get("name") or character.name
    character.ancestry = build.get("ancestry")
    character.character_class = build.get("class")
    character.level = build.get("level")
    character.alignment = build.get("alignment")
    character.size = build.get("sizeName")

    attrs = build.get("attributes") or {}
    speed = attrs.get("speed")
    if isinstance(speed, int):
        character.speed = f"{speed + (attrs.get('speedBonus') or 0)} ft"

    languages = build.get("languages")
    if isinstance(languages, list):
        character.languages = ", ".join(languages)

    abilities = build.get("abilities") or {}
    for key in ("str", "dex", "con", "int", "wis", "cha"):
        score = abilities.get(key)
        setattr(character, f"{key}_score", score)
        setattr(character, f"{key}_mod", _mod(score))

    level = build.get("level") or 0
    con_mod = _mod(abilities.get("con")) or 0
    hp_max = (
        (attrs.get("bonushp") or 0)
        + (attrs.get("classhp") or 0) * level
        + (attrs.get("ancestryhp") or 0)
        + con_mod * level
    )
    character.hp_max = hp_max
    character.hp_current = hp_max

    character.ac = (build.get("acTotal") or {}).get("acTotal")

    rows: list[object] = []

    proficiencies = build.get("proficiencies") or {}
    for pb_key, display_name in STANDARD_PROFICIENCY_KEYS.items():
        if pb_key in proficiencies:
            rows.append(Proficiency(name=display_name, rank=_rank_name(proficiencies[pb_key])))

    for lore in build.get("lores") or []:
        if isinstance(lore, list) and len(lore) >= 2:
            rows.append(Proficiency(name=f"{lore[0]} Lore", rank=_rank_name(lore[1])))

    for weapon in build.get("weapons") or []:
        if not isinstance(weapon, dict):
            continue
        rows.append(Equipment(
            name=weapon.get("display") or weapon.get("name") or "Weapon",
            qty=weapon.get("qty") or 1,
            attack_bonus=weapon.get("attack"),
        ))

    for armor in build.get("armor") or []:
        if not isinstance(armor, dict):
            continue
        rows.append(Equipment(
            name=armor.get("display") or armor.get("name") or "Armor",
            qty=armor.get("qty") or 1,
        ))

    for item in build.get("equipment") or []:
        if not isinstance(item, list) or not item:
            continue
        rows.append(Equipment(
            name=item[0],
            qty=item[1] if len(item) > 1 and isinstance(item[1], int) else 1,
            notes=item[2] if len(item) > 2 and isinstance(item[2], str) else None,
        ))

    for feat in build.get("feats") or []:
        if not isinstance(feat, list) or not feat:
            continue
        rows.append(Feature(
            name=feat[0],
            source=feat[2] if len(feat) > 2 and isinstance(feat[2], str) else None,
            level_gained=feat[3] if len(feat) > 3 and isinstance(feat[3], int) else None,
        ))

    for special in build.get("specials") or []:
        if isinstance(special, str):
            rows.append(Feature(name=special))

    for caster in build.get("spellCasters") or []:
        if not isinstance(caster, dict):
            continue
        for group in caster.get("spells") or []:
            if not isinstance(group, dict):
                continue
            rank = _ordinal(group.get("spellLevel") or 0)
            for spell_name in group.get("list") or []:
                if isinstance(spell_name, str):
                    rows.append(Spell(name=spell_name, rank=rank))

    for tradition in (build.get("focus") or {}).values():
        if not isinstance(tradition, dict):
            continue
        for ability_data in tradition.values():
            if not isinstance(ability_data, dict):
                continue
            for spell_name in ability_data.get("focusSpells") or []:
                if isinstance(spell_name, str):
                    rows.append(Spell(name=spell_name, rank="Focus"))

    rows.append(Note(
        title="Pathbuilder import summary",
        body="\n".join(_summary_lines(build, build_id, proficiencies)),
    ))

    return rows


def _summary_lines(build: dict, build_id: str, proficiencies: dict) -> list[str]:
    """Everything with no structured column — one place, not silently
    dropped and not guessed into a fabricated total bonus."""
    lines = [f"Imported from Pathbuilder (build {build_id})."]

    rank_extras = [
        ("Class DC", proficiencies.get("classDC")),
        ("Perception", proficiencies.get("perception")),
        ("Arcane casting", proficiencies.get("castingArcane")),
        ("Divine casting", proficiencies.get("castingDivine")),
        ("Occult casting", proficiencies.get("castingOccult")),
        ("Primal casting", proficiencies.get("castingPrimal")),
        ("Unarmored", proficiencies.get("unarmored")),
        ("Light armor", proficiencies.get("light")),
        ("Medium armor", proficiencies.get("medium")),
        ("Heavy armor", proficiencies.get("heavy")),
        ("Unarmed", proficiencies.get("unarmed")),
        ("Simple weapons", proficiencies.get("simple")),
        ("Martial weapons", proficiencies.get("martial")),
        ("Advanced weapons", proficiencies.get("advanced")),
    ]
    for label, value in rank_extras:
        if value is not None:
            lines.append(f"{label}: {_rank_name(value)}")

    for caster in build.get("spellCasters") or []:
        if isinstance(caster, dict) and caster.get("proficiency") is not None:
            lines.append(f"{caster.get('name', 'Spellcasting')} proficiency: {_rank_name(caster['proficiency'])}")

    for label, key in (
        ("Background", "background"), ("Deity", "deity"),
        ("Gender", "gender"), ("Age", "age"), ("Dual class", "dualClass"),
    ):
        value = build.get(key)
        if value:
            lines.append(f"{label}: {value}")

    money = build.get("money") or {}
    if any(money.values()):
        lines.append(f"Money: {money.get('pp', 0)}pp {money.get('gp', 0)}gp {money.get('sp', 0)}sp {money.get('cp', 0)}cp")

    resistances = build.get("resistances")
    if resistances:
        lines.append("Resistances: " + ", ".join(resistances))

    for key in ("familiars", "pets"):
        for critter in build.get(key) or []:
            if isinstance(critter, dict) and critter.get("name"):
                lines.append(f"{key[:-1].capitalize()}: {critter['name']}")

    return lines


def build_export_payload(character: Character) -> dict:
    """A best-effort, one-way export shaped like Pathbuilder's JSON.

    This app tracks less structured detail than Pathbuilder does (no weapon
    die/runes/material, no per-item container, no currency) — this is a
    lossy snapshot, not a real round-trip. Fields with nothing to draw from
    are simply omitted or defaulted rather than guessed at.
    """
    abilities = {
        "str": character.str_score, "dex": character.dex_score, "con": character.con_score,
        "int": character.int_score, "wis": character.wis_score, "cha": character.cha_score,
    }

    reverse_keys = {v: k for k, v in STANDARD_PROFICIENCY_KEYS.items()}
    proficiencies = {key: 0 for key in STANDARD_PROFICIENCY_KEYS}
    lores = []
    for prof in character.proficiencies:
        pb_key = reverse_keys.get((prof.name or "").strip())
        if pb_key:
            proficiencies[pb_key] = _rank_value(prof.rank)
        else:
            # Free text with no fixed-key home in Pathbuilder's schema — the
            # one place this app's free-form data genuinely can't round-trip
            # losslessly.
            lores.append([prof.name, _rank_value(prof.rank)])

    speed = 0
    if character.speed:
        digits = "".join(ch for ch in character.speed if ch.isdigit())
        speed = int(digits) if digits else 0

    equipment = [[item.name, item.qty or 1, item.notes or ""] for item in character.equipment]
    feats = [[f.name, None, f.source, f.level_gained] for f in character.features]

    spells_by_level: dict[int, list[str]] = {}
    for spell in character.spells:
        spells_by_level.setdefault(_spell_level(spell.rank), []).append(spell.name)
    spell_groups = [{"spellLevel": lvl, "list": names} for lvl, names in sorted(spells_by_level.items())]

    build = {
        "name": character.name,
        "class": character.character_class,
        "level": character.level,
        "ancestry": character.ancestry,
        "alignment": character.alignment,
        "sizeName": character.size,
        "languages": [lang.strip() for lang in (character.languages or "").split(",") if lang.strip()],
        "abilities": abilities,
        "attributes": {"speed": speed, "speedBonus": 0},
        "proficiencies": proficiencies,
        "lores": lores,
        "equipment": equipment,
        "feats": feats,
        "specials": [],
        "weapons": [],
        "armor": [],
        "money": {"cp": 0, "sp": 0, "gp": 0, "pp": 0},
        "spellCasters": [{
            "name": "Spells", "magicTradition": "", "spellcastingType": "", "ability": "",
            "proficiency": 0, "focusPoints": 0, "innate": False,
            "perDay": [0] * 11, "spells": spell_groups, "prepared": [], "blendedSpells": [],
        }] if spell_groups else [],
        "acTotal": {"acTotal": character.ac},
    }
    return {"success": True, "build": build}
