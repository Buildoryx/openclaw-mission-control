"use client";

import { memo } from "react";

import { Badge } from "@/components/ui/badge";

import type { DeliberationRead } from "./types";
import {
  DELIBERATION_STATUS_LABELS,
  DELIBERATION_STATUS_VARIANTS,
  formatConfidence,
  formatDuration,
  formatTimestamp,
  isTerminalStatus,
} from "./types";

type DeliberationCardProps = {
  deliberation: DeliberationRead;
  onClick?: (deliberation: DeliberationRead) => void;
  agentNameById?: Record<string, string>;
};

function DeliberationCardImpl({
  deliberation,
  onClick,
  agentNameById,
}: DeliberationCardProps) {
  const statusLabel =
    DELIBERATION_STATUS_LABELS[deliberation.status] ?? deliberation.status;
  const statusVariant =
    DELIBERATION_STATUS_VARIANTS[deliberation.status] ?? "default";
  const isTerminal = isTerminalStatus(deliberation.status);

  const initiatorName =
    deliberation.initiated_by_agent_id && agentNameById
      ? agentNameById[deliberation.initiated_by_agent_id] ?? "Agent"
      : null;

  return (
    <button
      type="button"
      onClick={() => onClick?.(deliberation)}
      className="group w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 text-left transition hover:border-[var(--border-strong)] hover:shadow-sm"
    >
      {/* Header: topic + status badge */}
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-sm font-semibold text-[var(--text)] line-clamp-2 leading-snug">
          {deliberation.topic}
        </h3>
        <Badge variant={statusVariant} className="shrink-0">
          {statusLabel}
        </Badge>
      </div>

      {/* Meta row */}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--text-muted)]">
        {/* Entry count */}
        <span className="inline-flex items-center gap-1">
          <svg
            className="h-3.5 w-3.5"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
            />
          </svg>
          {deliberation.entry_count}{" "}
          {deliberation.entry_count === 1 ? "entry" : "entries"}
        </span>

        {/* Max turns */}
        <span>
          / {deliberation.max_turns} max
        </span>

        {/* Duration (if concluded) */}
        {isTerminal && deliberation.duration_ms != null && (
          <span className="inline-flex items-center gap-1">
            <svg
              className="h-3.5 w-3.5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            {formatDuration(deliberation.duration_ms)}
          </span>
        )}

        {/* Synthesis indicator */}
        {deliberation.has_synthesis && (
          <span className="inline-flex items-center gap-1 text-[var(--success)]">
            <svg
              className="h-3.5 w-3.5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            Synthesized
          </span>
        )}

        {/* Confidence delta */}
        {deliberation.confidence_delta != null && (
          <span
            className={
              deliberation.confidence_delta >= 0
                ? "text-[var(--success)]"
                : "text-[var(--danger)]"
            }
          >
            Δ {deliberation.confidence_delta >= 0 ? "+" : ""}
            {formatConfidence(deliberation.confidence_delta)}
          </span>
        )}
      </div>

      {/* Footer: trigger + timestamp + initiator */}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--text-quiet)]">
        <div className="flex items-center gap-2">
          {deliberation.trigger_reason && (
            <span className="rounded-md bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono text-[10px]">
              {deliberation.trigger_reason}
            </span>
          )}
          {initiatorName && (
            <span>
              by <span className="font-medium text-[var(--text-muted)]">{initiatorName}</span>
            </span>
          )}
        </div>
        <time dateTime={deliberation.created_at}>
          {formatTimestamp(deliberation.created_at)}
        </time>
      </div>

      {/* Task link indicator */}
      {deliberation.task_id && (
        <div className="mt-2 text-[10px] text-[var(--text-quiet)] font-mono">
          Task: {deliberation.task_id.slice(0, 8)}…
        </div>
      )}
    </button>
  );
}

export const DeliberationCard = memo(DeliberationCardImpl);
DeliberationCard.displayName = "DeliberationCard";
