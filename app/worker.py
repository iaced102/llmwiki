"""
arq Worker — async Redis queue for document ingestion.

The worker now compiles each source into the LLM Wiki (markdown pages stored
in PostgreSQL) instead of producing chunk embeddings. See app/ai/wiki_compiler.py.

Start with:
    arq app.worker.WorkerSettings
"""

import asyncio
import uuid
import zipfile
from typing import Optional

from arq import cron
from arq.connections import ArqRedis, RedisSettings, create_pool
from loguru import logger
from sqlalchemy import select

from app.config import settings


def _get_redis_settings() -> RedisSettings:
    return RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        database=settings.redis_db,
        password=settings.redis_password or None,
    )


# arq Redis pool (lazy init)
_arq_pool: Optional[ArqRedis] = None


async def get_arq_pool() -> ArqRedis:
    """Lazy-init arq Redis connection pool."""
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(_get_redis_settings())
    return _arq_pool


# ---------------------------------------------------------------------------
# Progress helper
# ---------------------------------------------------------------------------

class ProgressTracker:
    """Updates source.progress + source.progress_message in DB."""

    def __init__(self, source_id: uuid.UUID):
        self.source_id = source_id

    async def update(self, progress: int, message: str):
        from app.database import async_session_factory
        from app.database.models import Source
        async with async_session_factory() as session:
            source = await session.get(Source, self.source_id)
            if source:
                source.progress = progress
                source.progress_message = message
                await session.commit()
        logger.debug(f"[{self.source_id}] Progress: {progress}% — {message}")


# ---------------------------------------------------------------------------
# Ingestion tasks
# ---------------------------------------------------------------------------

