from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.core.errors import ResourceNotFound
from studyhub.infrastructure.cache import Cache
from studyhub.models import Outbox, Subject
from studyhub.schemas import SubjectWrite


async def get_subject(db: AsyncSession, school_id: UUID, subject_id: UUID) -> Subject:
    subject = await db.scalar(
        select(Subject).where(Subject.id == subject_id, Subject.school_id == school_id)
    )
    if not subject:
        raise ResourceNotFound("Subject not found")
    return subject


async def save_subject(
    db: AsyncSession,
    cache: Cache,
    school_id: UUID,
    data: SubjectWrite,
    subject_id: UUID | None = None,
) -> Subject:
    if subject_id:
        subject = await db.scalar(
            select(Subject)
            .where(Subject.id == subject_id, Subject.school_id == school_id)
            .with_for_update()
        )
        if not subject:
            raise ResourceNotFound("Subject not found")
        for key, value in data.model_dump().items():
            setattr(subject, key, value)
    else:
        subject = Subject(school_id=school_id, **data.model_dump())
        db.add(subject)
    await db.flush()
    db.add(
        Outbox(
            school_id=school_id, kind="subjects.changed", payload={"subject_id": str(subject.id)}
        )
    )
    await db.commit()
    await cache.invalidate_subjects(school_id)  # Durable outbox retries if Redis is down here.
    return subject


async def delete_subject(db: AsyncSession, cache: Cache, school_id: UUID, subject_id: UUID):
    subject = await get_subject(db, school_id, subject_id)
    await db.delete(subject)
    db.add(
        Outbox(
            school_id=school_id, kind="subjects.changed", payload={"subject_id": str(subject_id)}
        )
    )
    await db.commit()
    await cache.invalidate_subjects(school_id)
