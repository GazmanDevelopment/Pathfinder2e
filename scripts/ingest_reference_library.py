#!/usr/bin/env python3
"""One-off ingestion script for Phase 7's reference library (GitHub issue #38).

Sparse-clones github.com/foundryvtt/pf2e's v14-dev branch, restricted to
packs/pf2e/{spells,equipment,feats} (~31 MB — measured directly via the
GitHub API, not the full ~2 GB repo), and writes a normalized snapshot to
app/data/reference_library.json for the running app to seed at startup
(see app/db.py's seed_reference_library()).

Licensing basis (see CLAUDE.md's "Data source & licensing" section for the
full statement): the foundryvtt/pf2e repo's own code is Apache-2.0 (not
itself redistributed here — only data extracted from its JSON packs is
used); the PF2e game rules text in that data is under the OGL v1.0a and/or
Paizo's ORC License (each entry's own system.publication.license field says
which, carried through into the output snapshot's "license" field);
Paizo-owned names/trademarks/art are used under Paizo's Community Use
Policy (paizo.com/communityuse) — fine for a private, non-commercial,
self-hosted single table, which is what this app is. The app also shows a
site-wide attribution notice (app/templates/_attribution.html) whenever
this data is in use.

Run manually, from a developer machine with `git` installed:

    python scripts/ingest_reference_library.py

Not part of the Docker build or app startup — the running app only ever
reads the resulting app/data/reference_library.json; no network or git
access happens at runtime. Re-run and commit the refreshed JSON (like a
lockfile) whenever the vendored data should be updated.
"""

import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import nh3  # noqa: E402

from app.templating import RICH_TEXT_ATTRIBUTES, RICH_TEXT_TAGS, RICH_TEXT_URL_SCHEMES  # noqa: E402

REPO_URL = "https://github.com/foundryvtt/pf2e.git"
BRANCH = "v14-dev"
PACK_DIRS = ["packs/pf2e/spells", "packs/pf2e/equipment", "packs/pf2e/feats"]
OUTPUT_PATH = REPO_ROOT / "app" / "data" / "reference_library.json"

FOUNDRY_TYPE_TO_ENTRY_TYPE = {"spell": "spell", "feat": "feature"}
# Any other Foundry `type` is treated as equipment if recognized here, or
# silently skipped otherwise (counted and reported at the end so a common
# type isn't lost unnoticed) — matches how CLAUDE.md's data model groups
# "an item" broadly under a single Equipment table.
EQUIPMENT_FOUNDRY_TYPES = {
    "weapon", "armor", "equipment", "consumable", "treasure", "backpack", "shield", "kit", "ammo",
}

# Foundry's own inline "enricher" syntax for live document links/checks —
# e.g. "@UUID[Compendium.pf2e.conditionitems.Item.Dying]" (verified against
# a real fetched feat sample: Diehard's description). This is NOT HTML, so
# nh3 leaves it untouched as literal text; without stripping it first, the
# raw "@UUID[...]" markup would leak straight into the UI. Foundry's own
# client resolves the real display name live from the linked document at
# render time — this offline ingest has no such access, so the fallback
# below is a best-effort approximation (the trailing path segment, title-
# cased), not a guaranteed-correct label.
_FOUNDRY_ENRICHER_RE = re.compile(r"@(?:UUID|Check|Damage|Template|Localize|Embed)\[([^\]]*)\](?:\{([^}]*)\})?")


def _enricher_fallback_label(bracket: str) -> str:
    tail = bracket.split(".")[-1]
    tail = re.sub(r"[-_]", " ", tail).strip()
    return tail.title() if tail else bracket


def _strip_foundry_enrichers(html: str) -> str:
    def _replace(match: re.Match) -> str:
        bracket, label = match.group(1), match.group(2)
        return label if label else _enricher_fallback_label(bracket)

    return _FOUNDRY_ENRICHER_RE.sub(_replace, html)


def clean_html(value: str | None) -> str | None:
    if not value:
        return None
    de_enriched = _strip_foundry_enrichers(value)
    cleaned = nh3.clean(
        de_enriched, tags=RICH_TEXT_TAGS, attributes=RICH_TEXT_ATTRIBUTES, url_schemes=RICH_TEXT_URL_SCHEMES
    )
    return cleaned or None


