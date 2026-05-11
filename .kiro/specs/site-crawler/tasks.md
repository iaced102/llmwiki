# Implementation Plan: Site Crawler

## Overview

Implement the Site Crawler feature end-to-end: database model, ARQ background task (`crawl_site_task`) with BFS crawl logic using crawl4ai/Playwright, REST API endpoints for job management, and React frontend components for job submission and progress monitoring. The implementation builds on the existing ARQ worker, MinIO storage, and `ingest_file_task` pipeline.

## Tasks

- [x] 1. Add `CrawlJob` ORM model and register the ARQ task skeleton
  - Add `CrawlJob` SQLAlchemy model to `app/database/models.py` mapping all columns from migration `020_crawl_jobs.py` (`id`, `start_url`, `domain`, `status`, `knowledge_type_id`, `scope_type`, `scope_id`, `created_by_id`, `pages_discovered`, `pages_crawled`, `pages_failed`, `pages_skipped`, `visited_urls`, `error_message`, `job_id`, `created_at`, `updated_at`, `finished_at`)
  - Add a stub `crawl_site_task(ctx, crawl_job_id: str)` function in `app/worker.py` that raises `NotImplementedError`
  - Register `crawl_site_task` in `WorkerSettings.functions` list
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2_

- [x] 2. Implement the `POST /api/crawl-jobs` endpoint
  - [x] 2.1 Create `app/routers/crawl_jobs.py` with Pydantic request/response schemas (`CrawlJobCreate`, `CrawlJobResponse`) and the `POST /crawl-jobs` route
    - Validate URL scheme is `http` or `https` and hostname is non-empty; return HTTP 422 on invalid URL
    - Require `doc:create` permission; return HTTP 403 if missing
    - Validate `scope_type`/`scope_id` combination: if `scope_type="project"` and `scope_id` is absent, return HTTP 422
    - Create `CrawlJob` record with `status="pending"`, extract and store `domain` (hostname only, no port)
    - Enqueue `crawl_site_task` via ARQ pool; store returned `job_id`; on enqueue failure set status `"failed"`, store error message, return HTTP 500
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8_

  - [x]* 2.2 Write unit tests for `POST /api/crawl-jobs` validation logic
    - Test HTTP 422 for non-http/https URLs (e.g. `ftp://`, empty string)
    - Test HTTP 422 when `scope_type="project"` and `scope_id` is missing
    - Test HTTP 403 when user lacks `doc:create` permission
    - Test successful creation returns `status="pending"` and correct `domain`
    - _Requirements: 1.1, 1.5, 1.6, 1.8_

- [x] 3. Implement `GET /api/crawl-jobs` and `GET /api/crawl-jobs/{job_id}` endpoints
  - [x] 3.1 Add list endpoint `GET /crawl-jobs` in `app/routers/crawl_jobs.py`
    - Return paginated list ordered by `created_at` descending; enforce `page_size` between 1 and 100 (default 20)
    - _Requirements: 5.6_

  - [x] 3.2 Add detail endpoint `GET /crawl-jobs/{job_id}` in `app/routers/crawl_jobs.py`
    - Return HTTP 404 if `job_id` does not exist
    - Include `error_message` field (may be `null`) in response
    - _Requirements: 5.1, 6.3, 6.4_

- [x] 4. Implement `POST /api/crawl-jobs/{job_id}/cancel` endpoint
  - Add cancel route in `app/routers/crawl_jobs.py`
  - Set status to `"cancelled"` and return HTTP 200 when current status is `"pending"` or `"running"`
  - Return HTTP 400 with message `"Cannot cancel a job with status '<current_status>'"` for terminal states
  - Return HTTP 404 if `job_id` does not exist
  - _Requirements: 6.5, 6.6, 6.7_

- [x] 5. Register the crawl-jobs router in `app/main.py`
  - Import `crawl_jobs` router and add `app.include_router(crawl_jobs.router, prefix="/api", tags=["crawl-jobs"])`
  - _Requirements: 1.1, 5.1, 5.6_

- [x] 6. Checkpoint — Ensure all API tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement URL normalization and BFS helpers
  - [x] 7.1 Create `app/services/crawler_utils.py` with URL normalization function
    - Lowercase scheme and hostname, strip query strings and fragments, normalize trailing slashes (treat `/path` and `/path/` as equivalent)
    - _Requirements: 2.6_

  - [x]* 7.2 Write property tests for URL normalization
    - **Property 1: Idempotency** — normalizing an already-normalized URL returns the same URL
    - **Validates: Requirements 2.6**
    - **Property 2: Trailing slash equivalence** — `normalize(url + "/") == normalize(url)` for path-only URLs
    - **Validates: Requirements 2.6**

  - [x] 7.3 Implement `extract_same_domain_links(html: str, base_domain: str, base_url: str) -> list[str]` in `app/services/crawler_utils.py`
    - Parse `<a href>` attributes, keep only `http`/`https` links, discard `javascript:`, `mailto:`, `tel:`, fragment-only links
    - Filter to links whose hostname matches `base_domain`; resolve relative URLs against `base_url`
    - Return normalized URLs
    - _Requirements: 2.3, 2.4, 2.5, 2.6_

  - [x]* 7.4 Write unit tests for `extract_same_domain_links`
    - Test cross-domain links are discarded
    - Test `javascript:`, `mailto:`, `tel:`, fragment-only links are discarded
    - Test relative URLs are resolved correctly
    - _Requirements: 2.3, 2.4, 2.5_

- [x] 8. Implement `generate_file_name_from_url(url: str) -> str` helper in `app/services/crawler_utils.py`
  - Lowercase, replace all non-alphanumeric characters with hyphens, collapse consecutive hyphens, strip leading/trailing hyphens, append `.pdf`; truncate slug to 200 characters before appending `.pdf`
  - _Requirements: 4.1_

