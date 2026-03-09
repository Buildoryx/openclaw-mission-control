"use client";

export const dynamic = "force-dynamic";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/auth/clerk";
import { ApiError } from "@/api/mutator";
import {
  type listBoardsApiV1BoardsGetResponse,
  useListBoardsApiV1BoardsGet,
} from "@/api/generated/boards/boards";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DeliberationCard } from "@/components/deliberations/DeliberationCard";
import type { DeliberationRead, DeliberationStatus } from "@/components/deliberations/types";
import {
  DELIBERATION_STATUS_LABELS,
  DELIBERATION_STATUS_VARIANTS,
  listDeliberations,
} from "@/components/deliberations/types";
import { DashboardPageLayout } from "@/components/templates/DashboardPageLayout";

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

type DeliberationWithBoard = DeliberationRead & { boardName: string };

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export default function DeliberationsPage() {
  const router = useRouter();
  const { isSignedIn } = useAuth();

  // ---- Board list ----------------------------------------------------------
  const boardsQuery = useListBoardsApiV1BoardsGet<
    listBoardsApiV1BoardsGetResponse,
    ApiError
  >(undefined, {
    query: {
      enabled: Boolean(isSignedIn),
      refetchOnMount: "always",
    },
  });

  const boards = useMemo(
    () =>
      boardsQuery.data?.status === 200
        ? (boardsQuery.data.data.items ?? [])
        : [],
    [boardsQuery.data],
  );

  // ---- Aggregated deliberations state -------------------------------------
  const [deliberations, setDeliberations] = useState<DeliberationWithBoard[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<DeliberationStatus | "all">("all");
  const isMountedRef = useRef(true);

  useEffect(() => {
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // ---- Fetch deliberations across all boards -------------------------------
  const fetchAll = useCallback(async () => {
    if (boards.length === 0) return;
    setIsLoading(true);
    setError(null);
    try {
      const results = await Promise.all(
        boards.map(async (board) => {
          try {
            const page = await listDeliberations(board.id, {
              status: statusFilter === "all" ? undefined : statusFilter,
              limit: 100,
              offset: 0,
            });
            return page.items.map((d) => ({
              ...d,
              boardName: board.name,
            }));
          } catch {
            return [];
          }
        }),
      );
      if (!isMountedRef.current) return;
      const merged = ([] as DeliberationWithBoard[])
        .concat(...results)
        .sort(
          (a, b) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
        );
      setDeliberations(merged);
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to load deliberations");
    } finally {
      if (isMountedRef.current) setIsLoading(false);
    }
  }, [boards, statusFilter]);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  // ---- Computed -----------------------------------------------------------
  const activeCount = deliberations.filter(
    (d) => d.status !== "concluded" && d.status !== "abandoned",
  ).length;

  const statCounts = useMemo(() => {
    const counts: Partial<Record<DeliberationStatus, number>> = {};
    for (const d of deliberations) {
      counts[d.status] = (counts[d.status] ?? 0) + 1;
    }
    return Object.entries(counts)
      .map(([status, count]) => ({ status: status as DeliberationStatus, count: count ?? 0 }))
      .sort((a, b) => b.count - a.count);
  }, [deliberations]);

  const handleCardClick = useCallback(
    (deliberation: DeliberationRead) => {
      router.push(`/boards/${deliberation.board_id}/deliberations/${deliberation.id}`);
    },
    [router],
  );

  // ---- Render -------------------------------------------------------------
  return (
    <DashboardPageLayout
      signedOut={{
        message: "Sign in to view deliberations.",
        forceRedirectUrl: "/deliberations",
      }}
      title="Deliberations"
      description={`All agent debates across all boards. ${deliberations.length} total · ${activeCount} active`}
    >
      {/* Status stats bar */}
      {statCounts.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {statCounts.map(({ status, count }) => (
            <button
              key={status}
              type="button"
              onClick={() =>
                setStatusFilter((prev) => (prev === status ? "all" : status))
              }
              className={[
                "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition",
                statusFilter === status
                  ? "border-[var(--accent-strong)] bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                  : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:border-[var(--border-strong)]",
              ].join(" ")}
            >
              <Badge
                variant={DELIBERATION_STATUS_VARIANTS[status] ?? "default"}
                className="px-1.5 py-0 text-[9px]"
              >
                {count}
              </Badge>
              {DELIBERATION_STATUS_LABELS[status] ?? status}
            </button>
          ))}
        </div>
      )}

      {/* Status filter pills */}
      <div className="mb-4 flex flex-wrap items-center gap-1.5 border-b border-[var(--border)] pb-3">
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

      {/* Loading */}
      {(isLoading || boardsQuery.isLoading) && (
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

      {/* Error */}
      {error && (
        <div className="mt-4 rounded-xl border border-[color:rgba(180,35,24,0.3)] bg-[color:rgba(180,35,24,0.06)] p-4">
          <p className="text-sm text-[var(--danger)]">{error}</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void fetchAll()}
            className="mt-2 text-xs"
          >
            Retry
          </Button>
        </div>
      )}

      {/* No boards */}
      {!isLoading && !boardsQuery.isLoading && boards.length === 0 && !error && (
        <div className="mt-8 rounded-xl border border-dashed border-[var(--border)] bg-[var(--surface)] p-8 text-center">
          <p className="text-sm text-[var(--text-muted)]">
            No boards found. Create a board first to start deliberations.
          </p>
        </div>
      )}

      {/* Empty deliberations */}
      {!isLoading && !error && boards.length > 0 && deliberations.length === 0 && (
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
              ? "Navigate to a board to start an agent deliberation."
              : `No deliberations with status "${DELIBERATION_STATUS_LABELS[statusFilter as DeliberationStatus] ?? statusFilter}".`}
          </p>
        </div>
      )}

      {/* Deliberation list grouped by board */}
      {!isLoading && !error && deliberations.length > 0 && (
        <div className="space-y-6">
          {Array.from(new Set(deliberations.map((d) => d.board_id))).map((boardId) => {
            const boardItems = deliberations.filter((d) => d.board_id === boardId);
            const boardName = boardItems[0]?.boardName ?? boardId;
            return (
              <div key={boardId}>
                <div className="mb-2 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => router.push(`/boards/${boardId}`)}
                    className="text-xs font-semibold uppercase tracking-wider text-[var(--text-quiet)] hover:text-[var(--accent-strong)] transition"
                  >
                    {boardName}
                  </button>
                  <span className="text-[10px] text-[var(--text-quiet)]">
                    ({boardItems.length})
                  </span>
                </div>
                <div className="space-y-3">
                  {boardItems.map((deliberation) => (
                    <DeliberationCard
                      key={deliberation.id}
                      deliberation={deliberation}
                      onClick={handleCardClick}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </DashboardPageLayout>
  );
}
