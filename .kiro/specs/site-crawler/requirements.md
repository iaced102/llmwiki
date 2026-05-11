# Requirements Document

## Introduction

Tính năng **Site Crawler** cho phép Admin nhập một URL gốc và hệ thống tự động crawl toàn bộ các trang cùng domain bằng thư viện **crawl4ai** (sử dụng Playwright). Mỗi trang được render bằng Playwright và export sang PDF, sau đó được lưu thành một **Source record** trong database, upload lên MinIO, và enqueue vào pipeline ingest hiện có (`ingest_file_task`). Admin có thể theo dõi tiến độ crawl job theo thời gian thực trên Frontend.

Tính năng này tích hợp vào hệ thống Arkon Enterprise AI Knowledge Base, tận dụng toàn bộ pipeline xử lý tài liệu hiện có (extract text, extract images, Vision AI caption, wiki agent compile).

## Glossary

- **Crawler**: Thành phần hệ thống thực hiện BFS crawl các trang web cùng domain.
- **CrawlJob**: Bản ghi trong bảng `crawl_jobs` đại diện cho một phiên crawl, lưu trạng thái và tiến độ.
- **CrawlTask**: ARQ background task (`crawl_site_task`) thực thi logic crawl trong worker.
- **StartURL**: URL gốc do Admin nhập, xác định domain cần crawl.
- **Domain**: Hostname (không bao gồm port) được trích xuất từ StartURL; chỉ các URL cùng hostname mới được crawl.
- **VisitedURLs**: Danh sách các URL đã được xử lý, lưu trong `crawl_jobs.visited_urls` để dedup.
- **PagePDF**: File PDF (bytes) được Playwright export từ một trang web đã render.
- **Source**: Bản ghi trong bảng `sources` đại diện cho một tài liệu trong Knowledge Base.
- **IngestPipeline**: Pipeline xử lý tài liệu hiện có: extract text → extract images → Vision AI caption → wiki agent compile.
- **ARQWorker**: Background worker sử dụng ARQ (Redis queue) để xử lý các task bất đồng bộ.
- **MinIO**: Object storage lưu trữ file PDF của các trang đã crawl.
- **BFS**: Breadth-First Search — thuật toán duyệt đồ thị theo chiều rộng, dùng để khám phá các trang web.
- **ProgressTracker**: Class hiện có trong `worker.py` cập nhật tiến độ của Source vào database.

---

## Requirements

### Requirement 1: Khởi tạo Crawl Job

**User Story:** As an Admin, I want to submit a root URL to start a site crawl, so that the system can automatically discover and ingest all pages from that domain into the Knowledge Base.

#### Acceptance Criteria

1. WHEN an Admin submits a valid HTTP or HTTPS URL via the API endpoint `POST /api/crawl-jobs`, THE CrawlJob API SHALL create a new `CrawlJob` record in the database with status `pending`. A valid URL is one that has scheme `http` or `https` and a non-empty hostname.
2. WHEN a `CrawlJob` record is created, THE CrawlJob API SHALL extract the hostname (excluding port) from the StartURL and store it in `crawl_jobs.domain`.
3. WHEN a `CrawlJob` record is created, THE CrawlJob API SHALL enqueue a `crawl_site_task` ARQ job and store the returned job ID in `crawl_jobs.job_id`.
4. IF the ARQ enqueue call fails, THEN THE CrawlJob API SHALL set the `CrawlJob` status to `failed`, store the error message in `crawl_jobs.error_message`, and return HTTP 500 to the caller.
5. IF an Admin submits a URL that is not a valid HTTP or HTTPS URL, THEN THE CrawlJob API SHALL return HTTP 422 with a validation error message without performing any permission check.
6. IF an Admin submits a valid URL without the `doc:create` permission, THEN THE CrawlJob API SHALL return HTTP 403.
7. THE CrawlJob API SHALL accept optional parameters: `knowledge_type_id` (UUID), `scope_type` (one of `"global"` or `"project"`), `scope_id` (UUID, required when `scope_type` is `"project"`) to be applied to all Source records created during the crawl.
8. IF `scope_type` is `"project"` and `scope_id` is not provided, THEN THE CrawlJob API SHALL return HTTP 422 with a validation error message.

