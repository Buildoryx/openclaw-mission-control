/**
 * Deliberation module types and API client utilities.
 *
 * These types mirror the backend schemas defined in
 * `backend/app/schemas/deliberation.py` and `backend/app/schemas/episodic_memory.py`.
 *
 * Until `make api-gen` is run against the live backend, these hand-written
 * types serve as the contract for the frontend components.
 */

import { customFetch } from "@/api/mutator";

// ---------------------------------------------------------------------------
// Deliberation types
// ---------------------------------------------------------------------------

export type DeliberationStatus =
  | "created"
  | "debating"
  | "discussing"
  | "verifying"
  | "synthesizing"
  | "concluded"
  | "abandoned";

export type EntryType =
  | "thesis"
  | "antithesis"
  | "evidence"
  | "question"
  | "vote"
  | "rebuttal"
  | "synthesis";

export type ConsensusLevel = "unanimous" | "majority" | "contested" | "split";

export type DeliberationRead = {
  id: string;
  board_id: string;
  topic: string;
  status: DeliberationStatus;
  initiated_by_agent_id: string | null;
  synthesizer_agent_id: string | null;
  trigger_reason: string | null;
  task_id: string | null;
  parent_deliberation_id: string | null;
  max_turns: number;
  outcome_changed: boolean;
  confidence_delta: number | null;
  duration_ms: number | null;
  approval_id: string | null;
  entry_count: number;
  has_synthesis: boolean;
  created_at: string;
  concluded_at: string | null;
  updated_at: string;
};

export type DeliberationCreate = {
  topic: string;
  trigger_reason?: string | null;
  task_id?: string | null;
  max_turns?: number | null;
};

export type DeliberationEntryRead = {
  id: string;
  deliberation_id: string;
  sequence: number;
  phase: string;
  entry_type: EntryType;
  agent_id: string | null;
  user_id: string | null;
  position: string | null;
  confidence: number | null;
  content: string;
  parent_entry_id: string | null;
  references: string[] | null;
  entry_metadata: Record<string, unknown> | null;
  created_at: string;
};

export type DeliberationEntryCreate = {
  content: string;
  phase: string;
  entry_type: EntryType;
  position?: string | null;
  confidence?: number | null;
  parent_entry_id?: string | null;
  references?: string[] | null;
  entry_metadata?: Record<string, unknown> | null;
};

export type DeliberationSynthesisRead = {
  id: string;
  deliberation_id: string;
  synthesized_by_agent_id: string | null;
  content: string;
  consensus_level: ConsensusLevel;
  key_points: string[] | null;
  dissenting_views: string[] | null;
  confidence: number;
  tags: string[] | null;
  promoted_to_memory: boolean;
  board_memory_id: string | null;
  created_at: string;
};

export type DeliberationSynthesisCreate = {
  content: string;
  consensus_level: ConsensusLevel;
  key_points?: string[] | null;
  dissenting_views?: string[] | null;
  confidence: number;
  tags?: string[] | null;
};

export type EpisodicMemoryRead = {
  id: string;
  board_id: string;
  pattern_type: string;
  topic: string | null;
  deliberation_id: string | null;
  pattern_summary: string;
  pattern_details: Record<string, unknown> | null;
  outcome_positive: boolean;
  confidence_range: Record<string, unknown> | null;
  occurrence_count: number;
  success_rate: number | null;
  reliability_score: number | null;
  created_at: string;
  updated_at: string;
};

export type AgentTrackRecord = {
  agent_id: string;
  board_id: string;
  total_positions: number;
  accepted_positions: number;
  accuracy_rate: number | null;
  strongest_areas: string[] | null;
  weakest_areas: string[] | null;
  pattern_count: number;
};

// ---------------------------------------------------------------------------
// Paginated response wrapper (mirrors backend LimitOffsetPage)
// ---------------------------------------------------------------------------

export type PaginatedResponse<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

// ---------------------------------------------------------------------------
// API response wrapper (customFetch envelope)
// ---------------------------------------------------------------------------

type ApiResponse<T> = {
  data: T;
  status: number;
  headers: Headers;
};

// ---------------------------------------------------------------------------
// Status display helpers
// ---------------------------------------------------------------------------

export const DELIBERATION_STATUS_LABELS: Record<DeliberationStatus, string> = {
  created: "Created",
  debating: "Debating",
  discussing: "Discussing",
  verifying: "Verifying",
  synthesizing: "Synthesizing",
  concluded: "Concluded",
  abandoned: "Abandoned",
};

export const DELIBERATION_STATUS_VARIANTS: Record<
  DeliberationStatus,
  "default" | "outline" | "accent" | "success" | "warning" | "danger"
> = {
  created: "outline",
  debating: "accent",
  discussing: "accent",
  verifying: "warning",
  synthesizing: "warning",
  concluded: "success",
  abandoned: "danger",
};

export const ENTRY_TYPE_LABELS: Record<EntryType, string> = {
  thesis: "Thesis",
  antithesis: "Antithesis",
  evidence: "Evidence",
  question: "Question",
  vote: "Vote",
  rebuttal: "Rebuttal",
  synthesis: "Synthesis",
};

export const CONSENSUS_LABELS: Record<ConsensusLevel, string> = {
  unanimous: "Unanimous",
  majority: "Majority",
  contested: "Contested",
  split: "Split",
};

