"use client";

import { use } from "react";
import { useRouter } from "next/navigation";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { CrawlJobDetail } from "@/components/knowledge/crawl-job-detail";

type Props = {
  params: Promise<{ id: string }>;
};

export default function CrawlJobDetailPage({ params }: Props) {
  const { id } = use(params);
  const router = useRouter();

  return (
    <>
      <PageHeader
        title="Crawl Job"
        description="Monitor the progress of your site crawl."
        action={
          <Button
            variant="outline"
            onClick={() => router.push("/knowledge")}
          >
            <span className="material-symbols-outlined text-base mr-1">arrow_back</span>
            Back to Knowledge Base
          </Button>
        }
      />
      <CrawlJobDetail jobId={id} />
    </>
  );
}