---

### Requirement 2: BFS Crawl và Khám Phá Trang

**User Story:** As an Admin, I want the system to automatically discover all pages on the target domain using BFS, so that no pages are missed during the crawl.

#### Acceptance Criteria

1. WHEN the `crawl_site_task` starts, THE Crawler SHALL check that the `CrawlJob` status is `pending`; IF the status is not `pending`, THE Crawler SHALL terminate the task without making any changes.
2. WHEN the `crawl_site_task` confirms the `CrawlJob` is `pending`, THE Crawler SHALL update the `CrawlJob` status to `running`.
3. WHEN the `crawl_site_task` processes a page, THE Crawler SHALL extract all hyperlinks from `<a href>` attributes in the rendered HTML, considering only links with scheme `http` or `https` (excluding `javascript:`, `mailto:`, `tel:`, and fragment-only links).
4. WHEN a discovered link has the same hostname as the Domain, THE Crawler SHALL add it to the BFS queue if it has not been visited and is not already in the queue.
5. WHEN a discovered link has a different hostname than the Domain, THE Crawler SHALL discard the link without adding it to the BFS queue.
6. WHEN a URL is added to the BFS queue or marked as visited, THE Crawler SHALL normalize the URL by: lowercasing the scheme and hostname, removing query strings, removing fragments, and normalizing trailing slashes (treating `/path` and `/path/` as equivalent).
7. WHEN a URL is dequeued from the BFS queue for processing, THE Crawler SHALL check `crawl_jobs.visited_urls` at that point; IF the URL is already present, THE Crawler SHALL skip it and increment `crawl_jobs.pages_skipped`.
8. WHEN a new URL is about to be crawled, THE Crawler SHALL add it to `crawl_jobs.visited_urls` before fetching to prevent duplicate processing.
9. WHEN the BFS queue is exhausted, THE Crawler SHALL mark the `CrawlJob` status as `completed`.

---

### Requirement 3: Render Trang và Export PDF

**User Story:** As an Admin, I want each discovered page to be rendered with JavaScript and exported as a PDF, so that the full visual content including dynamic elements is captured.

#### Acceptance Criteria

1. WHEN the Crawler processes a page URL, THE Crawler SHALL use crawl4ai with Playwright to render the full page including JavaScript-generated content, with a render timeout of 30 seconds per page.
2. WHEN a page has been rendered successfully, THE Crawler SHALL export the rendered page to PDF bytes using Playwright's PDF export capability, with an export timeout of 30 seconds.
3. WHEN a page returns HTTP status 401 or 403, THE Crawler SHALL skip the page, increment `crawl_jobs.pages_skipped`, and continue crawling remaining pages.
4. WHEN a page returns any other HTTP error (4xx or 5xx excluding 401/403), THE Crawler SHALL skip the page, increment `crawl_jobs.pages_failed`, and continue crawling remaining pages.
5. WHEN a network timeout or connection error occurs while fetching or rendering a page, THE Crawler SHALL retry up to 2 additional times before giving up; IF all retries fail, THE Crawler SHALL increment `crawl_jobs.pages_failed` and continue crawling remaining pages.
6. WHEN a page is successfully exported to PDF, THE Crawler SHALL increment `crawl_jobs.pages_crawled` exactly once regardless of how many fetch attempts were made.

---

### Requirement 4: Tạo Source Record và Upload lên MinIO

**User Story:** As an Admin, I want each crawled page PDF to be stored as a Source record in the Knowledge Base, so that it can be processed by the existing ingestion pipeline.

#### Acceptance Criteria

