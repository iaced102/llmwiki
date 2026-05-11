"""Crawl Jobs router — manage site crawl jobs.

Endpoints:
  POST   /crawl-jobs          — create a new crawl job (task 2.1)
  GET    /crawl-jobs          — list crawl jobs, paginated (task 3.1)
  GET    /crawl-jobs/{job_id} — get crawl job detail (task 3.2)
  POST   /crawl-jobs/{job_id}/cancel — cancel a crawl job (task 4)
"""

import uuid
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import CrawlJob, Employee
from app.services.auth_service import get_current_user, require_permission

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class CrawlJobCreate(BaseModel):
    start_url: str
    knowledge_type_id: Optional[uuid.UUID] = None
    scope_type: str = "workspace"
    scope_id: Optional[uuid.UUID] = None


class CrawlJobResponse(BaseModel):
    id: uuid.UUID
    start_url: str
    domain: str
    status: str
    knowledge_type_id: Optional[uuid.UUID] = None
    scope_type: str
    scope_id: Optional[uuid.UUID] = None
    created_by_id: Optional[uuid.UUID] = None
    pages_discovered: int
    pages_crawled: int
    pages_failed: int
    pages_skipped: int
    error_message: Optional[str] = None
    job_id: Optional[str] = None
    created_at: str
    updated_at: str
    finished_at: Optional[str] = None

    model_config = {"from_attributes": True}


def _to_response(job: CrawlJob) -> CrawlJobResponse:
    return CrawlJobResponse(
        id=job.id,
        start_url=job.start_url,
        domain=job.domain,
        status=job.status,
        knowledge_type_id=job.knowledge_type_id,
        scope_type=job.scope_type,
        scope_id=job.scope_id,
        created_by_id=job.created_by_id,
        pages_discovered=job.pages_discovered,
        pages_crawled=job.pages_crawled,
        pages_failed=job.pages_failed,
        pages_skipped=job.pages_skipped,
        error_message=job.error_message,
        job_id=job.job_id,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
    )


# ---------------------------------------------------------------------------
# GET /crawl-jobs — paginated list ordered by created_at desc
# ---------------------------------------------------------------------------

@router.get("/crawl-jobs")
async def list_crawl_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: Employee = Depends(get_current_user),
):
    """Return a paginated list of crawl jobs ordered by created_at descending.

    page_size is enforced between 1 and 100 (default 20).
    Requirements: 5.6
    """
    count_stmt = select(func.count(CrawlJob.id))
    total: int = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    stmt = (
        select(CrawlJob)
        .order_by(CrawlJob.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    jobs = (await db.execute(stmt)).scalars().all()

    return {
        "items": [_to_response(j) for j in jobs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, -(-total // page_size)),
    }


# ---------------------------------------------------------------------------
# POST /crawl-jobs — create a new crawl job (task 2.1)
# ---------------------------------------------------------------------------

@router.post("/crawl-jobs", status_code=201)
async def create_crawl_job(
    body: CrawlJobCreate,
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("doc:create"),
):
    """Create a new site crawl job and enqueue the background task.

    Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8
    """
    # --- Validate URL ---
    try:
        parsed = urlparse(body.start_url)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid URL")

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(
            status_code=422,
            detail="start_url must be an http or https URL with a non-empty hostname",
        )

    # --- Validate scope_type / scope_id ---
    if body.scope_type == "project" and body.scope_id is None:
        raise HTTPException(
            status_code=422,
            detail="scope_id is required when scope_type is 'project'",
        )

    # Extract domain (hostname only, no port)
    domain = parsed.hostname

    # --- Create CrawlJob record ---
    job = CrawlJob(
        start_url=body.start_url,
        domain=domain,
        status="pending",
        knowledge_type_id=body.knowledge_type_id,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        created_by_id=user.id,
    )
    db.add(job)
    await db.flush()  # get job.id before enqueue

    # --- Enqueue crawl_site_task ---
    try:
        from app.worker import get_arq_pool
        arq_pool = await get_arq_pool()
        arq_job = await arq_pool.enqueue_job("crawl_site_task", str(job.id))
        job.job_id = arq_job.job_id if arq_job else None
    except Exception as exc:
        logger.error(f"Failed to enqueue crawl_site_task for job {job.id}: {exc}")
        job.status = "failed"
        job.error_message = f"Failed to enqueue background task: {str(exc)[:400]}"
        await db.commit()
        raise HTTPException(
            status_code=500,
            detail="Failed to enqueue crawl task. Job created with status 'failed'.",
        )

    await db.commit()
    await db.refresh(job)
    return _to_response(job)


# ---------------------------------------------------------------------------
# GET /crawl-jobs/{job_id} — get crawl job detail (task 3.2)
# ---------------------------------------------------------------------------

@router.get("/crawl-jobs/{job_id}")
async def get_crawl_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = Depends(get_current_user),
):
    """Return a single crawl job by ID.

    Returns HTTP 404 if the job does not exist.
    Requirements: 5.1, 6.3, 6.4
    """
    job = await db.get(CrawlJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Crawl job not found")
    return _to_response(job)


# ---------------------------------------------------------------------------
# POST /crawl-jobs/{job_id}/cancel — cancel a crawl job (task 4)
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@router.post("/crawl-jobs/{job_id}/cancel")
async def cancel_crawl_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = Depends(get_current_user),
):
    """Cancel a pending or running crawl job.

    - Returns HTTP 404 if the job does not exist.
    - Returns HTTP 400 if the job is already in a terminal state.
    - Sets status to 'cancelled' and returns HTTP 200 otherwise.
    Requirements: 6.5, 6.6, 6.7
    """
    job = await db.get(CrawlJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Crawl job not found")

    if job.status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel a job with status '{job.status}'",
        )

    job.status = "cancelled"
    await db.commit()
    await db.refresh(job)
    return _to_response(job)
