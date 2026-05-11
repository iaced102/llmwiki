"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type CrawlJob = {
  id: string;
  start_url: string;
  domain: string;
  status: string;
  pages_discovered: number;
  discovered_urls: string[];
};

type Props = {
  jobId: string;
};

export function CrawlDiscoverView({ jobId }: Props) {
  const router = useRouter();
  const [job, setJob] = useState<CrawlJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  // URL selection state
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  // Initial load
  useEffect(() => {
    let cancelled = false;
    api<CrawlJob>(`/api/crawl-jobs/${jobId}`)
      .then((data) => {
        if (!cancelled) {
          setJob(data);
          setLoading(false);
          // Pre-select all discovered URLs when discovery completes
          if (data.status === "discovered") {
            setSelected(new Set(data.discovered_urls));
          }
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          if (err instanceof ApiError && err.status === 404) setNotFound(true);
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [jobId]);

  // Poll while discovering
  useEffect(() => {
    if (!job || job.status !== "running" || notFound) return;
    const id = setInterval(() => {
      api<CrawlJob>(`/api/crawl-jobs/${jobId}`)
        .then((data) => {
          setJob(data);
          if (data.status === "discovered") {
            setSelected(new Set(data.discovered_urls));
          }
        })
        .catch(() => {});
    }, 3000);
    return () => clearInterval(id);
  }, [job, notFound, jobId]);

  // Filtered URL list
  const filteredUrls = useMemo(() => {
    if (!job?.discovered_urls) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return job.discovered_urls;
    return job.discovered_urls.filter((u) => u.toLowerCase().includes(q));
  }, [job?.discovered_urls, filter]);

  const allFilteredSelected = filteredUrls.length > 0 && filteredUrls.every((u) => selected.has(u));

  const toggleAll = () => {
    if (allFilteredSelected) {
      setSelected((prev) => {
        const next = new Set(prev);
        filteredUrls.forEach((u) => next.delete(u));
        return next;
      });
    } else {
      setSelected((prev) => {
        const next = new Set(prev);
        filteredUrls.forEach((u) => next.add(u));
        return next;
      });
    }
  };

  const toggleUrl = (url: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  };

  const handleStart = async () => {
    if (!job || selected.size === 0) return;
    setStarting(true);
    setStartError(null);
    try {
      const newJob = await api<{ id: string }>(`/api/crawl-jobs/${job.id}/start`, {
        method: "POST",
        body: { selected_urls: Array.from(selected) },
      });
      router.push(`/knowledge/crawl-jobs/${newJob.id}`);
    } catch (err) {
      setStartError(
        err instanceof ApiError ? err.message : "Failed to start crawl."
      );
      setStarting(false);
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

  const isDiscovering = job.status === "running" || job.status === "pending";
  const isDiscovered = job.status === "discovered";

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-3 flex-wrap">
          {isDiscovering ? (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold text-white bg-[#ca8a04]">
              <span className="material-symbols-outlined animate-spin" style={{ fontSize: 12 }}>
                progress_activity
              </span>
              Discovering...
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold text-white bg-[#16a34a]">
              <span className="material-symbols-outlined" style={{ fontSize: 12 }}>check_circle</span>
              Discovery complete
            </span>
          )}
          <span className="text-sm text-muted-foreground font-mono break-all">
            {job.start_url}
          </span>
        </div>
        <p className="text-xs text-muted-foreground mt-1">
          {isDiscovering
            ? `Found ${job.pages_discovered} URLs so far — still scanning...`
            : `Found ${job.discovered_urls.length} URLs. Select which ones to crawl and ingest.`}
        </p>
      </div>

      {/* URL selection — only shown when discovered */}
      {isDiscovered && (
        <>
          {/* Filter + Check All */}
          <div className="flex items-center gap-3">
            <Input
              placeholder="Filter by URL..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="flex-1"
            />
            <Button
              variant="outline"
              size="sm"
              onClick={toggleAll}
              disabled={filteredUrls.length === 0}
            >
              {allFilteredSelected ? (
                <>
                  <span className="material-symbols-outlined text-sm mr-1">deselect</span>
                  Deselect All
                </>
              ) : (
                <>
                  <span className="material-symbols-outlined text-sm mr-1">select_all</span>
                  Select All
                </>
              )}
            </Button>
          </div>

          {/* Selection summary */}
          <p className="text-xs text-muted-foreground -mt-3">
            {selected.size} of {job.discovered_urls.length} selected
            {filter && ` (showing ${filteredUrls.length} filtered)`}
          </p>

          {/* URL list */}
          <div className="rounded-xl border overflow-hidden">
            <div className="max-h-[480px] overflow-y-auto divide-y divide-border">
              {filteredUrls.length === 0 ? (
                <div className="flex items-center justify-center py-10 text-muted-foreground text-sm">
                  No URLs match your filter.
                </div>
              ) : (
                filteredUrls.map((url) => (
                  <label
                    key={url}
                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/50 cursor-pointer transition-colors"
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(url)}
                      onChange={() => toggleUrl(url)}
                      className="accent-primary w-4 h-4 shrink-0"
                    />
                    <span className="text-xs font-mono text-foreground truncate min-w-0">
                      {url}
                    </span>
                  </label>
                ))
              )}
            </div>
          </div>

          {startError && (
            <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg flex items-center gap-2">
              <span className="material-symbols-outlined" style={{ fontSize: 16 }}>error</span>
              {startError}
            </p>
          )}

          {/* Start button */}
          <div className="flex justify-end">
            <Button
              onClick={handleStart}
              disabled={selected.size === 0 || starting}
              className="bg-primary text-primary-foreground hover:bg-primary/90"
            >
              {starting ? (
                <span className="flex items-center gap-2">
                  <span className="material-symbols-outlined animate-spin text-sm">
                    progress_activity
                  </span>
                  Starting...
                </span>
              ) : (
                <>
                  <span className="material-symbols-outlined text-base mr-1">rocket_launch</span>
                  Start Crawl ({selected.size} page{selected.size !== 1 ? "s" : ""})
                </>
              )}
            </Button>
          </div>
        </>
      )}

      {/* Discovering — live counter */}
      {isDiscovering && (
        <div className="flex flex-col items-center gap-3 py-10 text-muted-foreground">
          <span className="material-symbols-outlined animate-spin text-5xl text-primary">
            travel_explore
          </span>
          <p className="text-sm">
            Scanning <span className="font-semibold text-foreground">{job.domain}</span> for pages...
          </p>
          <p className="text-2xl font-bold tabular-nums text-foreground">
            {job.pages_discovered}
          </p>
          <p className="text-xs">URLs discovered</p>
        </div>
      )}
    </div>
  );
}