1. WHEN a PagePDF is successfully generated, THE Crawler SHALL create a new `Source` record in the database with `source_type = "file"`, `status = "pending"`, and `file_name` derived from the page URL by: lowercasing, replacing all non-alphanumeric characters with hyphens, collapsing consecutive hyphens, stripping leading/trailing hyphens, and appending `.pdf`; the resulting filename SHALL be truncated to 200 characters before the `.pdf` extension.
2. WHEN a `Source` record is created for a crawled page, THE Crawler SHALL set `title` to the page's `<title>` tag content truncated to 500 characters, or fall back to the page URL if no title is found.
3. WHEN a `Source` record is created, THE Crawler SHALL apply `knowledge_type_id`, `scope_type`, and `scope_id` from the parent `CrawlJob` to the new Source record.
4. WHEN a `Source` record is created, THE Crawler SHALL upload the PagePDF bytes to MinIO at path `sources/<source_id>/original/<slug>.pdf` and store the key in `sources.minio_key`; IF the MinIO upload fails, THE Crawler SHALL set the Source status to `"error"` and SHALL NOT enqueue `ingest_file_task`.
5. WHEN the PDF is uploaded to MinIO successfully, THE Crawler SHALL enqueue an `ingest_file_task` ARQ job for the new Source record and store the returned job ID in `sources.job_id`; IF the enqueue fails, THE Crawler SHALL set the Source status to `"error"` with a descriptive error message.
6. WHEN a `Source` record is created for a crawled page, THE Crawler SHALL set `contributed_by_employee_id` to the `created_by_id` of the parent `CrawlJob`.
7. WHEN the Crawler is about to create a `Source` record for a URL that already has a `Source` record with the same `url` within the same `CrawlJob`, THE Crawler SHALL skip creation, increment `crawl_jobs.pages_skipped`, and not enqueue a duplicate `ingest_file_task`.

---

### Requirement 5: Theo Dõi Tiến Độ Crawl Job

**User Story:** As an Admin, I want to monitor the real-time progress of a crawl job, so that I can see how many pages have been discovered, crawled, failed, and skipped.

#### Acceptance Criteria

1. THE CrawlJob API SHALL expose a `GET /api/crawl-jobs/{job_id}` endpoint that returns the current state of a `CrawlJob` record; IF the `job_id` does not exist, THE API SHALL return HTTP 404.
2. WHEN the Crawler discovers a new page URL (adds it to the BFS queue), THE Crawler SHALL increment `crawl_jobs.pages_discovered` by 1.
3. WHEN the Crawler successfully exports a page to PDF and creates a Source record, THE Crawler SHALL increment `crawl_jobs.pages_crawled` by 1.
4. IF the Crawler skips a page due to HTTP 401 or 403, THEN THE Crawler SHALL increment `crawl_jobs.pages_skipped` by 1.
5. IF the Crawler fails to process a page due to any error other than HTTP 401 or 403 (including HTTP 4xx/5xx, timeout, and connection errors after all retries), THEN THE Crawler SHALL increment `crawl_jobs.pages_failed` by 1.
6. THE CrawlJob API SHALL expose a `GET /api/crawl-jobs` endpoint that returns a paginated list of all `CrawlJob` records visible to the requesting user, ordered by `created_at` descending, with `page_size` between 1 and 100 (default 20).
7. WHEN a `CrawlJob` reaches status `completed` or `failed`, THE Crawler SHALL set `crawl_jobs.finished_at` to the current UTC timestamp.

---

### Requirement 6: Xử Lý Lỗi và Trạng Thái Thất Bại

**User Story:** As an Admin, I want the system to handle unexpected errors gracefully and report the final status of a crawl job, so that I can diagnose and retry failed crawls.

#### Acceptance Criteria

