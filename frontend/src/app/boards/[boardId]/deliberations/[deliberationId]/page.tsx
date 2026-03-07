"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DeliberationComposer } from "@/components/deliberations/DeliberationComposer";
import { EntryCard } from "@/components/deliberations/EntryCard";
import { SynthesisPanel } from "@/components/deliberations/SynthesisPanel";
import type {
  DeliberationEntryRead,
  DeliberationRead,
  DeliberationSynthesisRead,
  EntryType,
} from "@/components/deliberations/types";
import {
  DELIBERATION_STATUS_LABELS,
  DELIBERATION_STATUS_VARIANTS,
  advanceDeliberation,
  abandonDeliberation,
  createEntry,
  formatDuration,
  formatTimestamp,
  getDeliberation,
  getSynthesis,
  isTerminalStatus,
  listEntries,
} from "@/components/deliberations/types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 4000;
const ENTRIES_PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export default function DeliberationDetailPage() {
  const router = useRouter();
  const params = useParams();
  const boardId = params?.boardId as string;
  const deliberationId = params?.deliberationId as string;

  // ----- State -----------------------------------------------------------

  const [deliberation, setDeliberation] = useState<DeliberationRead | null>(
    null,
  );
  const [entries, setEntries] = useState<DeliberationEntryRead[]>([]);
  const [synthesis, setSynthesis] = useState<DeliberationSynthesisRead | null>(
    null,
  );
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSendingEntry, setIsSendingEntry] = useState(false);
  const [entryError, setEntryError] = useState<string | null>(null);

  // Phase advancement / abandon
  const [isAdvancing, setIsAdvancing] = useState(false);
  const [isAbandoning, setIsAbandoning] = useState(false);
  const [isAbandonDialogOpen, setIsAbandonDialogOpen] = useState(false);
  const [abandonReason, setAbandonReason] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  // Reply thread
  const [replyTo, setReplyTo] = useState<{
    id: string;
    sequence: number;
    authorName: string;
  } | null>(null);

  // Phase filter
  const [phaseFilter, setPhaseFilter] = useState<string | null>(null);

  // Auto-scroll
  const entriesEndRef = useRef<HTMLDivElement | null>(null);
  const isMountedRef = useRef(true);
  const previousEntryCountRef = useRef(0);

  useEffect(() => {
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // ----- Data fetching ---------------------------------------------------

  const fetchDeliberation = useCallback(async () => {
    if (!boardId || !deliberationId) return;
    try {
      const result = await getDeliberation(boardId, deliberationId);
      if (!isMountedRef.current) return;
      setDeliberation(result);
      return result;
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      const message =
        err instanceof Error ? err.message : "Failed to load deliberation";
      setError(message);
      return null;
    }
  }, [boardId, deliberationId]);

  const fetchEntries = useCallback(async () => {
    if (!boardId || !deliberationId) return;
    try {
      const result = await listEntries(boardId, deliberationId, {
        phase: phaseFilter ?? undefined,
        limit: ENTRIES_PAGE_SIZE,
        offset: 0,
      });
      if (!isMountedRef.current) return;
      const items = result.items;
      setEntries(items);
      // Auto-scroll if new entries appeared
      if (items.length > previousEntryCountRef.current) {
        setTimeout(() => {
          entriesEndRef.current?.scrollIntoView({ behavior: "smooth" });
        }, 100);
      }
      previousEntryCountRef.current = items.length;
    } catch {
      // Entries fetch failure is non-fatal; deliberation header already shown
    }
  }, [boardId, deliberationId, phaseFilter]);

  const fetchSynthesis = useCallback(async () => {
    if (!boardId || !deliberationId) return;
    try {
      const result = await getSynthesis(boardId, deliberationId);
      if (!isMountedRef.current) return;
      setSynthesis(result);
    } catch {
      // 404 is expected if no synthesis exists yet
      if (isMountedRef.current) setSynthesis(null);
    }
  }, [boardId, deliberationId]);

  // Initial load
  useEffect(() => {
    setIsLoading(true);
    setError(null);

    void (async () => {
      const delib = await fetchDeliberation();
      if (!delib) {
        if (isMountedRef.current) setIsLoading(false);
        return;
      }
      await Promise.all([fetchEntries(), fetchSynthesis()]);
      if (isMountedRef.current) setIsLoading(false);
    })();
  }, [fetchDeliberation, fetchEntries, fetchSynthesis]);

  // Polling for live updates
  useEffect(() => {
    if (!deliberation || isTerminalStatus(deliberation.status)) return;

    const interval = setInterval(() => {
      void fetchDeliberation();
      void fetchEntries();
      void fetchSynthesis();
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [deliberation, fetchDeliberation, fetchEntries, fetchSynthesis]);

  // Re-fetch entries when phase filter changes
  useEffect(() => {
    if (!isLoading) {
      void fetchEntries();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phaseFilter]);

  // ----- Handlers --------------------------------------------------------

  const handleSubmitEntry = useCallback(
    async (entry: {
      content: string;
      phase: string;
      entry_type: EntryType;
      position: string | null;
      confidence: number | null;
      parent_entry_id: string | null;
      references: string[] | null;
    }): Promise<boolean> => {
      if (!boardId || !deliberationId || isSendingEntry) return false;
      setIsSendingEntry(true);
      setEntryError(null);
      try {
        await createEntry(boardId, deliberationId, {
          content: entry.content,
          phase: entry.phase,
          entry_type: entry.entry_type,
          position: entry.position ?? undefined,
          confidence: entry.confidence ?? undefined,
          parent_entry_id: entry.parent_entry_id ?? undefined,
          references: entry.references ?? undefined,
        });
        if (!isMountedRef.current) return false;
        // Refresh entries and deliberation after submission
        await Promise.all([fetchEntries(), fetchDeliberation()]);
        return true;
      } catch (err: unknown) {
        if (!isMountedRef.current) return false;
        const message =
          err instanceof Error ? err.message : "Failed to submit entry";
        setEntryError(message);
        return false;
      } finally {
        if (isMountedRef.current) setIsSendingEntry(false);
      }
    },
    [boardId, deliberationId, isSendingEntry, fetchEntries, fetchDeliberation],
  );

  const handleAdvance = useCallback(async () => {
    if (!boardId || !deliberationId || isAdvancing) return;
    setIsAdvancing(true);
    setActionError(null);
    try {
      const updated = await advanceDeliberation(boardId, deliberationId);
      if (!isMountedRef.current) return;
      setDeliberation(updated);
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      const message =
        err instanceof Error ? err.message : "Failed to advance phase";
      setActionError(message);
    } finally {
      if (isMountedRef.current) setIsAdvancing(false);
    }
  }, [boardId, deliberationId, isAdvancing]);

  const handleAbandon = useCallback(async () => {
    if (!boardId || !deliberationId || isAbandoning) return;
    setIsAbandoning(true);
    setActionError(null);
    try {
      const updated = await abandonDeliberation(
        boardId,
        deliberationId,
        abandonReason.trim() || undefined,
      );
      if (!isMountedRef.current) return;
      setDeliberation(updated);
      setIsAbandonDialogOpen(false);
      setAbandonReason("");
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      const message =
        err instanceof Error ? err.message : "Failed to abandon deliberation";
      setActionError(message);
    } finally {
      if (isMountedRef.current) setIsAbandoning(false);
    }
  }, [boardId, deliberationId, isAbandoning, abandonReason]);

  const handleReply = useCallback(
    (entry: DeliberationEntryRead) => {
      const authorName = entry.agent_id ? "Agent" : "User";
      setReplyTo({
        id: entry.id,
        sequence: entry.sequence,
        authorName,
      });
      // Scroll to composer
      setTimeout(() => {
        document
          .getElementById("deliberation-composer")
          ?.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 100);
    },
    [],
  );

  const handleSynthesisPromoted = useCallback(
    (updated: DeliberationRead) => {
      setDeliberation(updated);
      void fetchSynthesis();
    },
    [fetchSynthesis],
  );

  // ----- Computed --------------------------------------------------------

  const phases = useMemo(() => {
    const seen = new Set<string>();
    for (const entry of entries) {
      seen.add(entry.phase);
    }
    return Array.from(seen);
  }, [entries]);

  const groupedEntries = useMemo(() => {
    const threaded: DeliberationEntryRead[] = [];
    const childrenMap = new Map<string, DeliberationEntryRead[]>();

    for (const entry of entries) {
      if (entry.parent_entry_id) {
        const siblings = childrenMap.get(entry.parent_entry_id) ?? [];
        siblings.push(entry);
        childrenMap.set(entry.parent_entry_id, siblings);
      } else {
        threaded.push(entry);
      }
    }

    // Flatten: parent followed by children
    const result: { entry: DeliberationEntryRead; isThreaded: boolean }[] = [];
    for (const entry of threaded) {
      result.push({ entry, isThreaded: false });
      const children = childrenMap.get(entry.id);
      if (children) {
        for (const child of children) {
          result.push({ entry: child, isThreaded: true });
        }
      }
    }
    return result;
  }, [entries]);

  const canAdvance =
    deliberation &&
    !isTerminalStatus(deliberation.status) &&
    deliberation.status !== "created" &&
    deliberation.status !== "synthesizing";

  const canAbandon =
    deliberation && !isTerminalStatus(deliberation.status);

  const nextPhaseLabel = useMemo(() => {
    if (!deliberation) return "";
    const order = [
      "created",
      "debating",
      "discussing",
      "verifying",
      "synthesizing",
      "concluded",
    ];
    const idx = order.indexOf(deliberation.status);
    if (idx < 0 || idx >= order.length - 1) return "";
    const next = order[idx + 1];
    return (
      DELIBERATION_STATUS_LABELS[next as keyof typeof DELIBERATION_STATUS_LABELS] ??
      next
    );
  }, [deliberation]);

  // ----- Render ----------------------------------------------------------

  if (isLoading) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-12">
        <div className="flex items-center justify-center gap-2 text-sm text-[var(--text-muted)]">
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
          Loading deliberation…
        </div>
      </div>
    );
  }

  if (error && !deliberation) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-12">
        <div className="rounded-xl border border-[color:rgba(180,35,24,0.3)] bg-[color:rgba(180,35,24,0.06)] p-6 text-center">
          <p className="text-sm text-[var(--danger)]">{error}</p>
          <div className="mt-4 flex items-center justify-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => router.push(`/boards/${boardId}/deliberations`)}
            >
              ← Back to list
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setError(null);
                setIsLoading(true);
                void (async () => {
                  await fetchDeliberation();
                  await Promise.all([fetchEntries(), fetchSynthesis()]);
                  if (isMountedRef.current) setIsLoading(false);
                })();
              }}
            >
              Retry
            </Button>
          </div>
        </div>
      </div>
    );
  }

  if (!deliberation) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-12 text-center">
        <p className="text-sm text-[var(--text-muted)]">
          Deliberation not found.
        </p>
        <Button
          variant="outline"
          size="sm"
          className="mt-4"
          onClick={() => router.push(`/boards/${boardId}/deliberations`)}
        >
          ← Back to list
        </Button>
      </div>
    );
  }

  const statusLabel =
    DELIBERATION_STATUS_LABELS[deliberation.status] ?? deliberation.status;
  const statusVariant =
    DELIBERATION_STATUS_VARIANTS[deliberation.status] ?? "default";
  const isTerminal = isTerminalStatus(deliberation.status);

  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <button
            type="button"
            onClick={() =>
              router.push(`/boards/${boardId}/deliberations`)
            }
            className="mt-0.5 rounded-lg p-1.5 text-[var(--text-muted)] transition hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
            aria-label="Back to deliberations"
          >
            <svg
              className="h-5 w-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M15 19l-7-7 7-7"
              />
            </svg>
          </button>
          <div className="min-w-0">
            <h1 className="text-lg font-bold text-[var(--text)] break-words">
              {deliberation.topic}
            </h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--text-muted)]">
              <Badge variant={statusVariant}>{statusLabel}</Badge>
              <span>
                {deliberation.entry_count} /{" "}
                {deliberation.max_turns} entries
              </span>
              {deliberation.duration_ms != null && (
                <span>
                  Duration: {formatDuration(deliberation.duration_ms)}
                </span>
              )}
              {deliberation.trigger_reason && (
                <span className="rounded-md bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono text-[10px]">
                  {deliberation.trigger_reason}
                </span>
              )}
              <time
                dateTime={deliberation.created_at}
                className="text-[var(--text-quiet)]"
              >
                Started {formatTimestamp(deliberation.created_at)}
              </time>
              {deliberation.concluded_at && (
                <time
                  dateTime={deliberation.concluded_at}
                  className="text-[var(--text-quiet)]"
                >
                  Ended {formatTimestamp(deliberation.concluded_at)}
                </time>
              )}
            </div>
          </div>
        </div>

        {/* Action buttons */}
        {!isTerminal && (
          <div className="flex shrink-0 items-center gap-2">
            {canAdvance && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => void handleAdvance()}
                disabled={isAdvancing}
                className="text-xs"
              >
                {isAdvancing ? (
                  <span className="inline-flex items-center gap-1.5">
                    <svg
                      className="h-3 w-3 animate-spin"
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
                    Advancing…
                  </span>
                ) : (
                  <>Advance → {nextPhaseLabel}</>
                )}
              </Button>
            )}

            {canAbandon && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setIsAbandonDialogOpen(true)}
                className="text-xs text-[var(--danger)] border-[color:rgba(180,35,24,0.3)] hover:bg-[color:rgba(180,35,24,0.06)]"
              >
                Abandon
              </Button>
            )}
          </div>
        )}
      </div>

      {/* Action error */}
      {actionError && (
        <div className="mt-3 rounded-md bg-[color:rgba(180,35,24,0.1)] px-3 py-2 text-xs text-[var(--danger)]">
          {actionError}
        </div>
      )}

      {/* Task link */}
      {deliberation.task_id && (
        <div className="mt-3 rounded-lg bg-[var(--surface-muted)] px-3 py-2 text-xs text-[var(--text-muted)]">
          Linked to task:{" "}
          <span className="font-mono">
            {deliberation.task_id.slice(0, 8)}…
          </span>
          <button
            type="button"
            onClick={() =>
              router.push(
                `/boards/${boardId}?task=${deliberation.task_id}`,
              )
            }
            className="ml-2 text-[var(--accent-strong)] hover:underline"
          >
            View Task →
          </button>
        </div>
      )}

      {/* Phase filter tabs */}
      {phases.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-1.5 border-b border-[var(--border)] pb-3">
          <button
            type="button"
            onClick={() => setPhaseFilter(null)}
            className={[
              "rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-wider transition",
              phaseFilter === null
                ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                : "text-[var(--text-quiet)] hover:bg-[var(--surface-muted)] hover:text-[var(--text-muted)]",
            ].join(" ")}
          >
            All ({entries.length})
          </button>
          {phases.map((phase) => {
            const count = entries.filter((e) => e.phase === phase).length;
            const label =
              phase === "debate"
                ? "Debate"
                : phase === "discussion"
                  ? "Discussion"
                  : phase === "verification"
                    ? "Verification"
                    : phase === "synthesis"
                      ? "Synthesis"
                      : phase;
            return (
              <button
                key={phase}
                type="button"
                onClick={() =>
                  setPhaseFilter((prev) =>
                    prev === phase ? null : phase,
                  )
                }
                className={[
                  "rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-wider transition",
                  phaseFilter === phase
                    ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                    : "text-[var(--text-quiet)] hover:bg-[var(--surface-muted)] hover:text-[var(--text-muted)]",
                ].join(" ")}
              >
                {label} ({count})
              </button>
            );
          })}
        </div>
      )}

      {/* Entry transcript */}
      <div className="mt-4 space-y-3">
        {groupedEntries.length === 0 && !isLoading && (
          <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] p-6 text-center">
            <svg
              className="mx-auto h-8 w-8 text-[var(--text-quiet)]"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
              />
            </svg>
            <p className="mt-2 text-sm text-[var(--text-muted)]">
              {phaseFilter
                ? `No entries in the "${phaseFilter}" phase.`
                : "No entries yet. Be the first to contribute!"}
            </p>
          </div>
        )}

        {groupedEntries.map(({ entry, isThreaded }) => (
          <EntryCard
            key={entry.id}
            entry={entry}
            isThreaded={isThreaded}
            onReply={!isTerminal ? handleReply : undefined}
          />
        ))}

        {/* Scroll anchor */}
        <div ref={entriesEndRef} />
      </div>

      {/* Synthesis panel */}
      {(deliberation.has_synthesis ||
        deliberation.status === "synthesizing" ||
        deliberation.status === "concluded") && (
        <div className="mt-6">
          <SynthesisPanel
            deliberation={deliberation}
            synthesis={synthesis}
            boardId={boardId}
            onPromoted={handleSynthesisPromoted}
            canWrite={!isTerminal || deliberation.status === "concluded"}
          />
        </div>
      )}

      {/* Entry error */}
      {entryError && (
        <div className="mt-4 rounded-md bg-[color:rgba(180,35,24,0.1)] px-3 py-2 text-xs text-[var(--danger)]">
          {entryError}
          <button
            type="button"
            onClick={() => setEntryError(null)}
            className="ml-2 font-medium underline hover:no-underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Entry composer */}
      <div id="deliberation-composer" className="mt-6">
        <DeliberationComposer
          deliberation={deliberation}
          boardId={boardId}
          onSubmit={handleSubmitEntry}
          isSending={isSendingEntry}
          replyTo={replyTo}
          onCancelReply={() => setReplyTo(null)}
        />
      </div>

      {/* Abandon confirmation dialog */}
      {isAbandonDialogOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            onClick={() => setIsAbandonDialogOpen(false)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setIsAbandonDialogOpen(false);
            }}
            role="button"
            tabIndex={0}
            aria-label="Close dialog"
          />

          {/* Dialog panel */}
          <div className="relative z-10 w-full max-w-md rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-6 shadow-2xl">
            <h2 className="text-base font-bold text-[var(--text)]">
              Abandon Deliberation?
            </h2>
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              This action cannot be undone. The deliberation will be marked as
              abandoned and no further entries can be submitted.
            </p>

            {/* Reason */}
            <div className="mt-4">
              <label
                htmlFor="abandon-reason"
                className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]"
              >
                Reason (optional)
              </label>
              <input
                id="abandon-reason"
                type="text"
                value={abandonReason}
                onChange={(e) => setAbandonReason(e.target.value)}
                placeholder="Why is this deliberation being abandoned?"
                disabled={isAbandoning}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--text-quiet)] focus:border-[var(--accent-strong)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-strong)] disabled:opacity-50"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    void handleAbandon();
                  }
                }}
              />
            </div>

            {/* Actions */}
            <div className="mt-5 flex items-center justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setIsAbandonDialogOpen(false);
                  setAbandonReason("");
                }}
                disabled={isAbandoning}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={() => void handleAbandon()}
                disabled={isAbandoning}
                className="bg-[var(--danger)] text-white hover:opacity-90"
              >
                {isAbandoning ? (
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
                    Abandoning…
                  </span>
                ) : (
                  "Abandon Deliberation"
                )}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