async def ingest_file_task(ctx: dict, source_id: str):
    """
    arq task: full file ingestion → wiki compilation.
    Steps: download from MinIO → extract text → vision captions → outline → compile wiki.
    File must already be uploaded to MinIO before this task is enqueued.
    """
    from app.ai.registry import ProviderRegistry
    from app.ai.wiki_agent import compile_source_with_agent
    from app.database import async_session_factory
    from app.database.models import KnowledgeType, Source, SourceImage
    from app.services.image_service import extract_images
    from app.services.kb_service import (
        _extract_text_from_file,
        _inline_image_markers,
    )
    from app.services.source_outline import assemble_full_text, build_outline
    from app.services.storage_service import storage_service

    sid = uuid.UUID(source_id)
    tracker = ProgressTracker(sid)

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source:
            raise ValueError(f"Source {source_id} not found")
        if not source.minio_key:
            raise ValueError(f"Source {source_id} has no file in storage")

        file_name = source.file_name or source.minio_key.split("/")[-1]

        try:
            source.status = "processing"
            source.progress = 0
            source.progress_message = "Starting processing..."
            await session.commit()

            # --- Step 1: Download from MinIO (10%) ---
            await tracker.update(5, "Loading file...")
            file_data = storage_service.download_file(source.minio_key)
            await tracker.update(10, "File loaded")

            # --- Step 2: Extract text per page (25%) ---
            await tracker.update(15, "Extracting text (per page)...")
            pages_data = await _extract_text_from_file(file_data, file_name)

            if not pages_data or not any((p.get("content") or "").strip() for p in pages_data):
                source.status = "error"
                source.error_message = "Unable to extract text content"
                source.progress = 0
                await session.commit()
                return {"status": "error", "message": "No text content"}

            await tracker.update(25, "Text extraction complete")

            # --- Step 3: Extract images + vision captions (40%) ---
            await tracker.update(30, "Extracting and analyzing images...")
            images = extract_images(file_data, file_name, source_id)

            registry = ProviderRegistry(session)
            vision_provider = await registry.get_vision()
            if vision_provider and images:
                # Build a lookup of page text by page number so we can give
                # the vision model surrounding context when captioning.
                page_text_by_num: dict[int, str] = {
                    p.get("page_number") or 1: (p.get("content") or "")[:1500]
                    for p in pages_data
                }
                for idx, img in enumerate(images, 1):
                    try:
                        if idx % 5 == 0 or idx == 1 or idx == len(images):
                            logger.info(f"Vision AI analyzing image {idx}/{len(images)}...")
                        img_bytes = storage_service.download_file(img.minio_key)
                        page_ctx = page_text_by_num.get(img.page_number or 1, "")
                        vision_prompt = (
                            "Describe this image concisely in 1-3 sentences. "
                            "Focus on what is shown (diagrams, charts, photos, illustrations) "
                            "and what information it conveys. Be specific — mention key elements, "
                            "labels, numbers, or steps visible in the image. Do not start with "
                            "'Based on the image' or similar filler phrases.\n\n"
                            + (f"Page context:\n{page_ctx}" if page_ctx else "")
                        )
                        img.caption = await vision_provider.analyze_image(
                            img_bytes, img.content_type, prompt=vision_prompt
                        )
                    except Exception as e:
                        logger.warning(f"Failed to analyze image {img.minio_key}: {e}")
            elif images:
                logger.info("No vision provider configured, skipping image captioning")

            # Persist images so wiki content_md can reference them by uuid.
            for img in images:
                row = SourceImage(
                    source_id=uuid.UUID(source_id),
                    minio_key=img.minio_key,
                    page_number=img.page_number,
                    image_index=img.image_index,
                    caption=img.caption,
                    content_type=img.content_type,
                    size_bytes=img.size_bytes,
                )
                session.add(row)
                await session.flush()
                img.image_id = str(row.id)

            # Inline image markers into per-page text so the compiler sees them.
            _inline_image_markers(pages_data, images)
            await tracker.update(40, f"Analyzed {len(images)} images")

            # --- Step 4: Build outline + assemble full_text (50%) ---
            await tracker.update(45, "Building document outline...")
            source.outline_json = build_outline(pages_data)
            full_text, page_offsets = assemble_full_text(pages_data)
            source.full_text = full_text
            source.page_offsets = page_offsets
            await session.commit()
            await tracker.update(50, f"Outline: {len(source.outline_json or [])} top-level sections")

            # --- Step 5: Resolve KnowledgeType context (52%) ---
            kt_slug = kt_name = kt_desc = None
            if source.knowledge_type_id:
                kt = await session.get(KnowledgeType, source.knowledge_type_id)
                if kt:
                    kt_slug, kt_name, kt_desc = kt.slug, kt.name, kt.description

            # --- Step 6: Compile into wiki via mini-agent (55-95%) ---
            await tracker.update(55, "Compiling into wiki (agent)...")

            async def emit(step: int, message: str) -> None:
                progress = min(95, 55 + step)
                await tracker.update(progress, f"Compiling: {message}")

            result = await compile_source_with_agent(
                session=session,
                source=source,
                full_text=full_text,
                kt_slug=kt_slug,
                kt_name=kt_name,
                kt_desc=kt_desc,
                on_progress=emit,
            )
            await session.commit()
            await tracker.update(
                95,
                f"Wiki: +{result['pages_created']} pages, ~{result['pages_updated']} updated "
                f"({result['tool_calls']} tool calls)",
            )

            # --- Done (100%) ---
            source.status = "ready"
            source.progress = 100
            source.progress_message = "Done"
            source.error_message = None
            await session.commit()

            logger.success(
                f"Source {source_id} ingested: {len(images)} images, "
                f"+{result['pages_created']} pages, ~{result['pages_updated']} updated"
            )
            return {
                "status": "ready",
                "images": len(images),
                "pages_created": result["pages_created"],
                "pages_updated": result["pages_updated"],
            }

        except BaseException as e:
            logger.error(f"Ingestion failed for {source_id}: {e}")
            error_msg = str(e)[:500]
            progress_msg = f"Error: {str(e)[:200]}"

            async def _mark_error_file() -> None:
                from app.database import async_session_factory as _sf
                from app.database.models import Source as _Source
                async with _sf() as err_session:
                    src = await err_session.get(_Source, sid)
                    if src:
                        src.status = "error"
                        src.error_message = error_msg
                        src.progress = 0
                        src.progress_message = progress_msg
                        await err_session.commit()

            try:
                await asyncio.shield(_mark_error_file())
            except Exception:
                pass
            raise


