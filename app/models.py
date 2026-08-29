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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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

    proficiencies: Mapped[list["Proficiency"]] = relationship(
        back_populates="character", cascade="all, delete-orphan", order_by="Proficiency.id"
    )
    spells: Mapped[list["Spell"]] = relationship(
        back_populates="character", cascade="all, delete-orphan", order_by="Spell.id"
    )
    equipment: Mapped[list["Equipment"]] = relationship(
        back_populates="character", cascade="all, delete-orphan", order_by="Equipment.id"
    )
    features: Mapped[list["Feature"]] = relationship(
        back_populates="character", cascade="all, delete-orphan", order_by="Feature.id"
    )
    notes: Mapped[list["Note"]] = relationship(
        back_populates="character", cascade="all, delete-orphan", order_by="Note.id"
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
    rank: Mapped[str | None] = mapped_column(String, nullable=True)
    uses: Mapped[str | None] = mapped_column(String, nullable=True)
    action_cost: Mapped[str | None] = mapped_column(String, nullable=True)
    range: Mapped[str | None] = mapped_column(String, nullable=True)
    effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    flags: Mapped[str | None] = mapped_column(String, nullable=True)
    attack_bonus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    damage_formula: Mapped[str | None] = mapped_column(String, nullable=True)

    character: Mapped["Character"] = relationship(back_populates="spells")


class Equipment(Base):
    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    qty: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    container: Mapped[str | None] = mapped_column(String, nullable=True)
    attack_bonus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    damage_formula: Mapped[str | None] = mapped_column(String, nullable=True)
    agile: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    character: Mapped["Character"] = relationship(back_populates="equipment")


class Feature(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    level_gained: Mapped[int | None] = mapped_column(Integer, nullable=True)

    character: Mapped["Character"] = relationship(back_populates="features")


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    character: Mapped["Character"] = relationship(back_populates="notes")
