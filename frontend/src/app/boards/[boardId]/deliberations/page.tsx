"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { DeliberationCard } from "@/components/deliberations/DeliberationCard";
import type {
  DeliberationRead,
  DeliberationStatus,
} from "@/components/deliberations/types";
import {
  DELIBERATION_STATUS_LABELS,
  DELIBERATION_STATUS_VARIANTS,
  createDeliberation,
  listDeliberations,
} from "@/components/deliberations/types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STATUS_FILTERS: { value: DeliberationStatus | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "created", label: "Created" },
  { value: "debating", label: "Debating" },
  { value: "discussing", label: "Discussing" },
  { value: "verifying", label: "Verifying" },
  { value: "synthesizing", label: "Synthesizing" },
  { value: "concluded", label: "Concluded" },
  { value: "abandoned", label: "Abandoned" },
];

const PAGE_SIZE = 20;

// ---------------------------------------------------------------------------
// Stats bar helpers
// ---------------------------------------------------------------------------

type StatsBarEntry = {
  status: DeliberationStatus;
  count: number;
};

function computeStats(items: DeliberationRead[]): StatsBarEntry[] {
  const counts: Partial<Record<DeliberationStatus, number>> = {};
  for (const item of items) {
    counts[item.status] = (counts[item.status] ?? 0) + 1;
  }
  return Object.entries(counts)
    .map(([status, count]) => ({
      status: status as DeliberationStatus,
      count: count ?? 0,
    }))
    .sort((a, b) => b.count - a.count);
}

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export default function DeliberationsListPage() {
  const router = useRouter();
  const params = useParams();
  const boardId = params?.boardId as string;

  // ----- State -----------------------------------------------------------

  const [deliberations, setDeliberations] = useState<DeliberationRead[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<
    DeliberationStatus | "all"
  >("all");
  const [offset, setOffset] = useState(0);

  // Create dialog state
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [createTopic, setCreateTopic] = useState("");
  const [createReason, setCreateReason] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const isMountedRef = useRef(true);

  useEffect(() => {
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // ----- Data fetching ---------------------------------------------------

  const fetchDeliberations = useCallback(async () => {
    if (!boardId) return;
    setIsLoading(true);
    setError(null);
    try {
      const result = await listDeliberations(boardId, {
        status: statusFilter === "all" ? undefined : statusFilter,
        limit: PAGE_SIZE,
        offset,
      });
      if (!isMountedRef.current) return;
      setDeliberations(result.items);
      setTotal(result.total);
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      const message =
        err instanceof Error ? err.message : "Failed to load deliberations";
      setError(message);
    } finally {
      if (isMountedRef.current) {
        setIsLoading(false);
      }
    }
  }, [boardId, statusFilter, offset]);

  useEffect(() => {
    void fetchDeliberations();
  }, [fetchDeliberations]);

  // Reset offset when filter changes
  useEffect(() => {
    setOffset(0);
  }, [statusFilter]);

  // ----- Computed values -------------------------------------------------

  const stats = useMemo(() => computeStats(deliberations), [deliberations]);

  const hasMore = offset + PAGE_SIZE < total;
  const hasPrevious = offset > 0;

  const activeCount = deliberations.filter(
    (d) =>
      d.status !== "concluded" && d.status !== "abandoned",
  ).length;

  // ----- Handlers --------------------------------------------------------

  const handleCardClick = useCallback(
    (deliberation: DeliberationRead) => {
      router.push(
        `/boards/${boardId}/deliberations/${deliberation.id}`,
      );
    },
    [boardId, router],
  );

  const handleCreate = useCallback(async () => {
    const trimmedTopic = createTopic.trim();
    if (!trimmedTopic || isCreating) return;
    setIsCreating(true);
    setCreateError(null);
    try {
      const created = await createDeliberation(boardId, {
        topic: trimmedTopic,
        trigger_reason: createReason.trim() || "manual",
      });
      if (!isMountedRef.current) return;
      setIsCreateOpen(false);
      setCreateTopic("");
      setCreateReason("");
      // Navigate to the new deliberation
      router.push(`/boards/${boardId}/deliberations/${created.id}`);
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      const message =
        err instanceof Error ? err.message : "Failed to create deliberation";
      setCreateError(message);
    } finally {
      if (isMountedRef.current) {
        setIsCreating(false);
      }
    }
  }, [boardId, createReason, createTopic, isCreating, router]);

  const handleCloseCreate = useCallback(() => {
    setIsCreateOpen(false);
    setCreateTopic("");
    setCreateReason("");
    setCreateError(null);
  }, []);

  // ----- Render ----------------------------------------------------------

  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => router.push(`/boards/${boardId}`)}
            className="rounded-lg p-1.5 text-[var(--text-muted)] transition hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
            aria-label="Back to board"
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
          <div>
            <h1 className="text-lg font-bold text-[var(--text)]">
              Deliberations
            </h1>
            <p className="text-xs text-[var(--text-muted)]">
              {total} total · {activeCount} active
            </p>
          </div>
        </div>

        <Button
          onClick={() => setIsCreateOpen(true)}
          size="sm"
        >
          <svg
            className="mr-1.5 h-4 w-4"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 4v16m8-8H4"
            />
          </svg>
          New Deliberation
        </Button>
      </div>

      {/* Stats bar */}
      {stats.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {stats.map(({ status, count }) => (
            <button
              key={status}
              type="button"
              onClick={() =>
                setStatusFilter((prev) =>
                  prev === status ? "all" : status,
                )
              }
              className={[
                "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition",
                statusFilter === status
                  ? "border-[var(--accent-strong)] bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                  : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:border-[var(--border-strong)]",
              ].join(" ")}
            >
              <Badge
                variant={
                  DELIBERATION_STATUS_VARIANTS[status] ?? "default"
                }
                className="text-[9px] px-1.5 py-0"
              >
                {count}
              </Badge>
              {DELIBERATION_STATUS_LABELS[status] ?? status}
            </button>
          ))}
        </div>
      )}

      {/* Filter pills */}
      <div className="mt-4 flex flex-wrap items-center gap-1.5 border-b border-[var(--border)] pb-3">
        {STATUS_FILTERS.map(({ value, label }) => (
          <button
            key={value}
            type="button"
            onClick={() => setStatusFilter(value)}
            className={[
              "rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-wider transition",
              statusFilter === value
                ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                : "text-[var(--text-quiet)] hover:bg-[var(--surface-muted)] hover:text-[var(--text-muted)]",
            ].join(" ")}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Error state */}
      {error && (
        <div className="mt-4 rounded-xl border border-[color:rgba(180,35,24,0.3)] bg-[color:rgba(180,35,24,0.06)] p-4">
          <div className="flex items-center gap-2 text-sm text-[var(--danger)]">
            <svg
              className="h-4 w-4 shrink-0"
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
            <span>{error}</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void fetchDeliberations()}
            className="mt-2 text-xs"
          >
            Retry
          </Button>
        </div>
      )}

      {/* Loading state */}
      {isLoading && (
        <div className="mt-8 flex items-center justify-center gap-2 text-sm text-[var(--text-muted)]">
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
          Loading deliberations…
        </div>
      )}

      {/* Deliberation list */}
      {!isLoading && !error && (
        <>
          {deliberations.length === 0 ? (
            <div className="mt-8 rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] p-8 text-center">
              <svg
                className="mx-auto h-10 w-10 text-[var(--text-quiet)]"
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
              <h3 className="mt-3 text-sm font-semibold text-[var(--text)]">
                No deliberations yet
              </h3>
              <p className="mt-1 text-xs text-[var(--text-muted)]">
                {statusFilter === "all"
                  ? "Start a new deliberation to have agents debate and reach consensus."
                  : `No deliberations with status "${DELIBERATION_STATUS_LABELS[statusFilter as DeliberationStatus] ?? statusFilter}".`}
              </p>
              {statusFilter === "all" && (
                <Button
                  onClick={() => setIsCreateOpen(true)}
                  size="sm"
                  className="mt-4"
                >
                  Start a Deliberation
                </Button>
              )}
            </div>
          ) : (
            <div className="mt-4 space-y-3">
              {deliberations.map((deliberation) => (
                <DeliberationCard
                  key={deliberation.id}
                  deliberation={deliberation}
                  onClick={handleCardClick}
                />
              ))}
            </div>
          )}

          {/* Pagination */}
          {(hasPrevious || hasMore) && (
            <div className="mt-6 flex items-center justify-between">
              <Button
                variant="outline"
                size="sm"
                disabled={!hasPrevious}
                onClick={() =>
                  setOffset((prev) => Math.max(0, prev - PAGE_SIZE))
                }
              >
                ← Previous
              </Button>
              <span className="text-xs text-[var(--text-quiet)]">
                Showing {offset + 1}–
                {Math.min(offset + PAGE_SIZE, total)} of {total}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={!hasMore}
                onClick={() => setOffset((prev) => prev + PAGE_SIZE)}
              >
                Next →
              </Button>
            </div>
          )}
        </>
      )}

      {/* Create deliberation dialog (modal overlay) */}
      {isCreateOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            onClick={handleCloseCreate}
            onKeyDown={(e) => {
              if (e.key === "Escape") handleCloseCreate();
            }}
            role="button"
            tabIndex={0}
            aria-label="Close dialog"
          />

          {/* Dialog panel */}
          <div className="relative z-10 w-full max-w-lg rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-6 shadow-2xl">
            {/* Close button */}
            <button
              type="button"
              onClick={handleCloseCreate}
              className="absolute right-4 top-4 rounded-md p-1 text-[var(--text-quiet)] transition hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
              aria-label="Close"
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
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </button>

            <h2 className="text-base font-bold text-[var(--text)]">
              Start a New Deliberation
            </h2>
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              Create a structured deliberation for agents to debate, discuss,
              and reach consensus on a topic.
            </p>

            {/* Topic */}
            <div className="mt-4">
              <label
                htmlFor="create-topic"
                className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]"
              >
                Topic *
              </label>
              <Textarea
                id="create-topic"
                value={createTopic}
                onChange={(e) => setCreateTopic(e.target.value)}
                placeholder="What should the agents deliberate about?"
                className="min-h-[80px]"
                disabled={isCreating}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    void handleCreate();
                  }
                }}
              />
            </div>

            {/* Trigger reason */}
            <div className="mt-3">
              <label
                htmlFor="create-reason"
                className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]"
              >
                Trigger Reason (optional)
              </label>
              <input
                id="create-reason"
                type="text"
                value={createReason}
                onChange={(e) => setCreateReason(e.target.value)}
                placeholder="e.g. divergent_positions, manual, review…"
                disabled={isCreating}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--text-quiet)] focus:border-[var(--accent-strong)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-strong)] disabled:opacity-50"
              />
            </div>

            {/* Error */}
            {createError && (
              <div className="mt-3 rounded-md bg-[color:rgba(180,35,24,0.1)] px-3 py-2 text-xs text-[var(--danger)]">
                {createError}
              </div>
            )}

            {/* Actions */}
            <div className="mt-5 flex items-center justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleCloseCreate}
                disabled={isCreating}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={() => void handleCreate()}
                disabled={!createTopic.trim() || isCreating}
              >
                {isCreating ? (
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
                    Creating…
                  </span>
                ) : (
                  "Start Deliberation"
                )}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
