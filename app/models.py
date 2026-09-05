from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """A login identity, resolved by verified email from an OIDC provider.

    Phase 2 introduces this table but does not yet link characters to it —
    per-user scoping, the allow-list, and the admin role arrive in Phase 4.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="member")
    auth_source: Mapped[str | None] = mapped_column(String, nullable=True)
    is_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The short Authelia login handle (e.g. "gareth") for a local account,
    # set once via /admin/users' "Set password" action (Phase 4c) and never
    # renamed after. None for Entra-only or never-set-up users. Existing
    # databases need app/db.py's run_startup_migrations() to add this column
    # and its unique index — declaring it here alone doesn't retrofit a live
    # table (see that function's docstring).
    local_username: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    ancestry: Mapped[str | None] = mapped_column(String, nullable=True)
    character_class: Mapped[str | None] = mapped_column("class", String, nullable=True)
    level: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    size: Mapped[str | None] = mapped_column(String, nullable=True)
    speed: Mapped[str | None] = mapped_column(String, nullable=True)
    languages: Mapped[str | None] = mapped_column(String, nullable=True)
    alignment: Mapped[str | None] = mapped_column(String, nullable=True)

    hp_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hp_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ac: Mapped[int | None] = mapped_column(Integer, nullable=True)
    class_dc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spell_dc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spell_atk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    perception: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hero_points: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)

    # Money (issue #47) — a record of what's on hand, not a ledger; no
    # transaction history, just the current count of each coin type.
    # Existing databases need run_startup_migrations() to add these.
    pp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cp: Mapped[int | None] = mapped_column(Integer, nullable=True)

    str_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    str_mod: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dex_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dex_mod: Mapped[int | None] = mapped_column(Integer, nullable=True)
    con_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    con_mod: Mapped[int | None] = mapped_column(Integer, nullable=True)
    int_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    int_mod: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wis_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wis_mod: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cha_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cha_mod: Mapped[int | None] = mapped_column(Integer, nullable=True)

    avatar_path: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_thumb_path: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    owner: Mapped["User"] = relationship()
    proficiencies: Mapped[list["Proficiency"]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
        # Alphabetical by name (issue #49), case-insensitively — verified
        # directly that .collate("NOCASE") works in this string order_by
        # form, not assumed. A plain case-sensitive sort would otherwise
        # sort every capitalized seeded name before any lowercase custom
        # one a player types, which isn't what "alphabetical" means to a
        # reader.
        order_by='Proficiency.name.collate("NOCASE")',
    )
    spells: Mapped[list["Spell"]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
        # Manually reorderable (issue #48) via sort_order; id is only a
        # tiebreaker for rows that happen to share one (shouldn't occur in
        # practice, but costs nothing to guard against).
        order_by="Spell.sort_order, Spell.id",
    )
    equipment: Mapped[list["Equipment"]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
        order_by="Equipment.sort_order, Equipment.id",
    )
    features: Mapped[list["Feature"]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
        order_by="Feature.sort_order, Feature.id",
    )
    notes: Mapped[list["Note"]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
        # Pinned notes first (issue #46), each group otherwise in its
        # normal (creation) order — verified directly that a compound
        # string order_by like this actually works, not assumed.
        order_by="Note.is_pinned.desc(), Note.id",
    )


class Proficiency(Base):
    __tablename__ = "proficiencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    rank: Mapped[str | None] = mapped_column(String, nullable=True)
    bonus: Mapped[int | None] = mapped_column(Integer, nullable=True)

    character: Mapped["Character"] = relationship(back_populates="proficiencies")


class Spell(Base):
    __tablename__ = "spells"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Manual display order (issue #48) — set at creation to one past the
    # character's current max (append to the end), then swapped between
    # adjacent rows by the move-up/move-down action. Nullable at the DB
    # level (a single migration-time default can't give each existing row
    # a distinct value), but always explicitly set by application code, so
    # in practice it's never actually null. Existing databases need
    # run_startup_migrations() to add this column and backfill it from
    # each row's own id, which preserves the prior creation-order display
    # exactly, since that's what the old `order_by="Spell.id"` was.
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank: Mapped[str | None] = mapped_column(String, nullable=True)
    uses: Mapped[str | None] = mapped_column(String, nullable=True)
    # Optional tap-to-track counter (issue #43), separate from the free-text
    # `uses` field above (kept as-is for notes like "3/day" or "at will").
    # Mirrors Character.hp_current/hp_max: unclamped (may go below 0 or
    # above max — a record of what happened, not a rules engine), and only
    # shown in the UI when uses_max is set. Existing databases need
    # run_startup_migrations() to add these two columns.
    uses_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uses_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action_cost: Mapped[str | None] = mapped_column(String, nullable=True)
    range: Mapped[str | None] = mapped_column(String, nullable=True)
    effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    flags: Mapped[str | None] = mapped_column(String, nullable=True)
    attack_bonus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    damage_formula: Mapped[str | None] = mapped_column(String, nullable=True)
    # Set only when this row was prefilled from reference_library (Phase 7).
    # reference_version is a snapshot of the library entry's source_version
    # at copy time — existing databases need run_startup_migrations() to add
    # these two columns, declaring them here alone doesn't retrofit a live
    # table (see that function's docstring).
    reference_id: Mapped[int | None] = mapped_column(
        ForeignKey("reference_library.id"), nullable=True
    )
    reference_version: Mapped[str | None] = mapped_column(String, nullable=True)

    character: Mapped["Character"] = relationship(back_populates="spells")
    reference: Mapped["ReferenceLibrary | None"] = relationship(viewonly=True)


class Equipment(Base):
    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    # See Spell.sort_order above — same issue #48 pattern.
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    qty: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    container: Mapped[str | None] = mapped_column(String, nullable=True)
    attack_bonus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    damage_formula: Mapped[str | None] = mapped_column(String, nullable=True)
    agile: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # See Spell.reference_id/reference_version above — same Phase 7 pattern.
    reference_id: Mapped[int | None] = mapped_column(
        ForeignKey("reference_library.id"), nullable=True
    )
    reference_version: Mapped[str | None] = mapped_column(String, nullable=True)

    character: Mapped["Character"] = relationship(back_populates="equipment")
    reference: Mapped["ReferenceLibrary | None"] = relationship(viewonly=True)


class Feature(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # See Spell.sort_order above — same issue #48 pattern.
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    level_gained: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # See Spell.reference_id/reference_version above — same Phase 7 pattern.
    reference_id: Mapped[int | None] = mapped_column(
        ForeignKey("reference_library.id"), nullable=True
    )
    reference_version: Mapped[str | None] = mapped_column(String, nullable=True)

    character: Mapped["Character"] = relationship(back_populates="features")
    reference: Mapped["ReferenceLibrary | None"] = relationship(viewonly=True)


class ReferenceLibrary(Base):
    """Read-only, seeded from a vendored ingest snapshot (see
    scripts/ingest_reference_library.py and app/db.py's
    seed_reference_library()) — never written to by the app itself. A
    prefill source for Spell/Equipment/Feature rows, copied in and then
    fully independent; never a constraint (CLAUDE.md's free-form philosophy).

    Not character-scoped — one shared table for every user, like a
    read-only lookup table rather than user data.
    """

    __tablename__ = "reference_library"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_type: Mapped[str] = mapped_column(String, nullable=False, index=True)  # "spell" | "equipment" | "feature"
    foundry_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_version: Mapped[str] = mapped_column(String, nullable=False)

    rank: Mapped[str | None] = mapped_column(String, nullable=True)
    action_cost: Mapped[str | None] = mapped_column(String, nullable=True)
    range: Mapped[str | None] = mapped_column(String, nullable=True)
    uses: Mapped[str | None] = mapped_column(String, nullable=True)
    effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    damage_formula: Mapped[str | None] = mapped_column(String, nullable=True)
    agile: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    level_gained: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Provenance, carried straight through from Foundry's own
    # system.publication.{license,title} — shown per-entry in search results
    # and referenced by the site-wide attribution notice (Paizo Community
    # Use Policy / OGL / ORC expect visible attribution, not just a README
    # mention).
    license: Mapped[str | None] = mapped_column(String, nullable=True)
    publication_title: Mapped[str | None] = mapped_column(String, nullable=True)


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # issue #46 — pinned notes sort to the top (see Character.notes'
    # order_by below); existing databases need run_startup_migrations() to
    # add this column.
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    character: Mapped["Character"] = relationship(back_populates="notes")