async def ingest_url_task(ctx: dict, source_id: str):
    """arq task: URL ingestion → wiki compilation."""
    from app.ai.wiki_agent import compile_source_with_agent
    from app.database import async_session_factory
    from app.database.models import KnowledgeType, Source
    from app.services.kb_service import _extract_text_from_url
    from app.services.source_outline import assemble_full_text, build_outline

    sid = uuid.UUID(source_id)
    tracker = ProgressTracker(sid)

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source:
            raise ValueError(f"Source {source_id} not found")

        try:
            source.status = "processing"
            source.progress = 0
            await session.commit()

            await tracker.update(15, "Fetching content from URL...")
            if not source.url:
                source.status = "error"
                source.error_message = "Source has no URL"
                await session.commit()
                return {"status": "error"}
            pages_data = await _extract_text_from_url(source.url)

            if not pages_data or not any((p.get("content") or "").strip() for p in pages_data):
                source.status = "error"
                source.error_message = "Unable to fetch content from URL"
                await session.commit()
                return {"status": "error"}

            await tracker.update(40, "Building outline...")
            source.outline_json = build_outline(pages_data)
            full_text, page_offsets = assemble_full_text(pages_data)
            source.full_text = full_text
            source.page_offsets = page_offsets
            await session.commit()

            kt_slug = kt_name = kt_desc = None
            if source.knowledge_type_id:
                kt = await session.get(KnowledgeType, source.knowledge_type_id)
                if kt:
                    kt_slug, kt_name, kt_desc = kt.slug, kt.name, kt.description

            await tracker.update(55, "Compiling into wiki (agent)...")

            async def emit(step: int, message: str) -> None:
                progress = min(95, 55 + step)
                await tracker.update(progress, f"Compiling: {message}")

            result = await compile_source_with_agent(
                session=session,
                source=source,
                full_text=full_text,
                kt_slug=kt_slug,
                kt_name=kt_name,
                kt_desc=kt_desc,
                on_progress=emit,
            )
            await session.commit()

            source.status = "ready"
            source.progress = 100
            source.progress_message = "Done"
            source.error_message = None
            await session.commit()

            logger.success(
                f"URL source {source_id} ingested: "
                f"+{result['pages_created']} pages, ~{result['pages_updated']} updated"
            )
            return {
                "status": "ready",
                "pages_created": result["pages_created"],
                "pages_updated": result["pages_updated"],
            }

        except BaseException as e:
            logger.error(f"URL ingestion failed for {source_id}: {e}")
            error_msg = str(e)[:500]

            async def _mark_error_url() -> None:
                from app.database import async_session_factory as _sf
                from app.database.models import Source as _Source
                async with _sf() as err_session:
                    src = await err_session.get(_Source, sid)
                    if src:
                        src.status = "error"
                        src.error_message = error_msg
                        src.progress = 0
                        await err_session.commit()

            try:
                await asyncio.shield(_mark_error_url())
            except Exception:
                pass
            raise


# ---------------------------------------------------------------------------
# Worker configuration
# ---------------------------------------------------------------------------


async def ingest_skill_task(ctx: dict, skill_id: str, version_id: str, file_path: str, file_name: str):
    """
    arq task: unzip skill package from disk buffer, store in MinIO, and extract metadata.
    """
    import os

    from app.database import async_session_factory
    from app.database.models import Skill, SkillVersion
    from app.services.storage_service import storage_service

    sid = uuid.UUID(skill_id)
    vid = uuid.UUID(version_id)
    skill_name = file_name.rsplit(".", 1)[0]
    
    logger.info(f"Starting ingestion for skill: {skill_name} ({skill_id})")

    async with async_session_factory() as session:
        skill = await session.get(Skill, sid)
        version = await session.get(SkillVersion, vid)
        
        if not skill or not version:
            logger.error(f"Skill {skill_id} or Version {version_id} not found in DB")
            return

        try:
            skill.status = "processing"
            await session.commit()

            if not os.path.exists(file_path):
                logger.error(f"Disk buffer file not found: {file_path}")
                skill.status = "error"
                await session.commit()
                return

            import asyncio

            from app.services.kb_service import _guess_content_type

            # 1. Unzip with streaming, security checks, and concurrent uploads
            MAX_UNCOMPRESSED_SIZE = 10 * 1024 * 1024  # 10 MB
            MAX_FILE_COUNT = 100

            total_size = 0
            file_count = 0

            upload_tasks = []
            semaphore = asyncio.Semaphore(10)

            async def _upload_worker(zf_path, member_name, obj_name, file_size):
                async with semaphore:
                    # Open a fresh ZipFile instance in the thread to avoid GIL lock contention
                    with zipfile.ZipFile(zf_path) as local_zf:
                        with local_zf.open(member_name) as f_stream:
                            await storage_service.upload_stream_async(
                                obj_name, f_stream, file_size, _guess_content_type(member_name)
                            )

            with zipfile.ZipFile(file_path) as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    
                    filename = member.filename
                    
                    # [Security] Zip Slip check
                    if filename.startswith("/") or filename.startswith("\\") or "../" in filename or "..\\" in filename:
                        raise ValueError(f"Security risk: Zip Slip detected in {filename}")
                        
                    # [Security] File count check
                    file_count += 1
                    if file_count > MAX_FILE_COUNT:
                        raise ValueError(f"Too many files (exceeds {MAX_FILE_COUNT})")
                        
                    # [Security] Zip Bomb check
                    total_size += member.file_size
                    if total_size > MAX_UNCOMPRESSED_SIZE:
                        raise ValueError("Uncompressed size too large (exceeds 10MB)")

                    object_name = f"skills/{skill_id}/versions/{version.version_number}/content/{filename}"
                    target_readme = f"{skill_name}/SKILL.md".lower()

                    if filename.lower() == target_readme or filename.lower().endswith("/skill.md"):
                        with zf.open(member) as f:
                            content = f.read()
                        
                        storage_service.upload_file(
                            object_name=object_name,
                            data=content,
                            content_type=_guess_content_type(filename)
                        )
                    else:
                        upload_tasks.append(
                            _upload_worker(file_path, filename, object_name, member.file_size)
                        )

            if upload_tasks:
                await asyncio.gather(*upload_tasks)

            # 3. Calculate content-based hash (consistent with contribution workflow)
            storage_path = f"skills/{skill_id}/versions/{version.version_number}/content/"
            file_hash = storage_service.calculate_prefix_hash(storage_path)

            # 4. Update DB with extracted metadata

            skill.version_hash = file_hash
            skill.current_version = version.version_number
            skill.storage_path = storage_path
            skill.status = "active"
            
            version.version_hash = file_hash
            version.storage_path = storage_path
            
            await session.commit()
            logger.success(f"Skill {skill_name} version {version.version_number} processed successfully")

        except Exception as e:
            logger.exception(f"Failed to process skill {skill_name}: {e}")
            skill.status = "error"
            await session.commit()
        finally:
            # Clean up disk buffer
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.debug(f"Cleaned up disk buffer: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete temp file {file_path}: {e}")