def sparse_checkout(dest: Path) -> str:
    """Clones just PACK_DIRS at shallow depth; returns the short commit hash."""
    subprocess.run(
        [
            "git", "clone", "--filter=blob:none", "--sparse", "--depth", "1",
            "--branch", BRANCH, REPO_URL, str(dest),
        ],
        check=True,
    )
    subprocess.run(["git", "sparse-checkout", "set", *PACK_DIRS], cwd=dest, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=dest, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _damage_formula(system: dict) -> str | None:
    damage = system.get("damage")
    if not isinstance(damage, dict):
        return None
    # Weapon/equipment shape: {"damageType": "...", "dice": 1, "die": "d8"}
    # — checked first since it's unambiguous (verified against a real
    # weapon sample: Aldori Dueling Sword).
    if damage.get("dice") and damage.get("die"):
        return f"{damage['dice']}{damage['die']}"
    # Spell shape: {"0": {"formula": "6d6", ...}, "1": {...}, ...} — Foundry
    # keys spell damage instances by a numeric-string index, not a fixed
    # field name (verified against a real spell sample: Fireball). Take the
    # first instance's formula.
    first = next(iter(damage.values()), None)
    if isinstance(first, dict) and first.get("formula"):
        return str(first["formula"])
    return None


def _traits(system: dict) -> list[str]:
    return [str(t) for t in (system.get("traits") or {}).get("value") or []]


def _price_bulk_line(system: dict) -> str | None:
    price = (system.get("price") or {}).get("value") or {}
    bulk = (system.get("bulk") or {}).get("value")
    parts = []
    amount = ", ".join(f"{v} {k}" for k, v in price.items() if v)
    if amount:
        parts.append(f"Price: {amount}")
    if bulk not in (None, ""):
        parts.append(f"Bulk: {bulk}")
    return "; ".join(parts) if parts else None


def _spell_rank_text(level) -> str | None:
    if level is None:
        return None
    level = int(level)
    if level == 0:
        return "Cantrip"
    suffix = "th" if 10 <= level % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(level % 10, "th")
    return f"{level}{suffix}"


def map_entry(data: dict, entry_type: str) -> dict | None:
    system = data.get("system") or {}
    name = data.get("name")
    foundry_id = data.get("_id")
    if not name or not foundry_id:
        return None

    publication = system.get("publication") or {}
    entry = {
        "entry_type": entry_type,
        "foundry_id": foundry_id,
        "name": name,
        "source": "foundryvtt-pf2e",
        "license": publication.get("license"),
        "publication_title": publication.get("title"),
        "agile": False,
    }
    effect_html = clean_html((system.get("description") or {}).get("value"))

    if entry_type == "spell":
        entry["rank"] = _spell_rank_text((system.get("level") or {}).get("value"))
        entry["action_cost"] = (system.get("time") or {}).get("value")
        entry["range"] = (system.get("range") or {}).get("value")
        entry["damage_formula"] = _damage_formula(system)
        entry["effect"] = effect_html
    elif entry_type == "feature":
        entry["level_gained"] = (system.get("level") or {}).get("value")
        entry["effect"] = effect_html
    else:  # equipment
        entry["damage_formula"] = _damage_formula(system)
        entry["agile"] = "agile" in _traits(system)
        extra_line = _price_bulk_line(system)
        parts = [p for p in (effect_html, extra_line) if p]
        entry["effect"] = "<br>".join(parts) if parts else None

    return entry


def foundry_type_to_entry_type(foundry_type: str | None) -> str | None:
    if foundry_type in FOUNDRY_TYPE_TO_ENTRY_TYPE:
        return FOUNDRY_TYPE_TO_ENTRY_TYPE[foundry_type]
    if foundry_type in EQUIPMENT_FOUNDRY_TYPES:
        return "equipment"
    return None


def walk_pack(pack_dir: Path, skipped_types: Counter) -> list[dict]:
    entries = []
    for json_path in pack_dir.rglob("*.json"):
        if json_path.name == "_folders.json":
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        foundry_type = data.get("type")
        entry_type = foundry_type_to_entry_type(foundry_type)
        if entry_type is None:
            skipped_types[foundry_type] += 1
            continue
        entry = map_entry(data, entry_type)
        if entry is not None:
            entries.append(entry)
    return entries


def main():
    skipped_types: Counter = Counter()
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "pf2e"
        print(f"Cloning {REPO_URL}@{BRANCH} (sparse: {', '.join(PACK_DIRS)})...")
        commit = sparse_checkout(dest)
        print(f"Checked out at {commit}")

        entries = []
        for pack_dir in PACK_DIRS:
            found = walk_pack(dest / pack_dir, skipped_types)
            print(f"{pack_dir}: {len(found)} entries")
            entries.extend(found)

    if skipped_types:
        print(f"Skipped (unrecognized Foundry type): {dict(skipped_types)}")

    snapshot = {
        "source_version": commit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_repo": REPO_URL,
        "entries": entries,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Compact, not pretty-printed — this file is committed to git and baked
    # into every Docker image build, so its on-disk size matters; ~14,000
    # entries add up fast with indentation.
    OUTPUT_PATH.write_text(json.dumps(snapshot, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(entries)} entries to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
