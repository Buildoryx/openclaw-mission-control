/**
 * Custom React Query hooks for the token usage API endpoints.
 *
 * These hooks call the backend `/api/v1/token-usage/*` endpoints directly
 * using the shared `customFetch` mutator so auth headers, base URL, and
 * error handling are consistent with the generated API client.
 *
 * We hand-write these instead of waiting for orval regeneration so the
 * feature can ship immediately.  Once `make api-gen` is run against the
 * updated backend, these can be replaced with the generated equivalents.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type {
  UseQueryOptions,
  UseMutationOptions,
  QueryKey,
} from "@tanstack/react-query";

import { customFetch, ApiError } from "@/api/mutator";

// ── Response types (mirror backend schemas) ──────────────────────────────────

export type TokenUsageSummaryKpis = {
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_cost_microcents: number;
  total_events: number;
  total_sessions: number;
  avg_tokens_per_session: number;
  avg_tokens_per_event: number;
};

export type TokenUsageDailyRollup = {
  date: string;
  model: string;
  model_provider: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  input_cost_microcents: number;
  output_cost_microcents: number;
  total_cost_microcents: number;
  event_count: number;
  session_count: number;
};

export type TokenUsageModelBreakdown = {
  model: string;
  model_provider: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  input_cost_microcents: number;
  output_cost_microcents: number;
  total_cost_microcents: number;
  event_count: number;
  session_count: number;
  share_pct: number;
};

export type TokenUsageByKind = {
  event_kind: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  event_count: number;
  share_pct: number;
};

export type UsageRangeKey = "24h" | "3d" | "5d" | "7d" | "14d" | "1m";

export type TokenUsageDashboard = {
  range: UsageRangeKey;
  generated_at: string;
  kpis: TokenUsageSummaryKpis;
  daily_rollup: TokenUsageDailyRollup[];
  by_model: TokenUsageModelBreakdown[];
  by_kind: TokenUsageByKind[];
};

export type TokenUsageEventRead = {
  id: string;
  organization_id: string;
  gateway_id: string | null;
  agent_id: string | null;
  board_id: string | null;
  session_id: string | null;
  model: string;
  model_provider: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  input_cost_microcents: number | null;
  output_cost_microcents: number | null;
  total_cost_microcents: number | null;
  event_kind: string;
  note: string | null;
  event_at: string;
  created_at: string;
};

export type TokenUsageRecentEvents = {
  total: number;
  events: TokenUsageEventRead[];
};

export type TokenUsageIngestItem = {
  model: string;
  model_provider?: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens?: number | null;
  input_cost_microcents?: number | null;
  output_cost_microcents?: number | null;
  total_cost_microcents?: number | null;
  event_kind?: string;
  session_id?: string | null;
  gateway_id?: string | null;
  agent_id?: string | null;
  board_id?: string | null;
  note?: string | null;
  event_at?: string | null;
};

export type TokenUsageIngestRequest = {
  events: TokenUsageIngestItem[];
};

export type TokenUsageIngestResponse = {
  ok: boolean;
  ingested: number;
};

// ── Internal fetch wrapper types ─────────────────────────────────────────────

type FetchResponse<T> = {
  data: T;
  status: number;
  headers: Headers;
};

// ── Query key factories ──────────────────────────────────────────────────────

export const tokenUsageKeys = {
  all: ["token-usage"] as const,
  dashboard: (range: UsageRangeKey, boardId?: string | null) =>
    ["token-usage", "dashboard", range, boardId ?? "all"] as const,
  events: (
    range: UsageRangeKey,
    boardId?: string | null,
    limit?: number,
    offset?: number,
  ) =>
    [
      "token-usage",
      "events",
      range,
      boardId ?? "all",
      limit ?? 50,
      offset ?? 0,
    ] as const,
};

// ── Dashboard hook ───────────────────────────────────────────────────────────

type DashboardParams = {
  range?: UsageRangeKey;
  boardId?: string | null;
};

type DashboardQueryResponse = FetchResponse<TokenUsageDashboard>;

export function useTokenUsageDashboard<
  TData = DashboardQueryResponse,
  TError = ApiError,
>(
  params?: DashboardParams,
  options?: Partial<
    UseQueryOptions<DashboardQueryResponse, TError, TData, QueryKey>
  >,
) {
  const range = params?.range ?? "5d";
  const boardId = params?.boardId ?? null;

  const queryParams = new URLSearchParams({ range_key: range });
  if (boardId) {
    queryParams.set("board_id", boardId);
  }

  return useQuery<DashboardQueryResponse, TError, TData>({
    queryKey: tokenUsageKeys.dashboard(range, boardId),
    queryFn: () =>
      customFetch<DashboardQueryResponse>(
        `/api/v1/token-usage/dashboard?${queryParams.toString()}`,
        { method: "GET" },
      ),
    ...options,
  });
}

// ── Events list hook ─────────────────────────────────────────────────────────

type EventsParams = {
  range?: UsageRangeKey;
  boardId?: string | null;
  limit?: number;
  offset?: number;
};

type EventsQueryResponse = FetchResponse<TokenUsageRecentEvents>;

export function useTokenUsageEvents<
  TData = EventsQueryResponse,
  TError = ApiError,
>(
  params?: EventsParams,
  options?: Partial<
    UseQueryOptions<EventsQueryResponse, TError, TData, QueryKey>
  >,
) {
  const range = params?.range ?? "5d";
  const boardId = params?.boardId ?? null;
  const limit = params?.limit ?? 50;
  const offset = params?.offset ?? 0;

  const queryParams = new URLSearchParams({
    range_key: range,
    limit: String(limit),
    offset: String(offset),
  });
  if (boardId) {
    queryParams.set("board_id", boardId);
  }

  return useQuery<EventsQueryResponse, TError, TData>({
    queryKey: tokenUsageKeys.events(range, boardId, limit, offset),
    queryFn: () =>
      customFetch<EventsQueryResponse>(
        `/api/v1/token-usage/events?${queryParams.toString()}`,
        { method: "GET" },
      ),
    ...options,
  });
}

// ── Ingest mutation hook ─────────────────────────────────────────────────────

type IngestMutationResponse = FetchResponse<TokenUsageIngestResponse>;

export function useTokenUsageIngest<TError = ApiError, TContext = unknown>(
  options?: Partial<
    UseMutationOptions<
      IngestMutationResponse,
      TError,
      TokenUsageIngestRequest,
      TContext
    >
  >,
) {
  const queryClient = useQueryClient();

  return useMutation<
    IngestMutationResponse,
    TError,
    TokenUsageIngestRequest,
    TContext
  >({
    mutationFn: (payload) =>
      customFetch<IngestMutationResponse>("/api/v1/token-usage/ingest", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: (...args) => {
      // Invalidate dashboard and events queries so they refetch with new data
      queryClient.invalidateQueries({ queryKey: tokenUsageKeys.all });
      options?.onSuccess?.(...args);
    },
    ...options,
  });
}

// ── Helper: convert micro-cents to USD ───────────────────────────────────────

/**
 * Convert a micro-cent value to USD.
 * 1 USD = 100 cents = 100_000_000 micro-cents.
 */
export function microCentsToUsd(microCents: number): number {
  return microCents / 100_000_000;
}

/**
 * Format a micro-cent value as a short currency string.
 */
export function formatMicroCentsAsUsd(microCents: number): string {
  const usd = microCentsToUsd(microCents);
  if (usd < 0.01) {
    return `$${usd.toFixed(4)}`;
  }
  if (usd < 1) {
    return `$${usd.toFixed(3)}`;
  }
  return `$${usd.toFixed(2)}`;
}

// ── Query key helpers for external invalidation ──────────────────────────────

export function getTokenUsageDashboardQueryKey(
  range: UsageRangeKey = "5d",
  boardId?: string | null,
) {
  return tokenUsageKeys.dashboard(range, boardId);
}

export function getTokenUsageEventsQueryKey(
  range: UsageRangeKey = "5d",
  boardId?: string | null,
  limit?: number,
  offset?: number,
) {
  return tokenUsageKeys.events(range, boardId, limit, offset);
}
