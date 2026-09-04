from sqlalchemy.orm import Session

from app.models import ReferenceLibrary

SEARCH_LIMIT = 20


def search_reference(db: Session, entry_type: str, q: str) -> list[ReferenceLibrary]:
    """Shared by spells/equipment/features' reference-search endpoints —
    same query shape, only entry_type differs per resource."""
    q = q.strip()
    if not q:
        return []
    return (
        db.query(ReferenceLibrary)
        .filter(ReferenceLibrary.entry_type == entry_type, ReferenceLibrary.name.ilike(f"%{q}%"))
        .order_by(ReferenceLibrary.name)
        .limit(SEARCH_LIMIT)
        .all()
    )