1. IF an unhandled exception occurs during the `crawl_site_task`, THEN THE Crawler SHALL set `crawl_jobs.status` to `failed`, store the error message (truncated to 500 characters) in `crawl_jobs.error_message`, and set `crawl_jobs.finished_at` to the current UTC timestamp.
2. IF the `crawl_site_task` cannot find the `CrawlJob` record in the database, THEN THE Crawler SHALL raise a `ValueError` with the message `"CrawlJob <id> not found"` and terminate the task without modifying any database records.
3. WHEN a `CrawlJob` has status `failed` and `error_message` is not null, THE CrawlJob API SHALL include the `error_message` field in the response.
4. WHEN a `CrawlJob` has status `failed` and `error_message` is null, THE CrawlJob API SHALL return the response successfully with `error_message` as `null`.
5. IF an Admin sends `POST /api/crawl-jobs/{job_id}/cancel` for a `CrawlJob` in `pending` or `running` state, THEN THE CrawlJob API SHALL set `crawl_jobs.status` to `cancelled` and return HTTP 200.
6. IF an Admin sends `POST /api/crawl-jobs/{job_id}/cancel` for a `CrawlJob` in `completed`, `failed`, or `cancelled` state, THEN THE CrawlJob API SHALL return HTTP 400 with the message `"Cannot cancel a job with status '<current_status>'"`.
7. IF the `job_id` in a cancel request does not exist, THEN THE CrawlJob API SHALL return HTTP 404.

---

### Requirement 7: Giao Diện Frontend — Khởi Tạo Crawl

**User Story:** As an Admin, I want a UI form to submit a site crawl job, so that I can easily start crawling a website without using the API directly.

#### Acceptance Criteria

1. THE Frontend SHALL provide a "Crawl Website" form component within the Knowledge Base management section (Sources page) containing: a URL text input field, an optional Knowledge Type dropdown, and a Submit button.
2. WHEN an Admin enters a URL and submits the form, THE Frontend SHALL call `POST /api/crawl-jobs` and display a loading spinner on the Submit button during the request.
3. WHEN the API call fails due to a network error, THE Frontend SHALL display the message "Something went wrong. Please try again." and re-enable the Submit button.
4. WHEN the API returns HTTP 200/201, THE Frontend SHALL navigate to the crawl job detail/progress view for the newly created job.
5. WHEN the API returns HTTP 422, THE Frontend SHALL display the validation error message inline below the URL input field.
6. THE Frontend SHALL enable the Submit button as soon as the URL input field contains any non-empty text; the Submit button SHALL be disabled when the input is empty or when a submission is in progress.
7. THE Frontend SHALL provide a Knowledge Type dropdown populated from `GET /api/knowledge-types`, consistent with the existing source upload form pattern; the dropdown SHALL be optional (no selection = no knowledge type assigned).

---

### Requirement 8: Giao Diện Frontend — Theo Dõi Tiến Độ

**User Story:** As an Admin, I want to see real-time progress of a crawl job in the UI, so that I know how the crawl is progressing without refreshing the page.

#### Acceptance Criteria

1. THE Frontend SHALL display a crawl job detail view showing: status badge, `pages_discovered`, `pages_crawled`, `pages_failed`, `pages_skipped` counters, elapsed time (calculated from `created_at`), and `start_url`.
2. WHILE a `CrawlJob` has status `running` or `pending`, THE Frontend SHALL poll `GET /api/crawl-jobs/{job_id}` every 3 seconds to refresh the displayed counters.
3. WHEN a `CrawlJob` reaches status `completed`, THE Frontend SHALL stop polling and display a completion summary showing total pages crawled and a link to the Sources list filtered by this crawl job.
4. WHEN a `CrawlJob` reaches status `failed`, THE Frontend SHALL stop polling and display the `error_message` in a visible error banner.
5. THE Frontend SHALL display a list of recent crawl jobs in the Knowledge Base management section, showing: `start_url`, status badge (using Sahara design system colors — `running`: sienna `#c2652a`, `completed`: green, `failed`: red, `cancelled`: gray), `pages_crawled`, and `created_at`.
6. WHEN a `CrawlJob` has status `running` or `pending`, THE Frontend SHALL display a Cancel button that calls `POST /api/crawl-jobs/{job_id}/cancel`; WHEN the cancel request succeeds, THE Frontend SHALL update the status badge to `cancelled` and hide the Cancel button.