export const CONSENSUS_VARIANTS: Record<
  ConsensusLevel,
  "default" | "success" | "warning" | "danger" | "accent"
> = {
  unanimous: "success",
  majority: "accent",
  contested: "warning",
  split: "danger",
};

export const isTerminalStatus = (status: DeliberationStatus): boolean =>
  status === "concluded" || status === "abandoned";

export const formatDuration = (ms: number | null): string => {
  if (ms === null || ms <= 0) return "—";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
};

export const formatTimestamp = (iso: string): string => {
  const date = new Date(iso);
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
};

export const formatConfidence = (value: number | null): string => {
  if (value === null) return "—";
  return `${Math.round(value * 100)}%`;
};

// ---------------------------------------------------------------------------
// API client functions
// ---------------------------------------------------------------------------

const API_PREFIX = "/api/v1";

export async function listDeliberations(
  boardId: string,
  opts?: { status?: DeliberationStatus; limit?: number; offset?: number },
): Promise<PaginatedResponse<DeliberationRead>> {
  const params = new URLSearchParams();
  if (opts?.status) params.set("status", opts.status);
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  if (opts?.offset != null) params.set("offset", String(opts.offset));
  const qs = params.toString();
  const url = `${API_PREFIX}/boards/${boardId}/deliberations${qs ? `?${qs}` : ""}`;
  const res = await customFetch<
    ApiResponse<PaginatedResponse<DeliberationRead>>
  >(url, { method: "GET" });
  return res.data;
}

export async function createDeliberation(
  boardId: string,
  payload: DeliberationCreate,
): Promise<DeliberationRead> {
  const url = `${API_PREFIX}/boards/${boardId}/deliberations`;
  const res = await customFetch<ApiResponse<DeliberationRead>>(url, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return res.data;
}

export async function getDeliberation(
  boardId: string,
  deliberationId: string,
): Promise<DeliberationRead> {
  const url = `${API_PREFIX}/boards/${boardId}/deliberations/${deliberationId}`;
  const res = await customFetch<ApiResponse<DeliberationRead>>(url, {
    method: "GET",
  });
  return res.data;
}

export async function listEntries(
  boardId: string,
  deliberationId: string,
  opts?: { phase?: string; limit?: number; offset?: number },
): Promise<PaginatedResponse<DeliberationEntryRead>> {
  const params = new URLSearchParams();
  if (opts?.phase) params.set("phase", opts.phase);
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  if (opts?.offset != null) params.set("offset", String(opts.offset));
  const qs = params.toString();
  const url = `${API_PREFIX}/boards/${boardId}/deliberations/${deliberationId}/entries${qs ? `?${qs}` : ""}`;
  const res = await customFetch<
    ApiResponse<PaginatedResponse<DeliberationEntryRead>>
  >(url, { method: "GET" });
  return res.data;
}

export async function createEntry(
  boardId: string,
  deliberationId: string,
  payload: DeliberationEntryCreate,
): Promise<DeliberationEntryRead> {
  const url = `${API_PREFIX}/boards/${boardId}/deliberations/${deliberationId}/entries`;
  const res = await customFetch<ApiResponse<DeliberationEntryRead>>(url, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return res.data;
}

export async function advanceDeliberation(
  boardId: string,
  deliberationId: string,
): Promise<DeliberationRead> {
  const url = `${API_PREFIX}/boards/${boardId}/deliberations/${deliberationId}/advance`;
  const res = await customFetch<ApiResponse<DeliberationRead>>(url, {
    method: "POST",
  });
  return res.data;
}

export async function abandonDeliberation(
  boardId: string,
  deliberationId: string,
  reason?: string,
): Promise<DeliberationRead> {
  const url = `${API_PREFIX}/boards/${boardId}/deliberations/${deliberationId}/abandon`;
  const body = reason ? JSON.stringify({ reason }) : undefined;
  const res = await customFetch<ApiResponse<DeliberationRead>>(url, {
    method: "POST",
    body,
  });
  return res.data;
}

export async function getSynthesis(
  boardId: string,
  deliberationId: string,
): Promise<DeliberationSynthesisRead> {
  const url = `${API_PREFIX}/boards/${boardId}/deliberations/${deliberationId}/synthesis`;
  const res = await customFetch<ApiResponse<DeliberationSynthesisRead>>(url, {
    method: "GET",
  });
  return res.data;
}

export async function createSynthesis(
  boardId: string,
  deliberationId: string,
  payload: DeliberationSynthesisCreate,
): Promise<DeliberationSynthesisRead> {
  const url = `${API_PREFIX}/boards/${boardId}/deliberations/${deliberationId}/synthesis`;
  const res = await customFetch<ApiResponse<DeliberationSynthesisRead>>(url, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return res.data;
}

export async function promoteSynthesis(
  boardId: string,
  deliberationId: string,
): Promise<DeliberationRead> {
  const url = `${API_PREFIX}/boards/${boardId}/deliberations/${deliberationId}/synthesis/promote`;
  const res = await customFetch<ApiResponse<DeliberationRead>>(url, {
    method: "POST",
  });
  return res.data;
}

export function deliberationStreamUrl(
  boardId: string,
  deliberationId: string,
  since?: string,
): string {
  const params = new URLSearchParams();
  if (since) params.set("since", since);
  const qs = params.toString();
  return `${API_PREFIX}/boards/${boardId}/deliberations/${deliberationId}/stream${qs ? `?${qs}` : ""}`;
}
