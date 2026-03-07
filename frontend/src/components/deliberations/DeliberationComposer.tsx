"use client";

import { memo, useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import type { DeliberationRead, EntryType } from "./types";
import { ENTRY_TYPE_LABELS, isTerminalStatus } from "./types";

// ---------------------------------------------------------------------------
// Phase → allowed entry types mapping
// ---------------------------------------------------------------------------

const PHASE_ENTRY_TYPES: Record<string, EntryType[]> = {
  debate: ["thesis", "antithesis", "rebuttal"],
  discussion: ["evidence", "question", "rebuttal"],
  verification: ["vote", "evidence"],
  synthesis: ["synthesis"],
};

const PHASE_OPTIONS: { value: string; label: string }[] = [
  { value: "debate", label: "Debate" },
  { value: "discussion", label: "Discussion" },
  { value: "verification", label: "Verification" },
];

// Map deliberation status to the default phase for new entries
const STATUS_TO_DEFAULT_PHASE: Record<string, string> = {
  created: "debate",
  debating: "debate",
  discussing: "discussion",
  verifying: "verification",
  synthesizing: "synthesis",
};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type DeliberationComposerProps = {
  deliberation: DeliberationRead;
  boardId: string;
  onSubmit: (entry: {
    content: string;
    phase: string;
    entry_type: EntryType;
    position: string | null;
    confidence: number | null;
    parent_entry_id: string | null;
    references: string[] | null;
  }) => Promise<boolean>;
  isSending?: boolean;
  disabled?: boolean;
  replyTo?: { id: string; sequence: number; authorName: string } | null;
  onCancelReply?: () => void;
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

function DeliberationComposerImpl({
  deliberation,
  boardId: _boardId,
  onSubmit,
  isSending = false,
  disabled = false,
  replyTo,
  onCancelReply,
}: DeliberationComposerProps) {
  const defaultPhase =
    STATUS_TO_DEFAULT_PHASE[deliberation.status] ?? "debate";
  const isTerminal = isTerminalStatus(deliberation.status);

  const [content, setContent] = useState("");
  const [phase, setPhase] = useState(defaultPhase);
  const [entryType, setEntryType] = useState<EntryType>(
    () => (PHASE_ENTRY_TYPES[defaultPhase] ?? ["thesis"])[0] ?? "thesis",
  );
  const [position, setPosition] = useState("");
  const [confidence, setConfidence] = useState<string>("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Available entry types for the selected phase
  const availableTypes = PHASE_ENTRY_TYPES[phase] ?? ["thesis"];

  // Reset entry type when phase changes
  const handlePhaseChange = useCallback(
    (nextPhase: string) => {
      setPhase(nextPhase);
      const nextTypes = PHASE_ENTRY_TYPES[nextPhase] ?? ["thesis"];
      if (!nextTypes.includes(entryType)) {
        setEntryType(nextTypes[0] ?? "thesis");
      }
    },
    [entryType],
  );

  const handleSubmit = useCallback(async () => {
    const trimmed = content.trim();
    if (!trimmed || isSending || disabled || isTerminal) return;

    const confidenceValue =
      confidence.trim() !== ""
        ? Math.max(0, Math.min(1, parseFloat(confidence)))
        : null;

    const ok = await onSubmit({
      content: trimmed,
      phase,
      entry_type: entryType,
      position: position.trim() || null,
      confidence: Number.isNaN(confidenceValue) ? null : confidenceValue,
      parent_entry_id: replyTo?.id ?? null,
      references: null,
    });

    if (ok) {
      setContent("");
      setPosition("");
      setConfidence("");
      onCancelReply?.();
    }
  }, [
    content,
    isSending,
    disabled,
    isTerminal,
    confidence,
    onSubmit,
    phase,
    entryType,
    position,
    replyTo,
    onCancelReply,
  ]);

  // Don't render if deliberation is in a terminal state
  if (isTerminal) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] p-4 text-center text-sm text-[var(--text-muted)]">
        This deliberation has{" "}
        {deliberation.status === "concluded" ? "concluded" : "been abandoned"}.
        No further entries can be submitted.
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 space-y-3">
      {/* Reply indicator */}
      {replyTo && (
        <div className="flex items-center justify-between rounded-lg bg-[var(--surface-muted)] px-3 py-2">
          <span className="text-xs text-[var(--text-muted)]">
            Replying to{" "}
            <span className="font-medium text-[var(--text)]">
              {replyTo.authorName}
            </span>{" "}
            <span className="font-mono text-[var(--text-quiet)]">
              #{replyTo.sequence}
            </span>
          </span>
          <button
            type="button"
            onClick={onCancelReply}
            className="rounded-md p-1 text-[var(--text-quiet)] transition hover:bg-[var(--surface)] hover:text-[var(--text)]"
            aria-label="Cancel reply"
          >
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
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>
      )}

      {/* Phase + entry type selectors */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Phase selector */}
        <div className="flex items-center gap-1.5">
          <label
            htmlFor="composer-phase"
            className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]"
          >
            Phase
          </label>
          <select
            id="composer-phase"
            value={phase}
            onChange={(e) => handlePhaseChange(e.target.value)}
            disabled={isSending || disabled}
            className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs text-[var(--text)] focus:border-[var(--accent-strong)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-strong)] disabled:opacity-50"
          >
            {PHASE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        {/* Entry type selector */}
        <div className="flex items-center gap-1.5">
          <label
            htmlFor="composer-type"
            className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]"
          >
            Type
          </label>
          <select
            id="composer-type"
            value={entryType}
            onChange={(e) => setEntryType(e.target.value as EntryType)}
            disabled={isSending || disabled}
            className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs text-[var(--text)] focus:border-[var(--accent-strong)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-strong)] disabled:opacity-50"
          >
            {availableTypes.map((t) => (
              <option key={t} value={t}>
                {ENTRY_TYPE_LABELS[t] ?? t}
              </option>
            ))}
          </select>
        </div>

        {/* Toggle advanced fields */}
        <button
          type="button"
          onClick={() => setShowAdvanced((prev) => !prev)}
          className="ml-auto inline-flex items-center gap-1 text-[11px] text-[var(--text-quiet)] transition hover:text-[var(--text-muted)]"
        >
          <svg
            className={[
              "h-3 w-3 transition-transform",
              showAdvanced ? "rotate-180" : "",
            ].join(" ")}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M19 9l-7 7-7-7"
            />
          </svg>
          {showAdvanced ? "Less" : "More"}
        </button>
      </div>

      {/* Advanced fields: position + confidence */}
      {showAdvanced && (
        <div className="flex flex-wrap items-end gap-3">
          {/* Position */}
          <div className="flex-1 min-w-[180px]">
            <label
              htmlFor="composer-position"
              className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]"
            >
              Position (stance)
            </label>
            <input
              id="composer-position"
              type="text"
              value={position}
              onChange={(e) => setPosition(e.target.value)}
              placeholder="e.g. Support, Oppose, Neutral…"
              disabled={isSending || disabled}
              className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs text-[var(--text)] placeholder:text-[var(--text-quiet)] focus:border-[var(--accent-strong)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-strong)] disabled:opacity-50"
            />
          </div>

          {/* Confidence */}
          <div className="w-32">
            <label
              htmlFor="composer-confidence"
              className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]"
            >
              Confidence (0–1)
            </label>
            <input
              id="composer-confidence"
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={confidence}
              onChange={(e) => setConfidence(e.target.value)}
              placeholder="0.8"
              disabled={isSending || disabled}
              className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs text-[var(--text)] placeholder:text-[var(--text-quiet)] focus:border-[var(--accent-strong)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-strong)] disabled:opacity-50"
            />
          </div>
        </div>
      )}

      {/* Content textarea */}
      <Textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder={
          entryType === "thesis"
            ? "Present your thesis — what is your position and why?"
            : entryType === "antithesis"
              ? "Present a counter-argument…"
              : entryType === "evidence"
                ? "Share supporting evidence or data…"
                : entryType === "question"
                  ? "Ask a clarifying question…"
                  : entryType === "vote"
                    ? "Cast your vote with reasoning…"
                    : entryType === "rebuttal"
                      ? "Respond to a specific argument…"
                      : "Write your contribution…"
        }
        className="min-h-[100px]"
        disabled={isSending || disabled}
        onKeyDown={(e) => {
          if (e.key !== "Enter") return;
          if (e.nativeEvent.isComposing) return;
          if (e.shiftKey) return;
          e.preventDefault();
          void handleSubmit();
        }}
      />

      {/* Footer: submit + entry count indicator */}
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-[var(--text-quiet)]">
          {deliberation.entry_count} / {deliberation.max_turns} entries
          {deliberation.entry_count >= deliberation.max_turns && (
            <span className="ml-1 font-medium text-[var(--warning)]">
              (limit reached)
            </span>
          )}
        </span>

        <Button
          onClick={() => void handleSubmit()}
          disabled={
            isSending ||
            disabled ||
            !content.trim() ||
            deliberation.entry_count >= deliberation.max_turns
          }
          size="sm"
        >
          {isSending ? (
            <span className="inline-flex items-center gap-1.5">
              <svg
                className="h-3.5 w-3.5 animate-spin"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
              Submitting…
            </span>
          ) : (
            `Submit ${ENTRY_TYPE_LABELS[entryType] ?? entryType}`
          )}
        </Button>
      </div>
    </div>
  );
}

export const DeliberationComposer = memo(DeliberationComposerImpl);
DeliberationComposer.displayName = "DeliberationComposer";
