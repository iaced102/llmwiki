"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { CrawlJobDetail } from "@/components/knowledge/crawl-job-detail";
import { CrawlDiscoverView } from "@/components/knowledge/crawl-discover-view";
import { api } from "@/lib/api";

type CrawlJobMeta = {
  id: string;
  mode: string;
  status: string;
};

type Props = {
  params: Promise<{ id: string }>;
};

export default function CrawlJobDetailPage({ params }: Props) {
  const { id } = use(params);
  const router = useRouter();
  const [meta, setMeta] = useState<CrawlJobMeta | null>(null);

  // Fetch just enough to decide which view to render
  useEffect(() => {
    let cancelled = false;
    api<CrawlJobMeta>(`/api/crawl-jobs/${id}`)
      .then((data) => { if (!cancelled) setMeta(data); })
      .catch(() => { if (!cancelled) setMeta({ id, mode: "crawl", status: "unknown" }); });
    return () => { cancelled = true; };
  }, [id]);

  // Re-check mode/status while in discover phase (pending→running→discovered)
  useEffect(() => {
    if (!meta) return;
    if (meta.mode !== "discover") return;
    if (meta.status === "discovered" || meta.status === "cancelled" || meta.status === "failed") return;
    const interval = setInterval(() => {
      api<CrawlJobMeta>(`/api/crawl-jobs/${id}`)
        .then((data) => setMeta(data))
        .catch(() => {});
    }, 3000);
    return () => clearInterval(interval);
  }, [id, meta]);

  // Discover mode: show URL selection view until user starts crawl
  const isDiscoverMode =
    meta?.mode === "discover" &&
    (meta?.status === "pending" || meta?.status === "running" || meta?.status === "discovered");

  const title = isDiscoverMode ? "Discover Pages" : "Crawl Job";
  const description = isDiscoverMode
    ? "Review discovered pages and select which ones to crawl."
    : "Monitor the progress of your site crawl.";

  return (
    <>
      <PageHeader
        title={title}
        description={description}
        action={
          <Button variant="outline" onClick={() => router.push("/knowledge")}>
            <span className="material-symbols-outlined text-base mr-1">arrow_back</span>
            Back to Knowledge Base
          </Button>
        }
      />
      {meta === null ? (
        <div className="flex items-center justify-center py-20">
          <span className="material-symbols-outlined animate-spin text-4xl text-primary">
            progress_activity
          </span>
        </div>
      ) : isDiscoverMode ? (
        <CrawlDiscoverView jobId={id} />
      ) : (
        <CrawlJobDetail jobId={id} />
      )}
    </>
  );
}
