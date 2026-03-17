"use client";

import { memo } from "react";

import { Badge } from "@/components/ui/badge";

import type { DeliberationEntryRead, EntryType } from "./types";
import { ENTRY_TYPE_LABELS, formatConfidence, formatTimestamp } from "./types";

const ENTRY_TYPE_VARIANTS: Record<
  EntryType,
  "default" | "outline" | "accent" | "success" | "warning" | "danger"
> = {
  thesis: "accent",
  antithesis: "danger",
  evidence: "default",
  question: "outline",
  vote: "warning",
  rebuttal: "accent",
  synthesis: "success",
};

const PHASE_LABELS: Record<string, string> = {
  debate: "Debate",
  discussion: "Discussion",
  verification: "Verification",
  synthesis: "Synthesis",
};

type EntryCardProps = {
  entry: DeliberationEntryRead;
  agentNameById?: Record<string, string>;
  userNameById?: Record<string, string>;
  isThreaded?: boolean;
  onReply?: (entry: DeliberationEntryRead) => void;
  highlightId?: string | null;
};

function EntryCardImpl({
  entry,
  agentNameById,
  userNameById,
  isThreaded = false,
  onReply,
  highlightId,
}: EntryCardProps) {
  const typeLabel = ENTRY_TYPE_LABELS[entry.entry_type] ?? entry.entry_type;
  const typeVariant = ENTRY_TYPE_VARIANTS[entry.entry_type] ?? "default";
  const phaseLabel = PHASE_LABELS[entry.phase] ?? entry.phase;
  const isHighlighted = highlightId === entry.id;

  const authorName = entry.agent_id
    ? (agentNameById?.[entry.agent_id] ?? "Agent")
    : entry.user_id
      ? (userNameById?.[entry.user_id] ?? "User")
      : "Unknown";

  const isAgent = !!entry.agent_id;
  const authorInitials = authorName
    .split(/[\s_-]+/)
    .slice(0, 2)
    .map((w) => w.charAt(0).toUpperCase())
    .join("");

  return (
    <div
      id={`entry-${entry.id}`}
      className={[
        "relative rounded-lg border p-3 transition-colors",
        isThreaded ? "ml-6 border-dashed" : "",
        isHighlighted
          ? "border-[var(--accent-strong)] bg-[var(--accent-soft)]"
          : "border-[var(--border)] bg-[var(--surface)]",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {/* Thread connector line */}
      {isThreaded && (
        <div className="absolute -left-3 top-0 h-full w-px bg-[var(--border)]" />
      )}

      {/* Header: avatar + author + badges + timestamp */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          {/* Avatar circle */}
          <div
            className={[
              "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[10px] font-bold",
              isAgent
                ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                : "bg-[var(--surface-muted)] text-[var(--text-muted)]",
            ].join(" ")}
          >
            {authorInitials}
          </div>

          {/* Author name */}
          <span className="text-sm font-medium text-[var(--text)]">
            {authorName}
          </span>

          {/* Entry type badge */}
          <Badge variant={typeVariant} className="text-[10px]">
            {typeLabel}
          </Badge>

          {/* Phase badge (subtle) */}
          <span className="rounded-md bg-[var(--surface-muted)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--text-quiet)]">
            {phaseLabel}
          </span>
        </div>

        {/* Timestamp + sequence */}
        <div className="flex shrink-0 items-center gap-2 text-[11px] text-[var(--text-quiet)]">
          <span className="font-mono">#{entry.sequence}</span>
          <time dateTime={entry.created_at}>
            {formatTimestamp(entry.created_at)}
          </time>
        </div>
      </div>

      {/* Position row (if present) */}
      {entry.position && (
        <div className="mt-2 flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
            Position:
          </span>
          <span className="text-xs text-[var(--text)]">{entry.position}</span>
          {entry.confidence != null && (
            <span className="ml-auto text-xs font-medium text-[var(--text-muted)]">
              Confidence: {formatConfidence(entry.confidence)}
            </span>
          )}
        </div>
      )}

      {/* Confidence bar (visual, if present and no position row) */}
      {entry.confidence != null && !entry.position && (
        <div className="mt-2 flex items-center gap-2">
          <span className="text-xs text-[var(--text-muted)]">
            Confidence: {formatConfidence(entry.confidence)}
          </span>
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--surface-muted)]">
            <div
              className="h-full rounded-full bg-[var(--accent-strong)] transition-all"
              style={{ width: `${Math.round(entry.confidence * 100)}%` }}
            />
          </div>
        </div>
      )}

      {/* Content */}
      <div className="mt-2 text-sm leading-relaxed text-[var(--text)] whitespace-pre-wrap">
        {entry.content}
      </div>

      {/* References */}
      {entry.references && entry.references.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--text-quiet)]">
            Refs:
          </span>
          {entry.references.map((ref, i) => (
            <span
              key={`${ref}-${i}`}
              className="rounded-md bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-muted)]"
            >
              {ref.length > 40 ? `${ref.slice(0, 37)}…` : ref}
            </span>
          ))}
        </div>
      )}

      {/* Metadata summary (collapsed) */}
      {entry.entry_metadata && Object.keys(entry.entry_metadata).length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[10px] font-medium uppercase tracking-wide text-[var(--text-quiet)] hover:text-[var(--text-muted)]">
            Metadata ({Object.keys(entry.entry_metadata).length}{" "}
            {Object.keys(entry.entry_metadata).length === 1
              ? "field"
              : "fields"}
            )
          </summary>
          <div className="mt-1 rounded-md bg-[var(--surface-muted)] p-2">
            <pre className="overflow-x-auto text-[11px] text-[var(--text-muted)]">
              {JSON.stringify(entry.entry_metadata, null, 2)}
            </pre>
          </div>
        </details>
      )}

      {/* Footer: reply action + parent reference */}
      <div className="mt-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          {entry.parent_entry_id && (
            <a
              href={`#entry-${entry.parent_entry_id}`}
              className="inline-flex items-center gap-1 text-[10px] text-[var(--text-quiet)] hover:text-[var(--accent-strong)]"
            >
              <svg
                className="h-3 w-3"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6"
                />
              </svg>
              In reply to #{entry.parent_entry_id.slice(0, 8)}
            </a>
          )}
        </div>

        {onReply && (
          <button
            type="button"
            onClick={() => onReply(entry)}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-[var(--text-quiet)] transition hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
          >
            <svg
              className="h-3 w-3"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6"
              />
            </svg>
            Reply
          </button>
        )}
      </div>
    </div>
  );
}

export const EntryCard = memo(EntryCardImpl);
EntryCard.displayName = "EntryCard";
