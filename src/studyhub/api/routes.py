import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.api.dependencies import (
    Principal,
    administrator,
    current_user,
    database,
    educator,
    school_context,
)
from studyhub.core.security import opaque_token, password_hasher, token_hash
from studyhub.infrastructure.redis import rate_limit
from studyhub.models import AuthSession, Job, Membership, Outbox, Subject, User
from studyhub.schemas import (
    JobRead,
    Login,
    Refresh,
    SchoolUserRead,
    SubjectRead,
    SubjectWrite,
    Tokens,
    UserCreate,
    UserRead,
)
from studyhub.services import auth, subjects

router = APIRouter(prefix="/api/v1")


async def auth_limit(request: Request, identity: str):
    state = request.app.state
    ip = request.client.host if request.client else "unknown"
    await rate_limit(state.control, state.settings.namespace, f"auth:ip:{ip}", 30, 60)
    await rate_limit(
        state.control, state.settings.namespace, f"auth:account:{identity.lower()}", 8, 60
    )


@router.post("/auth/login", response_model=Tokens, tags=["auth"])
async def login(
    payload: Login, request: Request, response: Response, db: AsyncSession = Depends(database)
):
    await auth_limit(request, str(payload.email))
    response.headers["Cache-Control"] = "no-store"
    return await auth.login(db, request.app.state.settings, str(payload.email), payload.password)


@router.post("/auth/refresh", response_model=Tokens, tags=["auth"])
async def refresh(
    payload: Refresh, request: Request, response: Response, db: AsyncSession = Depends(database)
):
    await auth_limit(request, f"refresh:{token_hash(payload.refresh_token)}")
    response.headers["Cache-Control"] = "no-store"
    return await auth.rotate_refresh(db, request.app.state.settings, payload.refresh_token)


@router.post("/auth/logout", status_code=204, tags=["auth"])
async def logout(
    principal: Principal = Depends(current_user), db: AsyncSession = Depends(database)
):
    session = await db.get(AuthSession, principal.session_id)
    session.revoked_at = datetime.now(UTC)
    await db.commit()


@router.get("/users/me", response_model=UserRead, tags=["users"])
async def me(principal: Principal = Depends(current_user)):
    return principal.user


@router.post("/users", response_model=SchoolUserRead, status_code=201, tags=["users"])
async def create_user(
    payload: UserCreate,
    principal: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    user = User(
        email=str(payload.email).lower(),
        full_name=payload.full_name,
        password_hash=await asyncio.to_thread(password_hasher.hash, payload.password),
    )
    db.add(user)
    await db.flush()
    db.add(Membership(school_id=principal.school_id, user_id=user.id, role=payload.role))
    await db.commit()
    await db.refresh(user)
    return {
        **UserRead.model_validate(user).model_dump(),
        "role": payload.role,
        "membership_active": True,
    }


@router.get("/users", response_model=list[SchoolUserRead], tags=["users"])
async def users_list(
    principal: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    rows = (
        await db.execute(
            select(User, Membership)
            .join(Membership)
            .where(Membership.school_id == principal.school_id)
            .order_by(User.created_at, User.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [
        {
            **UserRead.model_validate(user).model_dump(),
            "role": membership.role,
            "membership_active": membership.is_active,
        }
        for user, membership in rows
    ]


@router.get("/subjects/statistics", tags=["subjects"])
async def statistics(
    request: Request,
    principal: Principal = Depends(school_context),
    db: AsyncSession = Depends(database),
):
    async def compute():
        return {
            "subject_count": await db.scalar(
                select(func.count())
                .select_from(Subject)
                .where(Subject.school_id == principal.school_id)
            )
        }

    return await request.app.state.cache.subject_statistics(compute, school_id=principal.school_id)


@router.get("/subjects", response_model=list[SubjectRead], tags=["subjects"])
async def subjects_list(
    principal: Principal = Depends(school_context),
    db: AsyncSession = Depends(database),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    return (
        await db.scalars(
            select(Subject)
            .where(Subject.school_id == principal.school_id)
            .order_by(Subject.code)
            .limit(limit)
            .offset(offset)
        )
    ).all()


@router.get("/subjects/{subject_id}", response_model=SubjectRead, tags=["subjects"])
async def subject_detail(
    subject_id: UUID,
    principal: Principal = Depends(school_context),
    db: AsyncSession = Depends(database),
):
    return await subjects.get_subject(db, principal.school_id, subject_id)


@router.post("/subjects", response_model=SubjectRead, status_code=201, tags=["subjects"])
async def subject_create(
    payload: SubjectWrite,
    request: Request,
    principal: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await subjects.save_subject(db, request.app.state.cache, principal.school_id, payload)


@router.put("/subjects/{subject_id}", response_model=SubjectRead, tags=["subjects"])
async def subject_update(
    subject_id: UUID,
    payload: SubjectWrite,
    request: Request,
    principal: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    return await subjects.save_subject(
        db, request.app.state.cache, principal.school_id, payload, subject_id
    )


@router.delete("/subjects/{subject_id}", status_code=204, tags=["subjects"])
async def subject_delete(
    subject_id: UUID,
    request: Request,
    principal: Principal = Depends(administrator),
    db: AsyncSession = Depends(database),
):
    await subjects.delete_subject(db, request.app.state.cache, principal.school_id, subject_id)


@router.post("/jobs/subject-report", response_model=JobRead, status_code=202, tags=["jobs"])
async def report_job(
    idempotency_key: str = Header(min_length=8, max_length=100),
    principal: Principal = Depends(educator),
    db: AsyncSession = Depends(database),
):
    # The owner+key constraint protects retries and concurrent submissions.
    job_id = await db.scalar(
        insert(Job)
        .values(
            school_id=principal.school_id,
            owner_id=principal.user.id,
            idempotency_key=idempotency_key,
        )
        .on_conflict_do_nothing(index_elements=[Job.school_id, Job.owner_id, Job.idempotency_key])
        .returning(Job.id)
    )
    if job_id:
        db.add(
            Outbox(
                school_id=principal.school_id, kind="job.requested", payload={"job_id": str(job_id)}
            )
        )
    else:
        job_id = await db.scalar(
            select(Job.id).where(
                Job.school_id == principal.school_id,
                Job.owner_id == principal.user.id,
                Job.idempotency_key == idempotency_key,
            )
        )
    await db.commit()
    return await db.get(Job, job_id)


@router.get("/jobs/{job_id}", response_model=JobRead, tags=["jobs"])
async def job_status(
    job_id: UUID,
    principal: Principal = Depends(school_context),
    db: AsyncSession = Depends(database),
):
    job = await db.scalar(
        select(Job).where(
            Job.id == job_id,
            Job.school_id == principal.school_id,
            Job.owner_id == principal.user.id,
        )
    )
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.post("/auth/ws-ticket", tags=["auth"])
async def websocket_ticket(request: Request, principal: Principal = Depends(school_context)):
    state = request.app.state
    await rate_limit(
        state.control, state.settings.namespace, f"ws-ticket:{principal.user.id}", 20, 60
    )
    ticket = opaque_token()
    await state.control.set(
        f"{state.settings.namespace}:ws-ticket:{token_hash(ticket)}",
        json.dumps(
            {
                "user_id": str(principal.user.id),
                "session_id": str(principal.session_id),
                "school_id": str(principal.school_id),
            }
        ),
        ex=30,
    )
    return {"ticket": ticket, "expires_in": 30}
