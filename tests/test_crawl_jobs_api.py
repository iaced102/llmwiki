"""
Unit tests for app/routers/crawl_jobs.py — POST /api/crawl-jobs validation logic.

These tests run without a real DB or Redis by overriding FastAPI dependencies.
They cover:
  - HTTP 422 for non-http/https URLs (e.g. ftp://, empty string)
  - HTTP 422 when scope_type="project" and scope_id is missing
  - HTTP 403 when user lacks doc:create permission
  - Successful creation returns status="pending" and correct domain

Requirements: 1.1, 1.5, 1.6, 1.8
"""

import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.routers import crawl_jobs
from app.services.auth_service import get_current_user


# ---------------------------------------------------------------------------
# Helpers — build a minimal FastAPI app with the crawl_jobs router
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(crawl_jobs.router, prefix="/api")
    return app


def _make_admin() -> SimpleNamespace:
    """Return a plain namespace that looks like an admin Employee."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Admin User",
        email="admin@example.com",
        role="admin",
        is_active=True,
        custom_role=None,
        department_id=None,
        password_hash=None,
    )


def _make_employee_no_perms() -> SimpleNamespace:
    """Return a plain namespace that looks like an Employee with a custom role
    that has no doc:create permission."""
    # Create a mock custom role with empty permissions list
    custom_role = SimpleNamespace(
        id=uuid.uuid4(),
        name="NoPermRole",
        permissions=[],  # explicitly empty — no doc:create
        is_system=False,
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Regular User",
        email="user@example.com",
        role="employee",
        is_active=True,
        custom_role=custom_role,
        department_id=None,
        password_hash=None,
    )


def _make_crawl_job_ns(
    start_url: str = "https://example.com",
    domain: str = "example.com",
    status: str = "pending",
    scope_type: str = "workspace",
    scope_id: uuid.UUID | None = None,
    knowledge_type_id: uuid.UUID | None = None,
    job_id: str | None = "arq-job-123",
) -> SimpleNamespace:
    """Return a plain namespace that looks like a CrawlJob for DB mock returns."""
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        start_url=start_url,
        domain=domain,
        status=status,
        knowledge_type_id=knowledge_type_id,
        scope_type=scope_type,
        scope_id=scope_id,
        created_by_id=uuid.uuid4(),
        pages_discovered=0,
        pages_crawled=0,
        pages_failed=0,
        pages_skipped=0,
        error_message=None,
        job_id=job_id,
        created_at=now,
        updated_at=now,
        finished_at=None,
    )


# ---------------------------------------------------------------------------
# Async DB session mock
# ---------------------------------------------------------------------------

class _FakeDB:
    """Minimal async DB session mock for tests that don't need real DB ops."""

    def __init__(self, job=None):
        self._job = job
        self.added: list = []
        self.committed = False
        self.flushed = False

    async def flush(self):
        self.flushed = True
        # Simulate flush: apply SQLAlchemy column defaults that aren't set yet.
        # The router creates CrawlJob(...) with keyword args, but SQLAlchemy
        # column defaults (default=0, default=list, etc.) are only applied at
        # flush time. We apply them here to mimic real DB behavior.
        now = datetime.now(timezone.utc)
        for obj in self.added:
            from app.database.models import CrawlJob
            if isinstance(obj, CrawlJob):
                if obj.id is None:
                    obj.id = uuid.uuid4()
                if obj.pages_discovered is None:
                    obj.pages_discovered = 0
                if obj.pages_crawled is None:
                    obj.pages_crawled = 0
                if obj.pages_failed is None:
                    obj.pages_failed = 0
                if obj.pages_skipped is None:
                    obj.pages_skipped = 0
                if obj.visited_urls is None:
                    obj.visited_urls = []
                if obj.created_at is None:
                    obj.created_at = now
                if obj.updated_at is None:
                    obj.updated_at = now

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass  # no-op

    def add(self, obj):
        self.added.append(obj)

    async def get(self, model, pk):
        return self._job

    async def execute(self, stmt):
        result = MagicMock()
        result.scalar_one.return_value = 0
        result.scalars.return_value.all.return_value = []
        return result

    async def rollback(self):
        pass

    async def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# Shared setup: override get_current_user to return admin
# (require_permission depends on get_current_user, so this covers auth)
# ---------------------------------------------------------------------------

def _setup_admin_app(fake_db: _FakeDB) -> FastAPI:
    """Create app with admin user and fake DB overrides."""
    app = _make_app()
    admin = _make_admin()

    async def override_db() -> AsyncGenerator[_FakeDB, None]:
        yield fake_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: admin
    return app