async def delete_skill_task(ctx: dict, skill_id: str):
    """
    arq task: delete skill files from MinIO and remove from DB.
    """
    from app.database import async_session_factory
    from app.database.models import Skill
    from app.services.storage_service import storage_service

    sid = uuid.UUID(skill_id)
    
    logger.info(f"Starting deletion task for skill: {skill_id}")

    async with async_session_factory() as session:
        skill = await session.get(Skill, sid)
        if not skill:
            logger.warning(f"Skill {skill_id} already deleted or not found")
            return

        try:
            from sqlalchemy.orm import selectinload
            # 1. Fetch skill with contributions to get their storage paths
            stmt = select(Skill).where(Skill.id == sid).options(selectinload(Skill.contributions))
            res = await session.execute(stmt)
            skill = res.scalars().first()
            if not skill:
                return

            # 2. Delete files from MinIO for the skill itself
            prefix = f"skills/{skill_id}/"
            storage_service.delete_prefix(prefix)
            
            # 3. Delete files for all associated contributions
            for contrib in skill.contributions:
                if contrib.storage_path:
                    logger.info(f"Deleting storage for contribution {contrib.id}: {contrib.storage_path}")
                    storage_service.delete_prefix(contrib.storage_path)

            # 4. Delete skill from DB (cascades to SkillVersion and SkillContribution DB rows)
            await session.delete(skill)
            await session.commit()
            
            logger.success(f"Skill {skill_id} and all related assets (versions, contributions) deleted successfully")

        except Exception as e:
            logger.exception(f"Failed to delete skill {skill_id}: {e}")
            raise


async def cleanup_temp_uploads_cron(ctx: dict):
    """
    Cronjob: Quét và dọn các file rác trong temp_uploads do server crash để lại (cũ hơn 1 giờ).
    """
    import os
    import time
    
    temp_dir = "temp_uploads"
    if not os.path.exists(temp_dir):
        return
        
    cutoff_time = time.time() - 3600  # 1 hour ago
    
    for filename in os.listdir(temp_dir):
        file_path = os.path.join(temp_dir, filename)
        if os.path.isfile(file_path):
            if os.path.getmtime(file_path) < cutoff_time:
                try:
                    os.remove(file_path)
                    logger.info(f"Cronjob: Cleaned up orphaned temp file {filename}")
                except Exception as e:
                    logger.debug(f"Cronjob: Failed to clean {filename}: {e}")


# ---------------------------------------------------------------------------
# Embedding migration: re-embed every wiki page with a new model
# ---------------------------------------------------------------------------

