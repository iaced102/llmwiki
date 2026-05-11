"use client";

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";

const PROVIDERS = [
  { value: "google", label: "Google (Gemini)" },
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "litellm", label: "LiteLLM (100+ models)" },
];

type Props = {
  title: string;
  description: string;
  icon: string;
  capability: "llm" | "vision";
  provider: string;
  model: string;
  apiKey: string;
  baseUrl: string;
  onProviderChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onBaseUrlChange: (value: string) => void;
};

export function ProviderConfigCard({
  title,
  description,
  icon,
  capability,
  provider,
  model,
  apiKey,
  baseUrl,
  onProviderChange,
  onModelChange,
  onApiKeyChange,
  onBaseUrlChange,
}: Props) {
  const [fetchedModels, setFetchedModels] = useState<{ name: string; dimension: number | null }[]>([]);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState("");

  const isLitellm = provider === "litellm";

  async function fetchModels() {
    setFetching(true);
    setFetchError("");
    try {
      const params = new URLSearchParams({ capability });
      if (baseUrl) params.set("base_url", baseUrl);
      if (apiKey && !apiKey.includes("•")) params.set("api_key", apiKey);
      const data = await api<{ models: { name: string; dimension: number | null }[]; error?: string }>(
        `/api/settings/litellm-models?${params}`
      );
      if (data.error) {
        setFetchError(data.error);
        setFetchedModels([]);
      } else {
        setFetchedModels(data.models);
        if (data.models.length > 0 && !data.models.find((m) => m.name === model)) {
          onModelChange(data.models[0].name);
        }
      }
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : "Failed to fetch models");
    } finally {
      setFetching(false);
    }
  }

  return (
    <div className="bg-card rounded-xl p-6 border border-border shadow-sahara">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
          <span className="material-symbols-outlined text-primary text-base">
            {icon}
          </span>
        </div>
        <div>
          <h3 className="text-base font-semibold text-foreground">{title}</h3>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Provider */}
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs">Provider</Label>
          <Select value={provider} onValueChange={(v) => v && onProviderChange(v)}>
            <SelectTrigger className="bg-background">
              <SelectValue placeholder="Select provider" />
            </SelectTrigger>
            <SelectContent>
              {PROVIDERS.map((p) => (
                <SelectItem key={p.value} value={p.value}>
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Model ID */}
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs">Model ID</Label>
          {isLitellm && fetchedModels.length > 0 ? (
            <Select value={model} onValueChange={(v) => onModelChange(v ?? "")}>
              <SelectTrigger className="bg-background">
                <SelectValue placeholder="Select model" />
              </SelectTrigger>
              <SelectContent>
                {fetchedModels.map((m) => (
                  <SelectItem key={m.name} value={m.name}>
                    {m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Input
              value={model}
              onChange={(e) => onModelChange(e.target.value)}
              placeholder={isLitellm ? "Fetch models or type manually" : "e.g. gpt-4o"}
              className="bg-background"
            />
          )}
        </div>

        {/* API Key */}
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs">API Key</Label>
          <Input
            type={apiKey.includes("•") ? "text" : "password"}
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
            onFocus={() => {
              if (apiKey.includes("•")) onApiKeyChange("");
            }}
            placeholder="sk-..."
            className="bg-background"
          />
        </div>
      </div>

      {/* LiteLLM: Base URL + fetch button */}
      {isLitellm && (
        <div className="mt-4 flex flex-col gap-2">
          <div className="flex items-end gap-2">
            <div className="flex-1 flex flex-col gap-1.5">
              <Label className="text-xs">Base URL</Label>
              <Input
                value={baseUrl}
                onChange={(e) => onBaseUrlChange(e.target.value)}
                placeholder="http://localhost:4000"
                className="bg-background"
              />
            </div>
            <button
              type="button"
              onClick={fetchModels}
              disabled={fetching || !baseUrl}
              className="h-9 px-3 rounded-lg border border-border bg-background text-xs hover:bg-accent disabled:opacity-50 flex items-center gap-1.5 whitespace-nowrap"
            >
              <span className={`material-symbols-outlined text-sm ${fetching ? "animate-spin" : ""}`}>
                {fetching ? "progress_activity" : "refresh"}
              </span>
              {fetching ? "Fetching…" : "Fetch models"}
            </button>
          </div>
          {fetchError && <p className="text-xs text-destructive">{fetchError}</p>}
          {!fetchError && fetchedModels.length > 0 && (
            <p className="text-xs text-muted-foreground">{fetchedModels.length} model(s) available</p>
          )}
        </div>
      )}
    </div>
  );
}