# ---------------------------------------------------------------------------
# Tests — URL validation (Requirement 1.5)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_crawl_job_invalid_scheme_ftp():
    """HTTP 422 for ftp:// URL — not http or https."""
    app = _setup_admin_app(_FakeDB())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/crawl-jobs", json={"start_url": "ftp://example.com/path"})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_crawl_job_invalid_empty_string():
    """HTTP 422 for empty string URL."""
    app = _setup_admin_app(_FakeDB())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/crawl-jobs", json={"start_url": ""})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_crawl_job_invalid_no_hostname():
    """HTTP 422 for URL with no hostname (e.g. just a plain string)."""
    app = _setup_admin_app(_FakeDB())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/crawl-jobs", json={"start_url": "not-a-url"})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_crawl_job_invalid_javascript_scheme():
    """HTTP 422 for javascript: URL."""
    app = _setup_admin_app(_FakeDB())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/crawl-jobs", json={"start_url": "javascript:alert(1)"})

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests — scope_type/scope_id validation (Requirement 1.8)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_crawl_job_project_scope_missing_scope_id():
    """HTTP 422 when scope_type='project' and scope_id is not provided."""
    app = _setup_admin_app(_FakeDB())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/crawl-jobs",
            json={"start_url": "https://example.com", "scope_type": "project"},
        )

    assert resp.status_code == 422
    assert "scope_id" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests — permission check (Requirement 1.6)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_crawl_job_no_permission_returns_403():
    """HTTP 403 when user lacks doc:create permission.

    Override get_current_user to return an employee without doc:create.
    The require_permission dependency will then check permissions and raise 403.
    """
    app = _make_app()
    no_perm_user = _make_employee_no_perms()

    async def override_db() -> AsyncGenerator[_FakeDB, None]:
        yield _FakeDB()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: no_perm_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/crawl-jobs", json={"start_url": "https://example.com"})

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests — successful creation (Requirements 1.1, 1.2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_crawl_job_success_returns_pending_and_domain():
    """Successful creation returns status='pending' and correct domain."""
    job = _make_crawl_job_ns(
        start_url="https://example.com/path",
        domain="example.com",
        status="pending",
    )
    app = _setup_admin_app(_FakeDB(job=job))

    mock_arq_job = MagicMock()
    mock_arq_job.job_id = "arq-job-789"
    mock_pool = AsyncMock()
    mock_pool.enqueue_job = AsyncMock(return_value=mock_arq_job)

    import app.worker as worker_module
    with patch.object(worker_module, "get_arq_pool", new=AsyncMock(return_value=mock_pool)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/crawl-jobs",
                json={"start_url": "https://example.com/path"},
            )

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    assert data["domain"] == "example.com"
    assert data["start_url"] == "https://example.com/path"


@pytest.mark.asyncio
async def test_post_crawl_job_domain_excludes_port():
    """Domain stored is hostname only, without port number (Requirement 1.2)."""
    job = _make_crawl_job_ns(
        start_url="https://example.com:8080/page",
        domain="example.com",
        status="pending",
    )
    app = _setup_admin_app(_FakeDB(job=job))

    mock_arq_job = MagicMock()
    mock_arq_job.job_id = "arq-job-port"
    mock_pool = AsyncMock()
    mock_pool.enqueue_job = AsyncMock(return_value=mock_arq_job)

    import app.worker as worker_module
    with patch.object(worker_module, "get_arq_pool", new=AsyncMock(return_value=mock_pool)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/crawl-jobs",
                json={"start_url": "https://example.com:8080/page"},
            )

    assert resp.status_code == 201
    data = resp.json()
    assert data["domain"] == "example.com"
    assert ":" not in data["domain"]


@pytest.mark.asyncio
async def test_post_crawl_job_http_url_accepted():
    """Plain http:// URLs are valid (Requirement 1.1)."""
    job = _make_crawl_job_ns(
        start_url="http://example.com",
        domain="example.com",
        status="pending",
    )
    app = _setup_admin_app(_FakeDB(job=job))

    mock_arq_job = MagicMock()
    mock_arq_job.job_id = "arq-job-http"
    mock_pool = AsyncMock()
    mock_pool.enqueue_job = AsyncMock(return_value=mock_arq_job)

    import app.worker as worker_module
    with patch.object(worker_module, "get_arq_pool", new=AsyncMock(return_value=mock_pool)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/crawl-jobs",
                json={"start_url": "http://example.com"},
            )

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"


# ---------------------------------------------------------------------------
# Tests — ARQ enqueue failure (Requirement 1.4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_crawl_job_arq_failure_returns_500():
    """HTTP 500 when ARQ enqueue fails; job status set to 'failed' (Requirement 1.4)."""
    job = _make_crawl_job_ns(status="pending")
    app = _setup_admin_app(_FakeDB(job=job))

    import app.worker as worker_module
    with patch.object(worker_module, "get_arq_pool", new=AsyncMock(side_effect=RuntimeError("Redis down"))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/crawl-jobs",
                json={"start_url": "https://example.com"},
            )

    assert resp.status_code == 500
