"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";

type CrawlJob = {
  id: string;
  start_url: string;
  domain: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  pages_discovered: number;
  pages_crawled: number;
  pages_failed: number;
  pages_skipped: number;
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
};

const STATUS_COLORS: Record<string, string> = {
  running: "#c2652a",
  completed: "#16a34a",
  failed: "#dc2626",
  cancelled: "#6b7280",
  pending: "#ca8a04",
};

const STATUS_LABELS: Record<string, string> = {
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  pending: "Pending",
};

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? "#6b7280";
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold text-white"
      style={{ backgroundColor: color }}
    >
      {(status === "running" || status === "pending") && (
        <span
          className="material-symbols-outlined animate-spin"
          style={{ fontSize: 12 }}
        >
          progress_activity
        </span>
      )}
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

function Counter({ label, value, icon }: { label: string; value: number; icon: string }) {
  return (
    <div className="flex flex-col items-center gap-1 p-4 rounded-xl border bg-card min-w-[100px]">
      <span className="material-symbols-outlined text-muted-foreground" style={{ fontSize: 22 }}>
        {icon}
      </span>
      <span className="text-2xl font-bold tabular-nums">{value}</span>
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

type Props = {
  jobId: string;
};

export function CrawlJobDetail({ jobId }: Props) {
  const router = useRouter();
  const [job, setJob] = useState<CrawlJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const isTerminal =
    job?.status === "completed" ||
    job?.status === "failed" ||
    job?.status === "cancelled";

  const elapsed = useElapsed(job?.created_at ?? new Date().toISOString(), isTerminal);

  // Initial load
  useEffect(() => {
    let cancelled = false;
    api<CrawlJob>(`/api/crawl-jobs/${jobId}`)
      .then((data) => {
        if (!cancelled) {
          setJob(data);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          if (err instanceof ApiError && err.status === 404) {
            setNotFound(true);
          }
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, setJob, setLoading, setNotFound]);

  // Polling while active
  useEffect(() => {
    if (isTerminal || notFound) return;
    const id = setInterval(() => {
      api<CrawlJob>(`/api/crawl-jobs/${jobId}`)
        .then((data) => setJob(data))
        .catch(() => {});
    }, 3000);
    return () => clearInterval(id);
  }, [isTerminal, notFound, jobId, setJob]);

  const handleCancel = async () => {
    if (!job) return;
    setCancelling(true);
    setCancelError(null);
    try {
      await api(`/api/crawl-jobs/${job.id}/cancel`, { method: "POST" });
      setJob((prev) => prev ? { ...prev, status: "cancelled" } : prev);
    } catch (err) {
      setCancelError(
        err instanceof ApiError ? err.message : "Failed to cancel job."
      );
    } finally {
      setCancelling(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="material-symbols-outlined animate-spin text-4xl text-primary">
          progress_activity
        </span>
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="flex flex-col items-center gap-4 py-20 text-muted-foreground">
        <span className="material-symbols-outlined text-5xl">search_off</span>
        <p className="text-lg">Crawl job not found.</p>
        <Button variant="outline" onClick={() => router.push("/knowledge")}>
          Back to Knowledge Base
        </Button>
      </div>
    );
  }

  if (!job) return null;

  return (
    <div className="flex flex-col gap-6">
      {/* Header row */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-3 flex-wrap">
            <StatusBadge status={job.status} />
            <span className="text-sm text-muted-foreground font-mono break-all">
              {job.start_url}
            </span>
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            Elapsed: {elapsed} · Started{" "}
            {new Date(job.created_at).toLocaleString()}
          </p>
        </div>

        {/* Cancel button */}
        {(job.status === "running" || job.status === "pending") && (
          <Button
            variant="outline"
            size="sm"
            onClick={handleCancel}
            disabled={cancelling}
            className="self-start sm:self-auto"
          >
            {cancelling ? (
              <span className="flex items-center gap-1.5">
                <span className="material-symbols-outlined animate-spin text-sm">
                  progress_activity
                </span>
                Cancelling...
              </span>
            ) : (
              <>
                <span className="material-symbols-outlined text-sm mr-1">cancel</span>
                Cancel
              </>
            )}
          </Button>
        )}
      </div>

      {cancelError && (
        <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg flex items-center gap-2">
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>error</span>
          {cancelError}
        </p>
      )}

      {/* Counters */}
      <div className="flex flex-wrap gap-3">
        <Counter label="Discovered" value={job.pages_discovered} icon="travel_explore" />
        <Counter label="Crawled" value={job.pages_crawled} icon="check_circle" />
        <Counter label="Failed" value={job.pages_failed} icon="error" />
        <Counter label="Skipped" value={job.pages_skipped} icon="skip_next" />
      </div>

      {/* Completion summary */}
      {job.status === "completed" && (
        <div className="rounded-xl border border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/30 p-4 flex flex-col gap-2">
          <div className="flex items-center gap-2 text-green-700 dark:text-green-400 font-semibold">
            <span className="material-symbols-outlined" style={{ fontSize: 20 }}>
              check_circle
            </span>
            Crawl completed — {job.pages_crawled} page{job.pages_crawled !== 1 ? "s" : ""} crawled
          </div>
          <Button
            variant="outline"
            size="sm"
            className="self-start"
            onClick={() => router.push("/knowledge")}
          >
            <span className="material-symbols-outlined text-sm mr-1">open_in_new</span>
            View Sources
          </Button>
        </div>
      )}

      {/* Error banner */}
      {job.status === "failed" && (
        <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 flex flex-col gap-1">
          <div className="flex items-center gap-2 text-destructive font-semibold">
            <span className="material-symbols-outlined" style={{ fontSize: 20 }}>
              error
            </span>
            Crawl failed
          </div>
          {job.error_message && (
            <p className="text-sm text-destructive/80 font-mono break-all">
              {job.error_message}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