async def reembed_all_pages_task(ctx: dict, job_id: str) -> None:
    """
    Re-embed every wiki page using the model spec referenced by the job.

    On success, atomically flips `app_config.active_embedding_model_spec_id`
    to the new spec — search keeps using the OLD model until that flip lands,
    so there is no zero-result window during the migration.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.ai.embedding_catalog import get_spec
    from app.ai.registry import ProviderRegistry
    from app.database import async_session_factory
    from app.database.models import EmbeddingJob, WikiPage
    from app.services.config_service import (
        ACTIVE_EMBEDDING_MODEL_KEY,
        ConfigService,
    )
    from app.services.embedding_storage import (
        cleanup_stale_embeddings,
        compute_content_hash,
        embedding_input_text,
        upsert_page_embedding,
    )

    job_uuid = uuid.UUID(job_id)
    BATCH = 50

    async with async_session_factory() as session:
        job = await session.get(EmbeddingJob, job_uuid)
        if job is None:
            logger.error(f"reembed: job {job_id} not found")
            return
        if job.status not in ("pending", "running"):
            logger.info(f"reembed: job {job_id} status={job.status}, skipping")
            return

        try:
            spec = get_spec(job.model_spec_id)
        except Exception as e:
            job.status = "failed"
            job.error_message = f"Unknown model spec: {e}"
            job.finished_at = datetime.now(timezone.utc)
            await session.commit()
            return

        # Provision a provider bound to the NEW spec (not the active one).
        registry = ProviderRegistry(session)
        try:
            provider = await registry.get_embedding(
                task="document", spec_id=spec.id
            )
        except Exception as e:
            job.status = "failed"
            job.error_message = f"Provider init failed: {e}"
            job.finished_at = datetime.now(timezone.utc)
            await session.commit()
            return

        # Count work and mark running.
        total = (
            await session.execute(
                select(WikiPage.id).where(
                    WikiPage.slug.notin_(["_index", "_log"])
                )
            )
        ).scalars().all()
        job.total_pages = len(total)
        job.done_pages = 0
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await session.commit()

    logger.info(
        f"reembed: starting job {job_id} model={spec.id} dim={spec.dimension} "
        f"total={len(total)}"
    )

    # Process batches in independent sessions so progress is visible to UI poll.
    for offset in range(0, len(total), BATCH):
        batch_ids = total[offset : offset + BATCH]
        async with async_session_factory() as session:
            # Re-check cancellation flag.
            job = await session.get(EmbeddingJob, job_uuid)
            if job is None or job.status == "cancelled":
                logger.info(f"reembed: job {job_id} cancelled at offset={offset}")
                return

            pages = (
                await session.execute(
                    select(WikiPage).where(WikiPage.id.in_(batch_ids))
                )
            ).scalars().all()
            inputs = [
                embedding_input_text(p.title, p.summary or "", p.content_md or "")
                for p in pages
            ]
            try:
                vectors = await provider.embed_batch(inputs)
            except Exception as e:
                job.status = "failed"
                job.error_message = f"Embedding API failed: {e}"
                job.finished_at = datetime.now(timezone.utc)
                await session.commit()
                logger.exception(f"reembed: job {job_id} failed at offset={offset}")
                return

            for page, vec in zip(pages, vectors):
                await upsert_page_embedding(
                    session,
                    page_id=page.id,
                    spec=spec,
                    vector=list(vec),
                    content_hash=compute_content_hash(
                        page.title, page.summary or "", page.content_md or ""
                    ),
                )
            job.done_pages = min(offset + len(pages), job.total_pages)
            await session.commit()

    # Atomic flip + cleanup of old model's vectors.
    async with async_session_factory() as session:
        job = await session.get(EmbeddingJob, job_uuid)
        if job is None or job.status == "cancelled":
            return
        svc = ConfigService(session)
        await svc.set(ACTIVE_EMBEDDING_MODEL_KEY, spec.id)
        deleted = await cleanup_stale_embeddings(session, keep_spec_id=spec.id)
        job.status = "completed"
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info(
            f"reembed: job {job_id} complete — flipped to {spec.id}, "
            f"cleaned up {deleted} stale embedding rows"
        )


async def crawl_site_task(ctx: dict, crawl_job_id: str):
    """
    arq task: BFS site crawl — renders pages with Playwright via crawl4ai,
    exports each page to PDF, creates Source records, and enqueues ingest_file_task.
    """
    from datetime import datetime, timezone

    from app.database import async_session_factory
    from app.database.models import CrawlJob, Source
    from app.services.crawler_utils import (
        extract_same_domain_links,
        generate_file_name_from_url,
        normalize_url,
    )
    from app.services.storage_service import storage_service

    job_uuid = uuid.UUID(crawl_job_id)

    # -----------------------------------------------------------------------
    # 9.1 Status guard and transition
    # -----------------------------------------------------------------------
    async with async_session_factory() as session:
        job = await session.get(CrawlJob, job_uuid)
        if not job:
            raise ValueError(f"CrawlJob {crawl_job_id} not found")

        if job.status != "pending":
            logger.info(
                f"crawl_site_task: job {crawl_job_id} has status={job.status!r}, skipping"
            )
            return

        job.status = "running"
        await session.commit()
        logger.info(f"crawl_site_task: job {crawl_job_id} transitioned to running")

    # -----------------------------------------------------------------------
    # 9.2 + 9.3 BFS crawl loop with Source creation and MinIO upload
    # -----------------------------------------------------------------------
    try:
        # Load job fields needed for the BFS loop
        async with async_session_factory() as session:
            job = await session.get(CrawlJob, job_uuid)
            start_url_raw: str = job.start_url
            domain: str = job.domain
            knowledge_type_id = job.knowledge_type_id
            scope_type: str = job.scope_type
            scope_id = job.scope_id
            created_by_id = job.created_by_id
            # Restore any previously visited URLs (resume support)
            visited_urls: set[str] = set(job.visited_urls or [])

        start_url = normalize_url(start_url_raw)

        # BFS queue — use a list as a FIFO queue
        queue: list[str] = []
        if start_url not in visited_urls:
            queue.append(start_url)
            visited_urls.add(start_url)
            # Persist initial state
            async with async_session_factory() as session:
                job = await session.get(CrawlJob, job_uuid)
                job.visited_urls = list(visited_urls)
                job.pages_discovered = job.pages_discovered + 1
                await session.commit()

        # Import crawl4ai here so the worker can start even if crawl4ai is
        # not installed in non-crawler environments (graceful import error).
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        except ImportError as exc:
            raise RuntimeError(
                "crawl4ai is not installed. Add it to requirements.txt."
            ) from exc

        browser_cfg = BrowserConfig(headless=True)

        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            while queue:
                current_url = queue.pop(0)

                # ---------------------------------------------------------------
                # Dedup check at dequeue time (req 2.7)
                # ---------------------------------------------------------------
                # visited_urls already contains current_url (added at enqueue),
                # but if somehow it slipped through, skip it.
                # The real dedup guard is: we only enqueue URLs not in visited_urls.

                logger.info(f"crawl_site_task [{crawl_job_id}]: crawling {current_url}")

                # ---------------------------------------------------------------
                # Render page with retry (req 3.1, 3.5)
                # ---------------------------------------------------------------
                MAX_RETRIES = 2  # up to 2 additional attempts after the first
                result = None
                last_error: Exception | None = None

                for attempt in range(MAX_RETRIES + 1):
                    try:
                        run_cfg = CrawlerRunConfig(
                            page_timeout=30_000,  # 30 s render timeout (ms)
                        )
                        result = await crawler.arun(url=current_url, config=run_cfg)
                        last_error = None
                        break  # success
                    except Exception as exc:
                        last_error = exc
                        logger.warning(
                            f"crawl_site_task [{crawl_job_id}]: attempt {attempt + 1} "
                            f"failed for {current_url}: {exc}"
                        )
                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(2)  # brief back-off before retry

                # ---------------------------------------------------------------
                # Handle render failure (all retries exhausted)
                # ---------------------------------------------------------------
                if last_error is not None or result is None:
                    logger.error(
                        f"crawl_site_task [{crawl_job_id}]: all retries exhausted "
                        f"for {current_url}: {last_error}"
                    )
                    async with async_session_factory() as session:
                        job = await session.get(CrawlJob, job_uuid)
                        job.pages_failed = job.pages_failed + 1
                        job.visited_urls = list(visited_urls)
                        await session.commit()
                    continue

                # ---------------------------------------------------------------
                # Handle HTTP error codes (req 3.3, 3.4)
                # ---------------------------------------------------------------
                status_code: int = result.status_code or 0
                if status_code in (401, 403):
                    logger.info(
                        f"crawl_site_task [{crawl_job_id}]: HTTP {status_code} "
                        f"for {current_url} — skipping"
                    )
                    async with async_session_factory() as session:
                        job = await session.get(CrawlJob, job_uuid)
                        job.pages_skipped = job.pages_skipped + 1
                        job.visited_urls = list(visited_urls)
                        await session.commit()
                    continue

                if status_code >= 400:
                    logger.warning(
                        f"crawl_site_task [{crawl_job_id}]: HTTP {status_code} "
                        f"for {current_url} — failed"
                    )
                    async with async_session_factory() as session:
                        job = await session.get(CrawlJob, job_uuid)
                        job.pages_failed = job.pages_failed + 1
                        job.visited_urls = list(visited_urls)
                        await session.commit()
                    continue

                # ---------------------------------------------------------------
                # Export PDF bytes (req 3.2)
                # ---------------------------------------------------------------
                pdf_bytes: bytes | None = None
                try:
                    # crawl4ai exposes the Playwright page via result.page when
                    # screenshot/pdf is requested.  We use the pdf attribute if
                    # available, otherwise fall back to the screenshot bytes.
                    # The canonical way with crawl4ai ≥ 0.4 is to set
                    # pdf=True in CrawlerRunConfig and read result.pdf.
                    if hasattr(result, "pdf") and result.pdf:
                        pdf_bytes = result.pdf
                    else:
                        # Re-run with pdf=True to get the PDF
                        run_cfg_pdf = CrawlerRunConfig(
                            page_timeout=30_000,
                            pdf=True,
                        )
                        pdf_result = await asyncio.wait_for(
                            crawler.arun(url=current_url, config=run_cfg_pdf),
                            timeout=30,
                        )
                        if hasattr(pdf_result, "pdf") and pdf_result.pdf:
                            pdf_bytes = pdf_result.pdf
                except asyncio.TimeoutError:
                    logger.warning(
                        f"crawl_site_task [{crawl_job_id}]: PDF export timed out "
                        f"for {current_url}"
                    )
                except Exception as exc:
                    logger.warning(
                        f"crawl_site_task [{crawl_job_id}]: PDF export failed "
                        f"for {current_url}: {exc}"
                    )

                if not pdf_bytes:
                    logger.warning(
                        f"crawl_site_task [{crawl_job_id}]: no PDF bytes for "
                        f"{current_url} — counting as failed"
                    )
                    async with async_session_factory() as session:
                        job = await session.get(CrawlJob, job_uuid)
                        job.pages_failed = job.pages_failed + 1
                        job.visited_urls = list(visited_urls)
                        await session.commit()
                    continue

                # ---------------------------------------------------------------
                # 9.3 Source record creation and MinIO upload
                # ---------------------------------------------------------------
                rendered_html: str = result.html or ""

                # Extract <title> tag (req 4.2)
                page_title: str = current_url  # fallback
                try:
                    import re as _re
                    title_match = _re.search(
                        r"<title[^>]*>(.*?)</title>",
                        rendered_html,
                        _re.IGNORECASE | _re.DOTALL,
                    )
                    if title_match:
                        raw_title = title_match.group(1).strip()
                        if raw_title:
                            page_title = raw_title[:500]
                except Exception:
                    pass

                file_name = generate_file_name_from_url(current_url)
                slug = file_name[:-4]  # strip .pdf for the path component

                async with async_session_factory() as session:
                    # Check for duplicate Source for this URL + CrawlJob (req 4.7)
                    from sqlalchemy import select as sa_select
                    existing_stmt = sa_select(Source).where(
                        Source.url == current_url,
                        Source.metadata_["crawl_job_id"].astext == crawl_job_id,
                    )
                    existing_result = await session.execute(existing_stmt)
                    existing_source = existing_result.scalars().first()

                    if existing_source is not None:
                        logger.info(
                            f"crawl_site_task [{crawl_job_id}]: Source already exists "
                            f"for {current_url} — skipping"
                        )
                        job = await session.get(CrawlJob, job_uuid)
                        job.pages_skipped = job.pages_skipped + 1
                        job.visited_urls = list(visited_urls)
                        await session.commit()
                        # Still extract links from the rendered HTML
                    else:
                        # Create Source record (req 4.1, 4.2, 4.3, 4.6)
                        source = Source(
                            source_type="file",
                            status="pending",
                            file_name=file_name,
                            title=page_title,
                            url=current_url,
                            knowledge_type_id=knowledge_type_id,
                            scope_type=scope_type,
                            scope_id=scope_id,
                            contributed_by_employee_id=created_by_id,
                            metadata_={"crawl_job_id": crawl_job_id},
                        )
                        session.add(source)
                        await session.flush()  # get source.id

                        source_id = source.id
                        minio_key = f"sources/{source_id}/original/{slug}.pdf"

                        # Upload PDF to MinIO (req 4.4)
                        upload_ok = False
                        try:
                            await asyncio.to_thread(
                                storage_service.upload_file,
                                minio_key,
                                pdf_bytes,
                                "application/pdf",
                            )
                            source.minio_key = minio_key
                            upload_ok = True
                        except Exception as exc:
                            logger.error(
                                f"crawl_site_task [{crawl_job_id}]: MinIO upload failed "
                                f"for {current_url}: {exc}"
                            )
                            source.status = "error"
                            source.error_message = str(exc)[:500]

                        if upload_ok:
                            # Enqueue ingest_file_task (req 4.5)
                            try:
                                arq_pool = await get_arq_pool()
                                arq_job = await arq_pool.enqueue_job(
                                    "ingest_file_task", str(source_id)
                                )
                                source.job_id = arq_job.job_id if arq_job else None
                            except Exception as exc:
                                logger.error(
                                    f"crawl_site_task [{crawl_job_id}]: enqueue failed "
                                    f"for source {source_id}: {exc}"
                                )
                                source.status = "error"
                                source.error_message = (
                                    f"Failed to enqueue ingest_file_task: {str(exc)[:450]}"
                                )

                        # Increment pages_crawled only on successful PDF export (req 3.6, 5.3)
                        job = await session.get(CrawlJob, job_uuid)
                        job.pages_crawled = job.pages_crawled + 1
                        job.visited_urls = list(visited_urls)
                        await session.commit()

                # ---------------------------------------------------------------
                # Extract links and enqueue new URLs (req 2.3, 2.4, 2.5, 5.2)
                # ---------------------------------------------------------------
                new_links = extract_same_domain_links(rendered_html, domain, current_url)
                new_urls: list[str] = []
                for link in new_links:
                    if link not in visited_urls:
                        visited_urls.add(link)
                        queue.append(link)
                        new_urls.append(link)

                if new_urls:
                    async with async_session_factory() as session:
                        job = await session.get(CrawlJob, job_uuid)
                        job.pages_discovered = job.pages_discovered + len(new_urls)
                        job.visited_urls = list(visited_urls)
                        await session.commit()

        # -----------------------------------------------------------------------
        # 9.4 BFS completion
        # -----------------------------------------------------------------------
        async with async_session_factory() as session:
            job = await session.get(CrawlJob, job_uuid)
            job.status = "completed"
            job.finished_at = datetime.now(timezone.utc)
            await session.commit()
        logger.success(f"crawl_site_task [{crawl_job_id}]: completed")

    except BaseException as exc:
        # -----------------------------------------------------------------------
        # 9.4 Unhandled exception → failed
        # -----------------------------------------------------------------------
        logger.error(f"crawl_site_task [{crawl_job_id}]: unhandled error: {exc}")
        error_msg = str(exc)[:500]

        async def _mark_failed() -> None:
            from datetime import datetime, timezone as _tz
            from app.database import async_session_factory as _sf
            from app.database.models import CrawlJob as _CrawlJob
            async with _sf() as err_session:
                j = await err_session.get(_CrawlJob, job_uuid)
                if j:
                    j.status = "failed"
                    j.error_message = error_msg
                    j.finished_at = datetime.now(_tz.utc)
                    await err_session.commit()

        try:
            await asyncio.shield(_mark_failed())
        except Exception:
            pass
        raise


class WorkerSettings:
    """arq worker configuration."""

    functions = [
        ingest_file_task,
        ingest_url_task,
        reembed_all_pages_task,
        crawl_site_task,
    ]
    redis_settings = _get_redis_settings()
    max_jobs = settings.worker_max_jobs
    job_timeout = settings.worker_job_timeout
    max_tries = 3
    retry_delay = 10
    health_check_interval = 30

    @staticmethod
    async def on_startup(ctx: dict):
        logger.info("arq worker started — listening for ingestion jobs...")

    @staticmethod
    async def on_shutdown(ctx: dict):
        logger.info("arq worker shutting down...")


class SkillWorkerSettings:
    """arq worker configuration dedicated to Skills."""

    functions = [ingest_skill_task, delete_skill_task]
    queue_name = "skills_queue"
    redis_settings = _get_redis_settings()
    max_jobs = settings.worker_max_jobs
    job_timeout = settings.worker_job_timeout
    max_tries = 3
    retry_delay = 10
    health_check_interval = 30
    
    cron_jobs = [
        cron(cleanup_temp_uploads_cron, minute=0)
    ]

    @staticmethod
    async def on_startup(ctx: dict):
        logger.info("arq skills worker started — listening for skill jobs...")

    @staticmethod
    async def on_shutdown(ctx: dict):
        logger.info("arq skills worker shutting down...")
