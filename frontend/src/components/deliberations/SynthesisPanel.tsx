"use client";

import { memo, useCallback, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import type {
  ConsensusLevel,
  DeliberationRead,
  DeliberationSynthesisRead,
} from "./types";
import {
  CONSENSUS_LABELS,
  CONSENSUS_VARIANTS,
  formatConfidence,
  formatTimestamp,
  promoteSynthesis,
} from "./types";

type SynthesisPanelProps = {
  deliberation: DeliberationRead;
  synthesis: DeliberationSynthesisRead | null;
  boardId: string;
  agentNameById?: Record<string, string>;
  onPromoted?: (deliberation: DeliberationRead) => void;
  canWrite?: boolean;
};

function SynthesisPanelImpl({
  deliberation,
  synthesis,
  boardId,
  agentNameById,
  onPromoted,
  canWrite = true,
}: SynthesisPanelProps) {
  const [isPromoting, setIsPromoting] = useState(false);
  const [promoteError, setPromoteError] = useState<string | null>(null);
  const [isExpanded, setIsExpanded] = useState(true);

  const handlePromote = useCallback(async () => {
    if (!synthesis || synthesis.promoted_to_memory || isPromoting) return;
    setIsPromoting(true);
    setPromoteError(null);
    try {
      const updated = await promoteSynthesis(boardId, deliberation.id);
      onPromoted?.(updated);
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to promote synthesis";
      setPromoteError(message);
    } finally {
      setIsPromoting(false);
    }
  }, [synthesis, isPromoting, boardId, deliberation.id, onPromoted]);

  if (!synthesis) {
    if (deliberation.status === "concluded") {
      return (
        <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] p-4 text-center text-sm text-[var(--text-muted)]">
          Deliberation concluded but no synthesis was recorded.
        </div>
      );
    }
    if (deliberation.status === "synthesizing") {
      return (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
          <div className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
            <svg
              className="h-4 w-4 animate-spin"
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
            Awaiting synthesis from the synthesizer agent…
          </div>
        </div>
      );
    }
    return null;
  }

  const consensusLabel =
    CONSENSUS_LABELS[synthesis.consensus_level as ConsensusLevel] ??
    synthesis.consensus_level;
  const consensusVariant =
    CONSENSUS_VARIANTS[synthesis.consensus_level as ConsensusLevel] ?? "default";

  const synthesizerName =
    synthesis.synthesized_by_agent_id && agentNameById
      ? agentNameById[synthesis.synthesized_by_agent_id] ?? "Agent"
      : null;

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] overflow-hidden">
      {/* Header */}
      <button
        type="button"
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-[var(--surface-muted)]"
      >
        <div className="flex items-center gap-2">
          <svg
            className="h-5 w-5 text-[var(--success)]"
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
          <h3 className="text-sm font-semibold text-[var(--text)]">
            Synthesis
          </h3>
          <Badge variant={consensusVariant}>{consensusLabel}</Badge>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs font-medium text-[var(--text-muted)]">
            Confidence: {formatConfidence(synthesis.confidence)}
          </span>

          {/* Promoted badge */}
          {synthesis.promoted_to_memory && (
            <Badge variant="success" className="text-[10px]">
              In Memory
            </Badge>
          )}

          {/* Expand/collapse icon */}
          <svg
            className={[
              "h-4 w-4 text-[var(--text-quiet)] transition-transform",
              isExpanded ? "rotate-180" : "",
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
        </div>
      </button>

      {/* Body (collapsible) */}
      {isExpanded && (
        <div className="border-t border-[var(--border)] px-4 py-4 space-y-4">
          {/* Confidence bar */}
          <div className="flex items-center gap-3">
            <span className="text-xs font-medium text-[var(--text-muted)] shrink-0">
              Confidence
            </span>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--surface-muted)]">
              <div
                className={[
                  "h-full rounded-full transition-all",
                  synthesis.confidence >= 0.7
                    ? "bg-[var(--success)]"
                    : synthesis.confidence >= 0.4
                      ? "bg-[var(--warning)]"
                      : "bg-[var(--danger)]",
                ].join(" ")}
                style={{
                  width: `${Math.round(synthesis.confidence * 100)}%`,
                }}
              />
            </div>
            <span className="text-xs font-semibold text-[var(--text)] shrink-0">
              {formatConfidence(synthesis.confidence)}
            </span>
          </div>

          {/* Content */}
          <div className="text-sm leading-relaxed text-[var(--text)] whitespace-pre-wrap">
            {synthesis.content}
          </div>

          {/* Key points */}
          {synthesis.key_points && synthesis.key_points.length > 0 && (
            <div>
              <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                Key Points
              </h4>
              <ul className="space-y-1">
                {synthesis.key_points.map((point, i) => (
                  <li
                    key={`kp-${i}`}
                    className="flex items-start gap-2 text-sm text-[var(--text)]"
                  >
                    <svg
                      className="mt-0.5 h-4 w-4 shrink-0 text-[var(--success)]"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M5 13l4 4L19 7"
                      />
                    </svg>
                    <span>{point}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Dissenting views */}
          {synthesis.dissenting_views &&
            synthesis.dissenting_views.length > 0 && (
              <div>
                <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                  Dissenting Views
                </h4>
                <ul className="space-y-1">
                  {synthesis.dissenting_views.map((view, i) => (
                    <li
                      key={`dv-${i}`}
                      className="flex items-start gap-2 text-sm text-[var(--text)]"
                    >
                      <svg
                        className="mt-0.5 h-4 w-4 shrink-0 text-[var(--warning)]"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth={2}
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                        />
                      </svg>
                      <span>{view}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

          {/* Tags */}
          {synthesis.tags && synthesis.tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {synthesis.tags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full bg-[var(--surface-muted)] px-2.5 py-0.5 text-[11px] font-medium text-[var(--text-muted)]"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}

          {/* Meta footer */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border)] pt-3">
            <div className="flex items-center gap-3 text-xs text-[var(--text-quiet)]">
              {synthesizerName && (
                <span>
                  Synthesized by{" "}
                  <span className="font-medium text-[var(--text-muted)]">
                    {synthesizerName}
                  </span>
                </span>
              )}
              <time dateTime={synthesis.created_at}>
                {formatTimestamp(synthesis.created_at)}
              </time>
            </div>

            {/* Actions */}
            <div className="flex items-center gap-2">
              {/* Board memory reference */}
              {synthesis.promoted_to_memory && synthesis.board_memory_id && (
                <span className="text-[10px] font-mono text-[var(--text-quiet)]">
                  Memory: {synthesis.board_memory_id.slice(0, 8)}…
                </span>
              )}

              {/* Promote button */}
              {!synthesis.promoted_to_memory && canWrite && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handlePromote}
                  disabled={isPromoting}
                  className="text-xs"
                >
                  {isPromoting ? (
                    <>
                      <svg
                        className="mr-1.5 h-3 w-3 animate-spin"
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
                      Promoting…
                    </>
                  ) : (
                    <>
                      <svg
                        className="mr-1.5 h-3 w-3"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth={2}
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"
                        />
                      </svg>
                      Promote to Board Memory
                    </>
                  )}
                </Button>
              )}
            </div>
          </div>

          {/* Promote error */}
          {promoteError && (
            <div className="rounded-md bg-[color:rgba(180,35,24,0.1)] px-3 py-2 text-xs text-[var(--danger)]">
              {promoteError}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export const SynthesisPanel = memo(SynthesisPanelImpl);
SynthesisPanel.displayName = "SynthesisPanel";
