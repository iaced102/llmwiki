"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

type CrawlJob = {
  id: string;
  start_url: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  pages_crawled: number;
  created_at: string;
};

type PaginatedCrawlJobs = {
  items: CrawlJob[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
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

function formatDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function CrawlJobsList() {
  const router = useRouter();
  const [jobs, setJobs] = useState<CrawlJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Initial load
  useEffect(() => {
    let cancelled = false;
    api<PaginatedCrawlJobs>("/api/crawl-jobs?page=1&page_size=20")
      .then((data) => {
        if (!cancelled) {
          setJobs(data.items);
          setError(null);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("Failed to load crawl jobs.");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [setJobs, setLoading, setError]);

  // Poll while any job is active
  useEffect(() => {
    const hasActive = jobs.some(
      (j) => j.status === "running" || j.status === "pending"
    );
    if (!hasActive) return;
    const id = setInterval(() => {
      api<PaginatedCrawlJobs>("/api/crawl-jobs?page=1&page_size=20")
        .then((data) => setJobs(data.items))
        .catch(() => {});
    }, 3000);
    return () => clearInterval(id);
  }, [jobs, setJobs]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <span className="material-symbols-outlined animate-spin text-3xl text-primary">
          progress_activity
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg flex items-center gap-2">
        <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
          error
        </span>
        {error}
      </p>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-muted-foreground">
        <span className="material-symbols-outlined text-4xl">travel_explore</span>
        <p className="text-sm">No crawl jobs yet. Start one above.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col divide-y divide-border rounded-xl border overflow-hidden">
      {jobs.map((job) => (
        <button
          key={job.id}
          type="button"
          onClick={() => router.push(`/knowledge/crawl-jobs/${job.id}`)}
          className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 px-4 py-3 text-left hover:bg-muted/50 transition-colors"
        >
          {/* URL */}
          <span className="flex-1 text-sm font-mono truncate text-foreground min-w-0">
            {job.start_url}
          </span>

          {/* Status badge */}
          <StatusBadge status={job.status} />

          {/* Pages crawled */}
          <span className="flex items-center gap-1 text-xs text-muted-foreground whitespace-nowrap">
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
              check_circle
            </span>
            {job.pages_crawled} page{job.pages_crawled !== 1 ? "s" : ""}
          </span>

          {/* Created at */}
          <span className="text-xs text-muted-foreground whitespace-nowrap">
            {formatDate(job.created_at)}
          </span>
        </button>
      ))}
    </div>
  );
}