- [x] 9. Implement the `crawl_site_task` ARQ background task
  - [x] 9.1 Implement status guard and transition in `crawl_site_task`
    - Load `CrawlJob` by ID; raise `ValueError("CrawlJob <id> not found")` if missing
    - If status is not `"pending"`, terminate without changes
    - Set status to `"running"` and commit
    - _Requirements: 2.1, 2.2, 6.2_

  - [x] 9.2 Implement BFS crawl loop
    - Initialize queue with normalized `start_url`; add to `visited_urls`
    - Dequeue URL; skip and increment `pages_skipped` if already in `visited_urls`
    - Use crawl4ai with Playwright to render page (30 s timeout); retry up to 2 additional times on network/timeout errors
    - On HTTP 401/403: increment `pages_skipped`, continue
    - On other HTTP 4xx/5xx: increment `pages_failed`, continue
    - On all retries exhausted: increment `pages_failed`, continue
    - After successful render: export PDF bytes (30 s timeout)
    - Increment `pages_crawled` on successful PDF export
    - Extract links via `extract_same_domain_links`; for each new link increment `pages_discovered` and add to queue
    - Persist `visited_urls` and counters to DB after each page
    - _Requirements: 2.3, 2.4, 2.5, 2.7, 2.8, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 5.2, 5.3, 5.4, 5.5_

  - [x] 9.3 Implement Source record creation and MinIO upload per crawled page
    - Skip creation if a `Source` with the same `url` already exists for this `CrawlJob`; increment `pages_skipped`
    - Create `Source` with `source_type="file"`, `status="pending"`, `file_name` from `generate_file_name_from_url`, `title` from `<title>` tag (truncated to 500 chars) or fallback to URL, `contributed_by_employee_id` from `CrawlJob.created_by_id`, and `knowledge_type_id`/`scope_type`/`scope_id` from parent `CrawlJob`
    - Upload PDF bytes to MinIO at `sources/<source_id>/original/<slug>.pdf`; on failure set Source status `"error"`, do NOT enqueue `ingest_file_task`
    - On successful upload, enqueue `ingest_file_task`; on enqueue failure set Source status `"error"` with descriptive message
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [x] 9.4 Implement BFS completion and error handling
    - When BFS queue is exhausted, set `CrawlJob.status = "completed"` and `finished_at = utcnow()`
    - Wrap entire task body in `try/except BaseException`: on unhandled exception set status `"failed"`, store truncated (500 chars) error message, set `finished_at`
    - _Requirements: 2.9, 5.7, 6.1_

- [x] 10. Checkpoint — Ensure all backend tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement Frontend — Crawl Website form component
  - [x] 11.1 Create `CrawlWebsiteForm` React component in the Sources page section
    - URL text input field, optional Knowledge Type dropdown (populated from `GET /api/knowledge-types`, consistent with existing source upload form), and Submit button
    - Submit button disabled when input is empty or submission is in progress; enabled as soon as any non-empty text is present
    - On submit: call `POST /api/crawl-jobs`, show loading spinner on button
    - On HTTP 200/201: navigate to crawl job detail/progress view for the new job
    - On HTTP 422: display validation error inline below URL input
    - On network error: display "Something went wrong. Please try again." and re-enable button
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [ ]* 11.2 Write unit tests for `CrawlWebsiteForm`
    - Test Submit button is disabled when URL input is empty
    - Test Submit button is disabled during in-progress submission
    - Test validation error renders below URL input on HTTP 422
    - Test navigation occurs on HTTP 201
    - _Requirements: 7.2, 7.4, 7.5, 7.6_

- [x] 12. Implement Frontend — Crawl Job detail/progress view
  - [x] 12.1 Create `CrawlJobDetail` React component
    - Display: status badge (sienna `#c2652a` for `running`, green for `completed`, red for `failed`, gray for `cancelled`), `pages_discovered`, `pages_crawled`, `pages_failed`, `pages_skipped` counters, elapsed time from `created_at`, and `start_url`
    - Poll `GET /api/crawl-jobs/{job_id}` every 3 seconds while status is `running` or `pending`; stop polling on `completed` or `failed`
    - On `completed`: show completion summary with total pages crawled and link to Sources list filtered by this crawl job
    - On `failed`: show `error_message` in a visible error banner
    - Show Cancel button when status is `running` or `pending`; call `POST /api/crawl-jobs/{job_id}/cancel` on click; update badge to `cancelled` and hide button on success
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.6_

- [x] 13. Implement Frontend — Recent crawl jobs list
  - Add recent crawl jobs list in the Knowledge Base management section showing `start_url`, status badge (Sahara design system colors), `pages_crawled`, and `created_at`
  - Fetch from `GET /api/crawl-jobs`
  - _Requirements: 8.5_

- [x] 14. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- The `crawl_site_task` must be added to `WorkerSettings.functions` so the ARQ worker picks it up
- crawl4ai must be added to project dependencies (`requirements.txt` / `pyproject.toml`) before task 9
- The `CrawlJob` ORM model must be imported in `app/database/__init__.py` or wherever models are collected so Alembic and SQLAlchemy see it

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "3.2", "4", "7.1", "7.3", "8"] },
    { "id": 2, "tasks": ["2.2", "5", "7.2", "7.4"] },
    { "id": 3, "tasks": ["9.1", "11.1"] },
    { "id": 4, "tasks": ["9.2", "11.2"] },
    { "id": 5, "tasks": ["9.3"] },
    { "id": 6, "tasks": ["9.4", "12.1"] },
    { "id": 7, "tasks": ["13"] }
  ]
}
```
