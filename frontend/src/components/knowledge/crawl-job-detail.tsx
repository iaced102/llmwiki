"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

// ─── Types ────────────────────────────────────────────────────────────────────

type CrawlJob = {
  id: string;
  start_url: string;
  domain: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  mode: string;
  pages_discovered: number;
  pages_crawled: number;
  pages_failed: number;
  pages_skipped: number;
  failed_urls: string[];
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
};

type CrawlPage = {
  id: string;
  title: string | null;
  url: string | null;
  status: string;
  error_message: string | null;
  has_pdf: boolean;
  created_at: string;
};

// ─── Status helpers ───────────────────────────────────────────────────────────

const JOB_STATUS_COLORS: Record<string, string> = {
  running: "#c2652a",
  completed: "#16a34a",
  failed: "#dc2626",
  cancelled: "#6b7280",
  pending: "#ca8a04",
};

const JOB_STATUS_LABELS: Record<string, string> = {
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  pending: "Pending",
};

function JobStatusBadge({ status }: { status: string }) {
  const color = JOB_STATUS_COLORS[status] ?? "#6b7280";
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold text-white"
      style={{ backgroundColor: color }}
    >
      {(status === "running" || status === "pending") && (
        <span className="material-symbols-outlined animate-spin" style={{ fontSize: 12 }}>
          progress_activity
        </span>
      )}
      {JOB_STATUS_LABELS[status] ?? status}
    </span>
  );
}

// Page status: pending = crawled, ready = ingested, error = failed, processing = ingesting
function PageStatusIndicator({ status }: { status: string }) {
  if (status === "ready") {
    return (
      <span className="material-symbols-outlined text-green-600 dark:text-green-400" style={{ fontSize: 18 }} title="Ingested">
        check_circle
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="material-symbols-outlined text-destructive" style={{ fontSize: 18 }} title="Failed">
        error
      </span>
    );
  }
  if (status === "processing") {
    return (
      <span className="material-symbols-outlined animate-spin text-[#c2652a]" style={{ fontSize: 18 }} title="Processing">
        progress_activity
      </span>
    );
  }
  // pending = crawled, waiting for ingest
  return (
    <span className="material-symbols-outlined text-muted-foreground" style={{ fontSize: 18 }} title="Crawled — ready to ingest">
      hourglass_empty
    </span>
  );
}

function Counter({ label, value, icon }: { label: string; value: number; icon: string }) {
  return (
    <div className="flex flex-col items-center gap-1 p-3 rounded-xl border bg-card min-w-[90px]">
      <span className="material-symbols-outlined text-muted-foreground" style={{ fontSize: 20 }}>{icon}</span>
      <span className="text-xl font-bold tabular-nums">{value}</span>
      <span className="text-xs text-muted-foreground text-center">{label}</span>
    </div>
  );
}

function useElapsed(createdAt: string, stopped: boolean) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const start = new Date(createdAt).getTime();
    const tick = () => setElapsed(Math.floor((Date.now() - start) / 1000));
    tick();
    if (stopped) return;
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [createdAt, stopped]);
  const h = Math.floor(elapsed / 3600);
  const m = Math.floor((elapsed % 3600) / 60);
  const s = elapsed % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

// ─── PDF Panel ────────────────────────────────────────────────────────────────

