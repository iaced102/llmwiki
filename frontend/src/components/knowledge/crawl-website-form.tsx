"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type KnowledgeType = {
  id: string;
  slug: string;
  name: string;
  color: string;
};

type CrawlJobResponse = {
  id: string;
  start_url: string;
  domain: string;
  status: string;
};

type Props = {
  types: KnowledgeType[];
};

export function CrawlWebsiteForm({ types }: Props) {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [typeId, setTypeId] = useState("");
  const [urlPattern, setUrlPattern] = useState("");
  const [maxPages, setMaxPages] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [urlError, setUrlError] = useState<string | null>(null);
  const [networkError, setNetworkError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim() || submitting) return;

    setSubmitting(true);
    setUrlError(null);
    setNetworkError(null);

    try {
      const body: Record<string, string | number> = { start_url: url.trim() };
      if (typeId) body.knowledge_type_id = typeId;
      if (urlPattern.trim()) body.url_pattern = urlPattern.trim();
      if (maxPages.trim()) {
        const n = parseInt(maxPages.trim(), 10);
        if (!isNaN(n) && n > 0) body.max_pages = n;
      }

      const job = await api<CrawlJobResponse>("/api/crawl-jobs", {
        method: "POST",
        body,
      });

      router.push(`/knowledge/crawl-jobs/${job.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422) {
          // Extract validation error detail
          const data = err.data as { detail?: string | { msg: string }[] };
          if (Array.isArray(data?.detail)) {
            setUrlError(data.detail.map((d) => d.msg).join("; "));
          } else {
            setUrlError(typeof data?.detail === "string" ? data.detail : "Invalid URL.");
          }
        } else {
          setNetworkError("Something went wrong. Please try again.");
        }
      } else {
        setNetworkError("Something went wrong. Please try again.");
      }
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      {/* URL input */}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="crawl-url">Website URL</Label>
        <Input
          id="crawl-url"
          type="url"
          placeholder="https://example.com"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            if (urlError) setUrlError(null);
            if (networkError) setNetworkError(null);
          }}
          disabled={submitting}
          className={urlError ? "border-destructive focus-visible:ring-destructive" : ""}
          autoComplete="off"
        />
        {urlError && (
          <p className="text-destructive text-xs flex items-center gap-1">
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
              error
            </span>
            {urlError}
          </p>
        )}
      </div>

      {/* Knowledge Type dropdown */}
      <div className="flex flex-col gap-1.5">
        <Label>Knowledge Type</Label>
        <Select value={typeId} onValueChange={(v) => setTypeId(v ?? "")}>
          <SelectTrigger className="bg-background w-full">
            {typeId ? (
              (() => {
                const t = types.find((x) => x.id === typeId);
                return t ? (
                  <div className="flex items-center gap-2">
                    <span
                      className="w-2.5 h-2.5 rounded-full"
                      style={{ backgroundColor: t.color }}
                    />
                    <span>{t.name}</span>
                  </div>
                ) : (
                  <SelectValue placeholder="Select type (optional)" />
                );
              })()
            ) : (
              <SelectValue placeholder="Select type (optional)" />
            )}
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">None</SelectItem>
            {types.map((t) => (
              <SelectItem key={t.id} value={t.id}>
                <div className="flex items-center gap-2">
                  <span
                    className="w-2.5 h-2.5 rounded-full"
                    style={{ backgroundColor: t.color }}
                  />
                  {t.name}
                </div>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* URL Pattern */}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="crawl-pattern">
          URL Pattern <span className="text-muted-foreground font-normal">(optional regex)</span>
        </Label>
        <Input
          id="crawl-pattern"
          type="text"
          placeholder="e.g. /docs/.* or \.html$"
          value={urlPattern}
          onChange={(e) => setUrlPattern(e.target.value)}
          disabled={submitting}
          autoComplete="off"
        />
        <p className="text-xs text-muted-foreground">Only URLs matching this regex will be discovered and crawled.</p>
      </div>

      {/* Max Pages */}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="crawl-max-pages">
          Max Pages <span className="text-muted-foreground font-normal">(optional)</span>
        </Label>
        <Input
          id="crawl-max-pages"
          type="number"
          min={1}
          placeholder="e.g. 100"
          value={maxPages}
          onChange={(e) => setMaxPages(e.target.value)}
          disabled={submitting}
          className="w-40"
        />
        <p className="text-xs text-muted-foreground">Stop after discovering this many pages. Leave empty for unlimited.</p>
      </div>

      {/* Network error */}
      {networkError && (
        <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg flex items-center gap-2">
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
            error
          </span>
          {networkError}
        </p>
      )}

      {/* Submit button */}
      <div className="flex justify-end">
        <Button
          type="submit"
          disabled={!url.trim() || submitting}
          className="bg-primary text-primary-foreground hover:bg-primary/90"
        >
          {submitting ? (
            <span className="flex items-center gap-2">
              <span className="material-symbols-outlined animate-spin text-sm">
                progress_activity
              </span>
              Starting crawl...
            </span>
          ) : (
            <>
              <span className="material-symbols-outlined text-base mr-1">travel_explore</span>
              Start Crawl
            </>
          )}
        </Button>
      </div>
    </form>
  );
}
