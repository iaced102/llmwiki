"""Crawl Jobs router — manage site crawl jobs.

Endpoints:
  POST   /crawl-jobs                        — create a discover job
  GET    /crawl-jobs                        — list crawl jobs, paginated
  GET    /crawl-jobs/{job_id}               — get crawl job detail
  GET    /crawl-jobs/{job_id}/discovered-urls — list discovered URLs
  POST   /crawl-jobs/{job_id}/start         — start crawl with selected URLs
  POST   /crawl-jobs/{job_id}/cancel        — cancel a crawl job
  POST   /crawl-jobs/{job_id}/retry         — retry failed URLs
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
    url_pattern: Optional[str] = None
    max_pages: Optional[int] = None


class CrawlJobStartBody(BaseModel):
    selected_urls: list[str]


class CrawlJobResponse(BaseModel):
    id: uuid.UUID
    start_url: str
    domain: str
    status: str
    mode: str = "crawl"
    knowledge_type_id: Optional[uuid.UUID] = None
    scope_type: str
    scope_id: Optional[uuid.UUID] = None
    created_by_id: Optional[uuid.UUID] = None
    pages_discovered: int
    pages_crawled: int
    pages_failed: int
    pages_skipped: int
    failed_urls: list[str] = []
    discovered_urls: list[str] = []
    selected_urls: list[str] = []
    url_pattern: Optional[str] = None
    max_pages: Optional[int] = None
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
        mode=job.mode,
        knowledge_type_id=job.knowledge_type_id,
        scope_type=job.scope_type,
        scope_id=job.scope_id,
        created_by_id=job.created_by_id,
        pages_discovered=job.pages_discovered,
        pages_crawled=job.pages_crawled,
        pages_failed=job.pages_failed,
        pages_skipped=job.pages_skipped,
        failed_urls=list(job.failed_urls or []),
        discovered_urls=list(job.discovered_urls or []),
        selected_urls=list(job.selected_urls or []),
        url_pattern=job.url_pattern,
        max_pages=job.max_pages,
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
# POST /crawl-jobs — create a discover job (BFS collect links only)
# ---------------------------------------------------------------------------

@router.post("/crawl-jobs", status_code=201)
async def create_crawl_job(
    body: CrawlJobCreate,
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("doc:create"),
):
    """Create a discover job: BFS the site to collect all URLs, no PDF rendering.
    After discovery completes (status='discovered'), user selects URLs and calls /start.
    """
    try:
        parsed = urlparse(body.start_url)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid URL")

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(
            status_code=422,
            detail="start_url must be an http or https URL with a non-empty hostname",
        )

    if body.scope_type == "project" and body.scope_id is None:
        raise HTTPException(
            status_code=422,
            detail="scope_id is required when scope_type is 'project'",
        )

    domain = parsed.hostname

    job = CrawlJob(
        start_url=body.start_url,
        domain=domain,
        status="pending",
        mode="discover",
        knowledge_type_id=body.knowledge_type_id,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        created_by_id=user.id,
        url_pattern=body.url_pattern,
        max_pages=body.max_pages,
    )
    db.add(job)
    await db.flush()

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
# GET /crawl-jobs/{job_id} — get crawl job detail
# ---------------------------------------------------------------------------

@router.get("/crawl-jobs/{job_id}")
async def get_crawl_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = Depends(get_current_user),
):
    job = await db.get(CrawlJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Crawl job not found")
    return _to_response(job)


# ---------------------------------------------------------------------------
# GET /crawl-jobs/{job_id}/discovered-urls — list discovered URLs
# ---------------------------------------------------------------------------

@router.get("/crawl-jobs/{job_id}/discovered-urls")
async def get_discovered_urls(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = Depends(get_current_user),
):
    """Return the list of URLs discovered during the BFS discovery phase."""
    job = await db.get(CrawlJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Crawl job not found")
    return {
        "job_id": str(job.id),
        "status": job.status,
        "discovered_urls": list(job.discovered_urls or []),
        "total": len(job.discovered_urls or []),
    }


# ---------------------------------------------------------------------------
# POST /crawl-jobs/{job_id}/start — start crawl with selected URLs
# ---------------------------------------------------------------------------

@router.post("/crawl-jobs/{job_id}/start", status_code=201)
async def start_crawl_job(
    job_id: uuid.UUID,
    body: CrawlJobStartBody,
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("doc:create"),
):
    """Create a new crawl job seeded with the user-selected URLs.

    - Original job must have status='discovered'.
    - selected_urls must be non-empty.
    - Creates a new CrawlJob with mode='crawl' and enqueues crawl_site_task.
    """
    original = await db.get(CrawlJob, job_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Crawl job not found")

    if original.status != "discovered":
        raise HTTPException(
            status_code=400,
            detail=f"Job must have status 'discovered' to start crawl (current: '{original.status}')",
        )

    if not body.selected_urls:
        raise HTTPException(status_code=422, detail="selected_urls must not be empty")

    # Validate all selected URLs are from the discovered set
    discovered_set = set(original.discovered_urls or [])
    invalid = [u for u in body.selected_urls if u not in discovered_set]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Some URLs were not in the discovered set: {invalid[:5]}",
        )

    new_job = CrawlJob(
        start_url=original.start_url,
        domain=original.domain,
        status="pending",
        mode="crawl",
        knowledge_type_id=original.knowledge_type_id,
        scope_type=original.scope_type,
        scope_id=original.scope_id,
        created_by_id=user.id,
        selected_urls=body.selected_urls,
        visited_urls=[],
        failed_urls=[],
    )
    db.add(new_job)
    await db.flush()

    try:
        from app.worker import get_arq_pool
        arq_pool = await get_arq_pool()
        arq_job = await arq_pool.enqueue_job(
            "crawl_site_task", str(new_job.id),
            retry_urls=body.selected_urls,  # reuse retry_urls param to seed queue
        )
        new_job.job_id = arq_job.job_id if arq_job else None
    except Exception as exc:
        logger.error(f"Failed to enqueue crawl_site_task for job {new_job.id}: {exc}")
        new_job.status = "failed"
        new_job.error_message = f"Failed to enqueue background task: {str(exc)[:400]}"
        await db.commit()
        raise HTTPException(
            status_code=500,
            detail="Failed to enqueue crawl task. Job created with status 'failed'.",
        )

    await db.commit()
    await db.refresh(new_job)
    return _to_response(new_job)


# ---------------------------------------------------------------------------
# POST /crawl-jobs/{job_id}/cancel — cancel a crawl job
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@router.post("/crawl-jobs/{job_id}/cancel")
async def cancel_crawl_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = Depends(get_current_user),
):
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

    # Also mark all pending/processing Source records from this job as cancelled
    # so any already-queued ingest_file_task jobs will abort early
    from sqlalchemy import select as sa_select
    from app.database.models import Source
    stmt = sa_select(Source).where(
        Source.metadata_["crawl_job_id"].astext == str(job_id),
        Source.status.in_(["pending", "processing"]),
    )
    sources = (await db.execute(stmt)).scalars().all()
    for source in sources:
        source.status = "cancelled"
    if sources:
        await db.commit()

    return _to_response(job)


# ---------------------------------------------------------------------------
# POST /crawl-jobs/{job_id}/retry — retry failed URLs
# ---------------------------------------------------------------------------

@router.post("/crawl-jobs/{job_id}/retry", status_code=201)
async def retry_crawl_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("doc:create"),
):
    """Create a new crawl job that retries only the failed URLs from a previous job."""
    original = await db.get(CrawlJob, job_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Crawl job not found")

    failed_urls = list(original.failed_urls or [])
    if not failed_urls:
        raise HTTPException(
            status_code=400,
            detail="No failed URLs to retry for this job",
        )

    new_job = CrawlJob(
        start_url=original.start_url,
        domain=original.domain,
        status="pending",
        mode="crawl",
        knowledge_type_id=original.knowledge_type_id,
        scope_type=original.scope_type,
        scope_id=original.scope_id,
        created_by_id=user.id,
        visited_urls=[],
        failed_urls=[],
    )
    db.add(new_job)
    await db.flush()

    try:
        from app.worker import get_arq_pool
        arq_pool = await get_arq_pool()
        arq_job = await arq_pool.enqueue_job(
            "crawl_site_task", str(new_job.id), retry_urls=failed_urls
        )
        new_job.job_id = arq_job.job_id if arq_job else None
    except Exception as exc:
        logger.error(f"Failed to enqueue retry crawl_site_task for job {new_job.id}: {exc}")
        new_job.status = "failed"
        new_job.error_message = f"Failed to enqueue background task: {str(exc)[:400]}"
        await db.commit()
        raise HTTPException(
            status_code=500,
            detail="Failed to enqueue retry task. Job created with status 'failed'.",
        )

    await db.commit()
    await db.refresh(new_job)
    return _to_response(new_job)


# ---------------------------------------------------------------------------
# GET /crawl-jobs/{job_id}/pages — list Source records for a crawl job
# ---------------------------------------------------------------------------

@router.get("/crawl-jobs/{job_id}/pages")
async def get_crawl_job_pages(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = Depends(get_current_user),
):
    """Return all Source records created by this crawl job."""
    from sqlalchemy import select as sa_select
    from app.database.models import Source

    job = await db.get(CrawlJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Crawl job not found")

    stmt = sa_select(Source).where(
        Source.metadata_["crawl_job_id"].astext == str(job_id)
    ).order_by(Source.created_at.asc())
    sources = (await db.execute(stmt)).scalars().all()

    return {
        "job_id": str(job_id),
        "pages": [
            {
                "id": str(s.id),
                "title": s.title,
                "url": s.url,
                "status": s.status,
                "error_message": s.error_message,
                "has_pdf": bool(s.minio_key),
                "created_at": s.created_at.isoformat(),
            }
            for s in sources
        ],
        "total": len(sources),
    }


# ---------------------------------------------------------------------------
# GET /crawl-jobs/{job_id}/pages/{source_id}/pdf-url — presigned PDF URL
# ---------------------------------------------------------------------------

@router.get("/crawl-jobs/{job_id}/pages/{source_id}/pdf-url")
async def get_page_pdf_url(
    job_id: uuid.UUID,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = Depends(get_current_user),
):
    """Return a short-lived presigned URL to view the PDF for a crawled page."""
    from app.database.models import Source
    from app.services.storage_service import storage_service

    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if not source.minio_key:
        raise HTTPException(status_code=404, detail="PDF not available for this page")

    try:
        url = storage_service.get_presigned_url(source.minio_key, expiry_hours=1)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate PDF URL: {exc}")

    return {"pdf_url": url}


# ---------------------------------------------------------------------------
# POST /crawl-jobs/{job_id}/ingest — enqueue ingest for selected pages
# ---------------------------------------------------------------------------

class IngestPagesBody(BaseModel):
    source_ids: list[uuid.UUID]


@router.post("/crawl-jobs/{job_id}/ingest")
async def ingest_crawl_pages(
    job_id: uuid.UUID,
    body: IngestPagesBody,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("doc:create"),
):
    """Enqueue ingest_file_task for the selected Source records.

    Only sources with status 'pending' or 'error' are accepted.
    """
    from sqlalchemy import select as sa_select
    from app.database.models import Source
    from app.worker import get_arq_pool

    if not body.source_ids:
        raise HTTPException(status_code=422, detail="source_ids must not be empty")

    job = await db.get(CrawlJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Crawl job not found")

    stmt = sa_select(Source).where(Source.id.in_(body.source_ids))
    sources = (await db.execute(stmt)).scalars().all()

    if not sources:
        raise HTTPException(status_code=404, detail="No matching sources found")

    arq_pool = await get_arq_pool()
    enqueued = []
    errors = []

    for source in sources:
        if source.status not in ("pending", "error"):
            errors.append({
                "id": str(source.id),
                "reason": f"status is '{source.status}', expected 'pending' or 'error'",
            })
            continue
        if not source.minio_key:
            errors.append({"id": str(source.id), "reason": "no PDF in storage"})
            continue
        try:
            arq_job = await arq_pool.enqueue_job("ingest_file_task", str(source.id))
            source.job_id = arq_job.job_id if arq_job else None
            source.status = "pending"
            source.progress = 0
            source.progress_message = "Queued for ingestion..."
            source.error_message = None
            enqueued.append(str(source.id))
        except Exception as exc:
            errors.append({"id": str(source.id), "reason": str(exc)[:200]})

    await db.commit()

    return {
        "enqueued": enqueued,
        "enqueued_count": len(enqueued),
        "errors": errors,
    }