function PdfPanel({
  jobId,
  page,
  onClose,
}: {
  jobId: string;
  page: CrawlPage;
  onClose: () => void;
}) {
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api<{ pdf_url: string }>(`/api/crawl-jobs/${jobId}/pages/${page.id}/pdf-url`)
      .then((data) => { if (!cancelled) { setPdfUrl(data.pdf_url); setLoading(false); } })
      .catch(() => { if (!cancelled) { setError("Failed to load PDF."); setLoading(false); } });
    return () => { cancelled = true; };
  }, [jobId, page.id]);

  return (
    <div className="flex flex-col h-full border-l bg-card">
      {/* Panel header */}
      <div className="flex items-center justify-between px-4 py-3 border-b shrink-0">
        <div className="flex flex-col gap-0.5 min-w-0">
          <p className="text-sm font-semibold truncate">{page.title || page.url}</p>
          <p className="text-xs text-muted-foreground font-mono truncate">{page.url}</p>
        </div>
        <button
          onClick={onClose}
          className="ml-3 shrink-0 p-1 rounded hover:bg-muted transition-colors"
          aria-label="Close PDF panel"
        >
          <span className="material-symbols-outlined" style={{ fontSize: 20 }}>close</span>
        </button>
      </div>

      {/* PDF content */}
      <div className="flex-1 min-h-0">
        {loading && (
          <div className="flex items-center justify-center h-full">
            <span className="material-symbols-outlined animate-spin text-3xl text-primary">progress_activity</span>
          </div>
        )}
        {error && (
          <div className="flex items-center justify-center h-full text-destructive text-sm">{error}</div>
        )}
        {pdfUrl && !loading && (
          <iframe
            src={pdfUrl}
            className="w-full h-full"
            title={page.title || "PDF Preview"}
          />
        )}
      </div>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

type Props = { jobId: string };

export function CrawlJobDetail({ jobId }: Props) {
  const router = useRouter();

  // Job state
  const [job, setJob] = useState<CrawlJob | null>(null);
  const [jobLoading, setJobLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  // Pages state
  const [pages, setPages] = useState<CrawlPage[]>([]);
  const [pagesLoading, setPagesLoading] = useState(false);

  // Selection
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");

  // Actions
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  // PDF panel
  const [previewPage, setPreviewPage] = useState<CrawlPage | null>(null);

  const isTerminal = job?.status === "completed" || job?.status === "failed" || job?.status === "cancelled";
  const elapsed = useElapsed(job?.created_at ?? new Date().toISOString(), !!isTerminal);

  // Load job
  useEffect(() => {
    let cancelled = false;
    api<CrawlJob>(`/api/crawl-jobs/${jobId}`)
      .then((data) => { if (!cancelled) { setJob(data); setJobLoading(false); } })
      .catch((err: unknown) => {
        if (!cancelled) {
          if (err instanceof ApiError && err.status === 404) setNotFound(true);
          setJobLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [jobId]);

  // Poll job while active
  useEffect(() => {
    if (isTerminal || notFound || !job) return;
    const id = setInterval(() => {
      api<CrawlJob>(`/api/crawl-jobs/${jobId}`)
        .then((data) => setJob(data))
        .catch(() => {});
    }, 3000);
    return () => clearInterval(id);
  }, [isTerminal, notFound, job, jobId]);

  // Load pages when job completes or is already terminal
  useEffect(() => {
    if (!job || !isTerminal) return;
    setPagesLoading(true);
    api<{ pages: CrawlPage[] }>(`/api/crawl-jobs/${jobId}/pages`)
      .then((data) => { setPages(data.pages); setPagesLoading(false); })
      .catch(() => setPagesLoading(false));
  }, [job?.status, isTerminal, jobId]);

  // Poll pages while job is running (live updates)
  useEffect(() => {
    if (!job || isTerminal || notFound) return;
    const id = setInterval(() => {
      api<{ pages: CrawlPage[] }>(`/api/crawl-jobs/${jobId}/pages`)
        .then((data) => setPages(data.pages))
        .catch(() => {});
    }, 5000);
    return () => clearInterval(id);
  }, [job, isTerminal, notFound, jobId]);

  // Filtered pages
  const filteredPages = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return pages;
    return pages.filter(
      (p) =>
        (p.title ?? "").toLowerCase().includes(q) ||
        (p.url ?? "").toLowerCase().includes(q)
    );
  }, [pages, filter]);

  // Selection helpers
  const selectedPendingCount = [...selected].filter(
    (id) => pages.find((p) => p.id === id)?.status === "pending"
  ).length;
  const selectedErrorCount = [...selected].filter(
    (id) => pages.find((p) => p.id === id)?.status === "error"
  ).length;

  const allFilteredSelected =
    filteredPages.length > 0 && filteredPages.every((p) => selected.has(p.id));

  const toggleAll = () => {
    if (allFilteredSelected) {
      setSelected((prev) => {
        const next = new Set(prev);
        filteredPages.forEach((p) => next.delete(p.id));
        return next;
      });
    } else {
      setSelected((prev) => {
        const next = new Set(prev);
        filteredPages.forEach((p) => next.add(p.id));
        return next;
      });
    }
  };

  const togglePage = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Actions
  const handleCancel = async () => {
    if (!job) return;
    setCancelling(true);
    setCancelError(null);
    try {
      await api(`/api/crawl-jobs/${job.id}/cancel`, { method: "POST" });
      setJob((prev) => prev ? { ...prev, status: "cancelled" } : prev);
    } catch (err) {
      setCancelError(err instanceof ApiError ? err.message : "Failed to cancel.");
    } finally {
      setCancelling(false);
    }
  };

  const handleIngest = async () => {
    if (!job || selectedPendingCount === 0) return;
    setIngesting(true);
    setIngestError(null);
    const pendingIds = [...selected].filter(
      (id) => pages.find((p) => p.id === id)?.status === "pending"
    );
    try {
      await api(`/api/crawl-jobs/${job.id}/ingest`, {
        method: "POST",
        body: { source_ids: pendingIds },
      });
      // Refresh pages
      const data = await api<{ pages: CrawlPage[] }>(`/api/crawl-jobs/${jobId}/pages`);
      setPages(data.pages);
      setSelected(new Set());
    } catch (err) {
      setIngestError(err instanceof ApiError ? err.message : "Failed to start ingestion.");
    } finally {
      setIngesting(false);
    }
  };

  const handleRetry = async () => {
    if (!job || selectedErrorCount === 0) return;
    setRetrying(true);
    setRetryError(null);
    const errorIds = [...selected].filter(
      (id) => pages.find((p) => p.id === id)?.status === "error"
    );
    try {
      await api(`/api/crawl-jobs/${job.id}/ingest`, {
        method: "POST",
        body: { source_ids: errorIds },
      });
      const data = await api<{ pages: CrawlPage[] }>(`/api/crawl-jobs/${jobId}/pages`);
      setPages(data.pages);
      setSelected(new Set());
    } catch (err) {
      setRetryError(err instanceof ApiError ? err.message : "Failed to retry.");
    } finally {
      setRetrying(false);
    }
  };

  // ── Render ──────────────────────────────────────────────────────────────────

  if (jobLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="material-symbols-outlined animate-spin text-4xl text-primary">progress_activity</span>
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="flex flex-col items-center gap-4 py-20 text-muted-foreground">
        <span className="material-symbols-outlined text-5xl">search_off</span>
        <p className="text-lg">Crawl job not found.</p>
        <Button variant="outline" onClick={() => router.push("/knowledge")}>Back to Knowledge Base</Button>
      </div>
    );
  }

  if (!job) return null;

  return (
    <div className="flex flex-col gap-5">
      {/* ── Job header ── */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-3 flex-wrap">
            <JobStatusBadge status={job.status} />
            <span className="text-sm text-muted-foreground font-mono break-all">{job.start_url}</span>
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            Elapsed: {elapsed} · Started {new Date(job.created_at).toLocaleString()}
          </p>
        </div>

        {/* Cancel button */}
        {(job.status === "running" || job.status === "pending") && (
          <Button variant="outline" size="sm" onClick={handleCancel} disabled={cancelling} className="self-start sm:self-auto">
            {cancelling ? (
              <span className="flex items-center gap-1.5">
                <span className="material-symbols-outlined animate-spin text-sm">progress_activity</span>
                Cancelling...
              </span>
            ) : (
              <><span className="material-symbols-outlined text-sm mr-1">cancel</span>Cancel</>
            )}
          </Button>
        )}
      </div>

      {cancelError && (
        <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg flex items-center gap-2">
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>error</span>{cancelError}
        </p>
      )}

      {/* ── Counters ── */}
      <div className="flex flex-wrap gap-3">
        <Counter label="Discovered" value={job.pages_discovered} icon="travel_explore" />
        <Counter label="Crawled" value={job.pages_crawled} icon="check_circle" />
        <Counter label="Failed" value={job.pages_failed} icon="error" />
        <Counter label="Skipped" value={job.pages_skipped} icon="skip_next" />
      </div>

      {/* ── Error banner ── */}
      {job.status === "failed" && job.error_message && (
        <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4">
          <div className="flex items-center gap-2 text-destructive font-semibold mb-1">
            <span className="material-symbols-outlined" style={{ fontSize: 20 }}>error</span>
            Crawl failed
          </div>
          <p className="text-sm text-destructive/80 font-mono break-all">{job.error_message}</p>
        </div>
      )}

      {/* ── Pages list (shown when there are pages) ── */}
      {(pages.length > 0 || pagesLoading) && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <h3 className="text-base font-semibold">
              Pages {pages.length > 0 && `(${pages.length})`}
            </h3>

            {/* Action toolbar */}
            <div className="flex items-center gap-2 flex-wrap">
              {selectedPendingCount > 0 && (
                <Button
                  size="sm"
                  onClick={handleIngest}
                  disabled={ingesting}
                  className="bg-primary text-primary-foreground hover:bg-primary/90"
                >
                  {ingesting ? (
                    <span className="flex items-center gap-1.5">
                      <span className="material-symbols-outlined animate-spin text-sm">progress_activity</span>
                      Processing...
                    </span>
                  ) : (
                    <><span className="material-symbols-outlined text-sm mr-1">auto_awesome</span>
                    Process {selectedPendingCount} page{selectedPendingCount !== 1 ? "s" : ""}</>
                  )}
                </Button>
              )}
              {selectedErrorCount > 0 && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleRetry}
                  disabled={retrying}
                  className="border-orange-300 text-orange-700 hover:bg-orange-50 dark:border-orange-700 dark:text-orange-400"
                >
                  {retrying ? (
                    <span className="flex items-center gap-1.5">
                      <span className="material-symbols-outlined animate-spin text-sm">progress_activity</span>
                      Retrying...
                    </span>
                  ) : (
                    <><span className="material-symbols-outlined text-sm mr-1">replay</span>
                    Retry {selectedErrorCount} failed</>
                  )}
                </Button>
              )}
            </div>
          </div>

          {(ingestError || retryError) && (
            <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg flex items-center gap-2">
              <span className="material-symbols-outlined" style={{ fontSize: 16 }}>error</span>
              {ingestError || retryError}
            </p>
          )}

          {/* Filter + Select All */}
          <div className="flex items-center gap-2">
            <Input
              placeholder="Filter by title or URL..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="flex-1"
            />
            <Button variant="outline" size="sm" onClick={toggleAll} disabled={filteredPages.length === 0}>
              {allFilteredSelected ? "Deselect All" : "Select All"}
            </Button>
          </div>

          {selected.size > 0 && (
            <p className="text-xs text-muted-foreground -mt-1">
              {selected.size} selected ({selectedPendingCount} crawled, {selectedErrorCount} failed)
            </p>
          )}

          {pagesLoading ? (
            <div className="flex items-center justify-center py-10">
              <span className="material-symbols-outlined animate-spin text-3xl text-primary">progress_activity</span>
            </div>
          ) : (
            /* Split view wrapper */
            <div className={`flex gap-0 rounded-xl border overflow-hidden ${previewPage ? "h-[600px]" : ""}`}>
              {/* Page list */}
              <div className={`flex flex-col divide-y divide-border overflow-y-auto ${previewPage ? "w-1/2 shrink-0" : "w-full"}`}>
                {filteredPages.length === 0 ? (
                  <div className="flex items-center justify-center py-10 text-muted-foreground text-sm">
                    No pages match your filter.
                  </div>
                ) : (
                  filteredPages.map((page) => (
                    <div
                      key={page.id}
                      className={`flex items-center gap-3 px-4 py-3 hover:bg-muted/40 transition-colors ${
                        previewPage?.id === page.id ? "bg-muted/60" : ""
                      }`}
                    >
                      {/* Checkbox */}
                      <input
                        type="checkbox"
                        checked={selected.has(page.id)}
                        onChange={() => togglePage(page.id)}
                        className="accent-primary w-4 h-4 shrink-0"
                      />

                      {/* Status icon */}
                      <PageStatusIndicator status={page.status} />

                      {/* Title + URL */}
                      <div className="flex flex-col min-w-0 flex-1">
                        <span className="text-sm font-medium truncate">
                          {page.title || page.url || "Untitled"}
                        </span>
                        <span className="text-xs text-muted-foreground font-mono truncate">
                          {page.url}
                        </span>
                        {page.status === "error" && page.error_message && (
                          <span className="text-xs text-destructive truncate mt-0.5">
                            {page.error_message}
                          </span>
                        )}
                      </div>

                      {/* Preview button — only for pages with PDF */}
                      {page.has_pdf && (
                        <button
                          onClick={() =>
                            setPreviewPage(previewPage?.id === page.id ? null : page)
                          }
                          className="shrink-0 p-1 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
                          title="Preview PDF"
                        >
                          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>
                            {previewPage?.id === page.id ? "close" : "picture_as_pdf"}
                          </span>
                        </button>
                      )}
                    </div>
                  ))
                )}
              </div>

              {/* PDF panel */}
              {previewPage && (
                <div className="flex-1 min-w-0">
                  <PdfPanel
                    jobId={jobId}
                    page={previewPage}
                    onClose={() => setPreviewPage(null)}
                  />
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
