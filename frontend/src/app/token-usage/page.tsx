"use client";

export const dynamic = "force-dynamic";

import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Coins,
  Database,
  FileText,
  Folder,
  FolderOpen,
  Lightbulb,
  RefreshCw,
  Timer,
  TrendingDown,
  Zap,
} from "lucide-react";

import { useAuth } from "@/auth/clerk";

import {
  useTokenUsageDashboard,
  microCentsToUsd,
  type UsageRangeKey,
  type TokenUsageDashboard,
} from "@/hooks/useTokenUsageApi";
import { DashboardPageLayout } from "@/components/templates/DashboardPageLayout";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import {
  type ContextFileEntry,
  type ContextDirectory,
  buildContextInventory,
  groupByDirectory,
  computeTotals,
  computeSessionBootCost,
  buildCronJobEstimates,
  generateDailyUsageEstimates,
  formatTokenCount,
  formatBytes,
  formatCost,
  toPercent,
  tokenEfficiencyClass,
  estimateInputCost,
} from "@/lib/token-estimation";

// ── Raw measured file data ───────────────────────────────────────────────────
// These are the actual measured sizes from the repository (wc -c / wc -w / wc -l)

const RAW_FILE_DATA: Array<{
  path: string;
  bytes: number;
  words: number;
  lines: number;
  loadContext: ContextFileEntry["loadContext"];
}> = [
  // Root-level context files
  {
    path: "AGENTS.md",
    bytes: 2591,
    words: 344,
    lines: 39,
    loadContext: "boot",
  },
  { path: "agent.md", bytes: 498, words: 82, lines: 11, loadContext: "boot" },
  {
    path: "README.md",
    bytes: 6549,
    words: 723,
    lines: 151,
    loadContext: "on-demand",
  },
  {
    path: "CONTRIBUTING.md",
    bytes: 2682,
    words: 367,
    lines: 81,
    loadContext: "on-demand",
  },
  // GitHub context files
  {
    path: ".github/copilot-instructions.md",
    bytes: 2591,
    words: 344,
    lines: 39,
    loadContext: "always",
  },
  {
    path: ".github/pull_request_template.md",
    bytes: 903,
    words: 168,
    lines: 35,
    loadContext: "on-demand",
  },
  // Docs root
  {
    path: "docs/README.md",
    bytes: 860,
    words: 66,
    lines: 26,
    loadContext: "on-demand",
  },
  {
    path: "docs/03-development.md",
    bytes: 666,
    words: 100,
    lines: 23,
    loadContext: "on-demand",
  },
  {
    path: "docs/coverage-policy.md",
    bytes: 117,
    words: 14,
    lines: 3,
    loadContext: "on-demand",
  },
  {
    path: "docs/installer-support.md",
    bytes: 1499,
    words: 231,
    lines: 26,
    loadContext: "on-demand",
  },
  {
    path: "docs/openclaw_baseline_config.md",
    bytes: 12888,
    words: 1551,
    lines: 499,
    loadContext: "on-demand",
  },
  {
    path: "docs/openclaw_gateway_ws.md",
    bytes: 1394,
    words: 180,
    lines: 30,
    loadContext: "on-demand",
  },
  {
    path: "docs/style-guide.md",
    bytes: 809,
    words: 122,
    lines: 39,
    loadContext: "on-demand",
  },
  // Docs subdirectories
  {
    path: "docs/architecture/README.md",
    bytes: 209,
    words: 32,
    lines: 10,
    loadContext: "on-demand",
  },
  {
    path: "docs/deployment/README.md",
    bytes: 2642,
    words: 344,
    lines: 100,
    loadContext: "on-demand",
  },
  {
    path: "docs/development/README.md",
    bytes: 954,
    words: 141,
    lines: 59,
    loadContext: "on-demand",
  },
  {
    path: "docs/getting-started/README.md",
    bytes: 714,
    words: 88,
    lines: 30,
    loadContext: "on-demand",
  },
  {
    path: "docs/operations/README.md",
    bytes: 1954,
    words: 260,
    lines: 86,
    loadContext: "on-demand",
  },
  {
    path: "docs/policy/one-migration-per-pr.md",
    bytes: 780,
    words: 115,
    lines: 23,
    loadContext: "on-demand",
  },
  {
    path: "docs/production/README.md",
    bytes: 33,
    words: 4,
    lines: 3,
    loadContext: "on-demand",
  },
  {
    path: "docs/reference/api.md",
    bytes: 3885,
    words: 534,
    lines: 141,
    loadContext: "on-demand",
  },
  {
    path: "docs/reference/authentication.md",
    bytes: 487,
    words: 56,
    lines: 30,
    loadContext: "on-demand",
  },
  {
    path: "docs/reference/configuration.md",
    bytes: 560,
    words: 77,
    lines: 19,
    loadContext: "on-demand",
  },
  {
    path: "docs/release/README.md",
    bytes: 1831,
    words: 321,
    lines: 62,
    loadContext: "on-demand",
  },
  {
    path: "docs/testing/README.md",
    bytes: 1216,
    words: 192,
    lines: 82,
    loadContext: "on-demand",
  },
  {
    path: "docs/troubleshooting/README.md",
    bytes: 325,
    words: 40,
    lines: 12,
    loadContext: "on-demand",
  },
  {
    path: "docs/troubleshooting/gateway-agent-provisioning.md",
    bytes: 3593,
    words: 495,
    lines: 106,
    loadContext: "on-demand",
  },
  // Backend & Frontend READMEs
  {
    path: "backend/README.md",
    bytes: 4236,
    words: 551,
    lines: 171,
    loadContext: "on-demand",
  },
  {
    path: "backend/templates/README.md",
    bytes: 5657,
    words: 608,
    lines: 195,
    loadContext: "on-demand",
  },
  {
    path: "frontend/README.md",
    bytes: 4966,
    words: 693,
    lines: 178,
    loadContext: "on-demand",
  },
];

// ── Metric card component ────────────────────────────────────────────────────

function MetricCard({
  icon: Icon,
  label,
  value,
  subtitle,
  tone = "default",
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  subtitle?: string;
  tone?: "default" | "accent" | "warning" | "danger" | "success";
}) {
  const iconColors: Record<string, string> = {
    default: "text-[var(--text-muted)]",
    accent: "text-[var(--accent)]",
    warning: "text-[var(--warning)]",
    danger: "text-[var(--danger)]",
    success: "text-[var(--success)]",
  };

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-start gap-3">
          <div
            className={cn(
              "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[var(--surface-muted)]",
              iconColors[tone],
            )}
          >
            <Icon className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-wider text-[var(--text-quiet)]">
              {label}
            </p>
            <p className="mt-0.5 text-2xl font-bold tracking-tight text-[var(--text)]">
              {value}
            </p>
            {subtitle ? (
              <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                {subtitle}
              </p>
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── File tree component ──────────────────────────────────────────────────────

function FileTreeRow({ file }: { file: ContextFileEntry }) {
  const efficiency = tokenEfficiencyClass(file.tokens);
  const badgeVariant: "default" | "success" | "warning" | "danger" =
    efficiency === "low"
      ? "success"
      : efficiency === "moderate"
        ? "default"
        : efficiency === "high"
          ? "warning"
          : "danger";

  return (
    <div className="flex items-center gap-3 rounded-lg px-3 py-2 transition hover:bg-[var(--surface-muted)]">
      <FileText className="h-4 w-4 shrink-0 text-[var(--text-quiet)]" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-[var(--text)]">
          {file.name}
        </p>
        <p className="truncate text-xs text-[var(--text-muted)]">{file.path}</p>
      </div>
      <div className="flex shrink-0 items-center gap-3 text-right">
        <div className="text-right">
          <p className="text-sm font-semibold tabular-nums text-[var(--text)]">
            {formatTokenCount(file.tokens)} tokens
          </p>
          <p className="text-xs tabular-nums text-[var(--text-muted)]">
            {formatBytes(file.bytes)} · {file.words} words · {file.lines} lines
          </p>
        </div>
        <Badge variant={badgeVariant} className="w-20 justify-center">
          {efficiency}
        </Badge>
        <div className="w-16 text-right">
          <p className="text-xs font-medium tabular-nums text-[var(--text-muted)]">
            {formatCost(file.cost)}
          </p>
        </div>
      </div>
    </div>
  );
}

function DirectoryNode({ dir }: { dir: ContextDirectory }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)]">
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-[var(--surface-muted)]"
      >
        {isOpen ? (
          <>
            <ChevronDown className="h-4 w-4 shrink-0 text-[var(--text-muted)]" />
            <FolderOpen className="h-4 w-4 shrink-0 text-[var(--accent)]" />
          </>
        ) : (
          <>
            <ChevronRight className="h-4 w-4 shrink-0 text-[var(--text-muted)]" />
            <Folder className="h-4 w-4 shrink-0 text-[var(--text-muted)]" />
          </>
        )}
        <span className="flex-1 text-sm font-semibold text-[var(--text)]">
          {dir.name}
        </span>
        <span className="text-xs font-medium text-[var(--text-muted)]">
          {dir.files.length} file{dir.files.length !== 1 ? "s" : ""}
        </span>
        <span className="ml-2 text-sm font-bold tabular-nums text-[var(--text)]">
          {formatTokenCount(dir.totalTokens)} tokens
        </span>
        <span className="ml-2 text-xs tabular-nums text-[var(--text-muted)]">
          {formatBytes(dir.totalBytes)}
        </span>
        <span className="ml-2 text-xs tabular-nums text-[var(--text-muted)]">
          {formatCost(dir.totalCost)}
        </span>
      </button>
      {isOpen ? (
        <div className="border-t border-[var(--border)] px-2 py-1">
          {dir.files
            .slice()
            .sort((a, b) => b.tokens - a.tokens)
            .map((file) => (
              <FileTreeRow key={file.path} file={file} />
            ))}
        </div>
      ) : null}
    </div>
  );
}

// ── Daily usage chart tooltip ────────────────────────────────────────────────

function UsageTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-semibold text-[var(--text)]">{label}</p>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center gap-2">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: entry.color }}
          />
          <span className="text-[var(--text-muted)]">{entry.name}:</span>
          <span className="font-medium tabular-nums text-[var(--text)]">
            {formatTokenCount(entry.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

function CostTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-semibold text-[var(--text)]">{label}</p>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center gap-2">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: entry.color }}
          />
          <span className="text-[var(--text-muted)]">{entry.name}:</span>
          <span className="font-medium tabular-nums text-[var(--text)]">
            {formatCost(entry.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Optimization recommendation card ─────────────────────────────────────────

type Recommendation = {
  title: string;
  impact: "high" | "medium" | "low";
  tokensSaved: number;
  description: string;
  action: string;
};

function RecommendationCard({ rec }: { rec: Recommendation }) {
  const impactColors: Record<string, string> = {
    high: "border-l-[var(--danger)]",
    medium: "border-l-[var(--warning)]",
    low: "border-l-[var(--success)]",
  };

  const impactBadge: Record<string, "danger" | "warning" | "success"> = {
    high: "danger",
    medium: "warning",
    low: "success",
  };

  return (
    <Card className={cn("border-l-4", impactColors[rec.impact])}>
      <CardContent className="pt-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h4 className="text-sm font-semibold text-[var(--text)]">
                {rec.title}
              </h4>
              <Badge variant={impactBadge[rec.impact]}>
                {rec.impact} impact
              </Badge>
            </div>
            <p className="mt-1 text-sm text-[var(--text-muted)]">
              {rec.description}
            </p>
            <p className="mt-2 text-xs font-medium text-[var(--accent)]">
              → {rec.action}
            </p>
          </div>
          <div className="shrink-0 text-right">
            <p className="text-lg font-bold tabular-nums text-[var(--success)]">
              −{formatTokenCount(rec.tokensSaved)}
            </p>
            <p className="text-xs text-[var(--text-muted)]">tokens/session</p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Main page component ──────────────────────────────────────────────────────

const STATIC_MODEL_COLORS: Record<string, string> = {
  "claude-sonnet-4": "#60a5fa",
  "claude-opus-4": "#a78bfa",
  "claude-haiku-3.5": "#34d399",
};

const EXTRA_MODEL_COLORS = [
  "#f59e0b",
  "#ec4899",
  "#14b8a6",
  "#f97316",
  "#8b5cf6",
  "#06b6d4",
  "#84cc16",
  "#e11d48",
];

/** Build a color map dynamically from API model names + known static ones. */
function buildModelColors(models: string[]): Record<string, string> {
  const colors: Record<string, string> = { ...STATIC_MODEL_COLORS };
  let extraIdx = 0;
  for (const m of models) {
    if (!colors[m]) {
      colors[m] = EXTRA_MODEL_COLORS[extraIdx % EXTRA_MODEL_COLORS.length];
      extraIdx++;
    }
  }
  return colors;
}

/** Data source indicator component. */
function DataSourceBanner({
  isLive,
  isLoading,
  isError,
}: {
  isLive: boolean;
  isLoading: boolean;
  isError: boolean;
}) {
  if (isLoading) {
    return (
      <div className="mb-4 flex items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] px-4 py-2 text-xs text-[var(--text-muted)]">
        <RefreshCw className="h-3 w-3 animate-spin" />
        Loading live token usage data from API…
      </div>
    );
  }
  if (isLive) {
    return (
      <div className="mb-4 flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-[rgba(15,118,110,0.08)] px-4 py-2 text-xs text-[var(--success)]">
        <span className="h-2 w-2 rounded-full bg-emerald-500" />
        Showing live data from{" "}
        <code className="font-semibold">/api/v1/token-usage/dashboard</code>
      </div>
    );
  }
  if (isError) {
    return (
      <div className="mb-4 flex items-center gap-2 rounded-lg border border-amber-500/30 bg-[rgba(180,83,9,0.08)] px-4 py-2 text-xs text-[var(--warning)]">
        <AlertTriangle className="h-3 w-3" />
        Could not reach the token usage API — showing estimated data. Start the
        backend and ingest events via{" "}
        <code className="font-semibold">
          POST /api/v1/token-usage/ingest
        </code>{" "}
        to see live metrics.
      </div>
    );
  }
  return (
    <div className="mb-4 flex items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] px-4 py-2 text-xs text-[var(--text-muted)]">
      <Database className="h-3 w-3" />
      Showing static estimates based on file analysis. Connect the backend API
      for live data.
    </div>
  );
}

export default function TokenUsagePage() {
  const { isSignedIn } = useAuth();
  const [rangeKey, _setRangeKey] = useState<UsageRangeKey>("5d");

  // ── Live API query ─────────────────────────────────────────────────────
  const dashboardQuery = useTokenUsageDashboard(
    { range: rangeKey },
    {
      enabled: Boolean(isSignedIn),
      retry: 1,
      refetchInterval: 60_000,
      refetchOnMount: "always",
    },
  );

  const liveData: TokenUsageDashboard | null =
    dashboardQuery.data?.status === 200 ? dashboardQuery.data.data : null;
  const isLive = liveData !== null && liveData.kpis.total_events > 0;
  const isApiLoading = dashboardQuery.isLoading;
  const isApiError = dashboardQuery.isError;

  // ── Static context file data (always available) ────────────────────────
  const inventory = useMemo(() => buildContextInventory(RAW_FILE_DATA), []);
  const directories = useMemo(() => groupByDirectory(inventory), [inventory]);
  const totals = useMemo(() => computeTotals(inventory), [inventory]);
  const sessionBootCosts = useMemo(
    () => computeSessionBootCost(inventory),
    [inventory],
  );
  const cronJobs = useMemo(() => buildCronJobEstimates(), []);

  // Fallback static estimates (used only when API has no data)
  const staticDailyUsage = useMemo(() => generateDailyUsageEstimates(5), []);

  const bootTotal = useMemo(
    () => sessionBootCosts.reduce((s, c) => s + c.tokens, 0),
    [sessionBootCosts],
  );
  const bootCost = useMemo(
    () => sessionBootCosts.reduce((s, c) => s + c.cost, 0),
    [sessionBootCosts],
  );

  // ── Derive chart data from live API or static fallback ─────────────────

  // Daily chart data (token counts by model per day)
  const dailyChartData = useMemo(() => {
    if (isLive && liveData) {
      const byDate = new Map<
        string,
        Record<string, number> & { date: string; total: number }
      >();
      for (const entry of liveData.daily_rollup) {
        let row = byDate.get(entry.date);
        if (!row) {
          row = { date: entry.date, total: 0 } as Record<string, number> & {
            date: string;
            total: number;
          };
          byDate.set(entry.date, row);
        }
        row[entry.model] = (row[entry.model] ?? 0) + entry.total_tokens;
        row.total += entry.total_tokens;
      }
      return Array.from(byDate.values());
    }

    // Static fallback
    const byDate = new Map<
      string,
      Record<string, number> & { date: string; total: number }
    >();
    for (const entry of staticDailyUsage) {
      let row = byDate.get(entry.date);
      if (!row) {
        row = { date: entry.date, total: 0 } as Record<string, number> & {
          date: string;
          total: number;
        };
        byDate.set(entry.date, row);
      }
      row[entry.model] = (row[entry.model] ?? 0) + entry.totalTokens;
      row.total += entry.totalTokens;
    }
    return Array.from(byDate.values());
  }, [isLive, liveData, staticDailyUsage]);

  // Daily cost data
  const dailyCostData = useMemo(() => {
    if (isLive && liveData) {
      const byDate = new Map<
        string,
        Record<string, number> & { date: string; total: number }
      >();
      for (const entry of liveData.daily_rollup) {
        let row = byDate.get(entry.date);
        if (!row) {
          row = { date: entry.date, total: 0 } as Record<string, number> & {
            date: string;
            total: number;
          };
          byDate.set(entry.date, row);
        }
        const costUsd = microCentsToUsd(entry.total_cost_microcents);
        row[entry.model] = (row[entry.model] ?? 0) + costUsd;
        row.total += costUsd;
      }
      return Array.from(byDate.values());
    }

    // Static fallback
    const byDate = new Map<
      string,
      Record<string, number> & { date: string; total: number }
    >();
    for (const entry of staticDailyUsage) {
      let row = byDate.get(entry.date);
      if (!row) {
        row = { date: entry.date, total: 0 } as Record<string, number> & {
          date: string;
          total: number;
        };
        byDate.set(entry.date, row);
      }
      row[entry.model] = (row[entry.model] ?? 0) + entry.totalCost;
      row.total += entry.totalCost;
    }
    return Array.from(byDate.values());
  }, [isLive, liveData, staticDailyUsage]);

  // Aggregate daily totals
  const dailyTotals = useMemo(() => {
    if (isLive && liveData) {
      return {
        totalTokens: liveData.kpis.total_tokens,
        totalCost: microCentsToUsd(liveData.kpis.total_cost_microcents),
        totalSessions: liveData.kpis.total_sessions,
      };
    }

    let totalTokens = 0;
    let totalCost = 0;
    let totalSessions = 0;
    for (const entry of staticDailyUsage) {
      totalTokens += entry.totalTokens;
      totalCost += entry.totalCost;
      totalSessions += entry.sessions;
    }
    return { totalTokens, totalCost, totalSessions };
  }, [isLive, liveData, staticDailyUsage]);

  // Per-model breakdown
  const modelBreakdown = useMemo(() => {
    if (isLive && liveData) {
      return liveData.by_model.map((m) => ({
        model: m.model,
        tokens: m.total_tokens,
        cost: microCentsToUsd(m.total_cost_microcents),
        sessions: m.session_count,
        sharePct: m.share_pct,
      }));
    }

    const map = new Map<
      string,
      { tokens: number; cost: number; sessions: number }
    >();
    for (const entry of staticDailyUsage) {
      const existing = map.get(entry.model) ?? {
        tokens: 0,
        cost: 0,
        sessions: 0,
      };
      existing.tokens += entry.totalTokens;
      existing.cost += entry.totalCost;
      existing.sessions += entry.sessions;
      map.set(entry.model, existing);
    }
    const total = Array.from(map.values()).reduce((s, v) => s + v.tokens, 0);
    return Array.from(map.entries())
      .map(([model, data]) => ({
        model,
        ...data,
        sharePct: total > 0 ? Math.round((data.tokens / total) * 1000) / 10 : 0,
      }))
      .sort((a, b) => b.tokens - a.tokens);
  }, [isLive, liveData, staticDailyUsage]);

  // Collect all model names for dynamic color assignment
  const MODEL_COLORS = useMemo(() => {
    const models = modelBreakdown.map((m) => m.model);
    return buildModelColors(models);
  }, [modelBreakdown]);

  // Boot files only
  const bootFiles = useMemo(
    () =>
      inventory.filter(
        (f) => f.loadContext === "boot" || f.loadContext === "always",
      ),
    [inventory],
  );

  // Optimization recommendations
  const recommendations: Recommendation[] = useMemo(
    () => [
      {
        title: "Deduplicate AGENTS.md and copilot-instructions.md",
        impact: "high" as const,
        tokensSaved: 648,
        description:
          "AGENTS.md (2,591 bytes) and .github/copilot-instructions.md (2,591 bytes) are identical files — " +
          "the same 648 tokens loaded twice. AGENTS.md is loaded at boot, and copilot-instructions.md is loaded " +
          "on every interaction by the Copilot context system.",
        action:
          'Make AGENTS.md a one-line pointer: "See .github/copilot-instructions.md for all repo guidelines." ' +
          "This saves ~600 tokens per session.",
      },
      {
        title: "Compress openclaw_baseline_config.md",
        impact: "high" as const,
        tokensSaved: 2400,
        description:
          "At 12,888 bytes (~3,222 tokens), this is by far the largest .md file. It contains a full JSON " +
          "config dump plus verbose section-by-section commentary. Most of the JSON block is repeated in the " +
          "commentary below it.",
        action:
          "Split into two files: a concise quick-reference (keep under 2KB) and a full reference doc. " +
          "Only the quick-reference needs to be in the docs/ root. Move the verbose version to docs/reference/.",
      },
      {
        title: "Trim agent.md review policy directives",
        impact: "low" as const,
        tokensSaved: 80,
        description:
          "agent.md (498 bytes, ~125 tokens) is loaded at boot and contains PR review policy. " +
          "These instructions could be folded into the existing copilot-instructions.md as a subsection, " +
          "eliminating a separate file load.",
        action:
          "Merge the 5-point review policy into .github/copilot-instructions.md under a new " +
          '"## PR Review Policy" heading. Delete agent.md.',
      },
      {
        title: "Create a .contextignore or .copilotignore",
        impact: "medium" as const,
        tokensSaved: 1200,
        description:
          "The AI agent may scan and load README files from backend/templates/, frontend/, and backend/ " +
          "during exploratory operations. These large README files (4–6KB each) are rarely needed for " +
          "typical coding tasks.",
        action:
          "Add a .copilotignore or equivalent config to exclude backend/templates/README.md and " +
          "docs/openclaw_baseline_config.md from automatic context loading. Keep them accessible on-demand.",
      },
      {
        title: "Consolidate sparse docs/ subdirectory READMEs",
        impact: "medium" as const,
        tokensSaved: 350,
        description:
          "Several docs/ subdirectories contain only a single small README.md (architecture: 209B, " +
          "production: 33B, getting-started: 714B, troubleshooting: 325B). These add file-system overhead " +
          "when the agent indexes the project structure.",
        action:
          "Merge stub READMEs (under 500B) into docs/README.md as sections. Keep only subdirectories " +
          "that have substantial content (deployment, operations, reference, testing, release).",
      },
      {
        title: "Use structured summaries in large docs",
        impact: "medium" as const,
        tokensSaved: 800,
        description:
          "Several docs files (deployment README: 2,642B, operations README: 1,954B, release README: 1,831B) " +
          "use verbose prose. Adding a TL;DR block at the top of each would let context-pruning trim the " +
          "body while preserving key information.",
        action:
          "Add a <!-- SUMMARY --> block (under 200 words) at the top of each doc over 1.5KB. " +
          "Configure context pruning to prefer summary blocks when trimming.",
      },
    ],
    [],
  );

  const totalSavings = recommendations.reduce((s, r) => s + r.tokensSaved, 0);
  const totalSavingsCost = estimateInputCost(totalSavings);

  return (
    <DashboardPageLayout
      signedOut={{
        message: "Sign in to view token usage analytics",
        forceRedirectUrl: "/token-usage",
      }}
      title="Token Usage Dashboard"
      description="Monitor context sizes, session costs, and daily token consumption across all activities"
    >
      {/* ── Data source indicator ─────────────────────────────────── */}
      <DataSourceBanner
        isLive={isLive}
        isLoading={isApiLoading}
        isError={isApiError}
      />

      {/* ── Top-level metrics ─────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          icon={Database}
          label="Total context files"
          value={`${totals.totalFiles} files`}
          subtitle={`${formatTokenCount(totals.totalTokens)} tokens · ${formatBytes(totals.totalBytes)}`}
          tone="accent"
        />
        <MetricCard
          icon={Zap}
          label="Session boot cost"
          value={formatTokenCount(bootTotal)}
          subtitle={`${formatCost(bootCost)} per new session`}
          tone="warning"
        />
        <MetricCard
          icon={Coins}
          label="5-day total usage"
          value={formatTokenCount(dailyTotals.totalTokens)}
          subtitle={`${formatCost(dailyTotals.totalCost)} · ${dailyTotals.totalSessions} sessions`}
          tone="accent"
        />
        <MetricCard
          icon={TrendingDown}
          label="Potential savings"
          value={`−${formatTokenCount(totalSavings)}`}
          subtitle={`${formatCost(totalSavingsCost)} per session if optimized`}
          tone="success"
        />
      </div>

      {/* ── Tabbed sections ───────────────────────────────────────── */}
      <div className="mt-8">
        <Tabs defaultValue="files">
          <TabsList>
            <TabsTrigger value="files">Context Files</TabsTrigger>
            <TabsTrigger value="daily">Daily Usage</TabsTrigger>
            <TabsTrigger value="boot">Session Boot</TabsTrigger>
            <TabsTrigger value="cron">Cron & Jobs</TabsTrigger>
            <TabsTrigger value="optimize">Optimization</TabsTrigger>
          </TabsList>

          {/* ── TAB: Context Files ──────────────────────────────── */}
          <TabsContent value="files">
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-lg font-semibold text-[var(--text)]">
                      Context File Browser
                    </h2>
                    <p className="mt-1 text-sm text-[var(--text-muted)]">
                      All <code>.md</code> files in the repository, grouped by
                      directory. Click a folder to expand and see individual
                      files with their token counts.
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-2xl font-bold tabular-nums text-[var(--text)]">
                      {formatTokenCount(totals.totalTokens)}
                    </p>
                    <p className="text-xs text-[var(--text-muted)]">
                      total tokens across {totals.totalFiles} files
                    </p>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                {/* Legend */}
                <div className="mb-4 flex flex-wrap items-center gap-3 text-xs">
                  <span className="text-[var(--text-quiet)]">Efficiency:</span>
                  <Badge variant="success">low (&lt;500 tok)</Badge>
                  <Badge variant="default">moderate (500–1.5K)</Badge>
                  <Badge variant="warning">high (1.5–4K)</Badge>
                  <Badge variant="danger">critical (&gt;4K)</Badge>
                </div>

                {/* Load context filter */}
                <div className="mb-4 flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-[var(--text-quiet)]">
                    Load context key:
                  </span>
                  <span className="rounded bg-[var(--accent-soft)] px-2 py-0.5 font-medium text-[var(--accent-strong)]">
                    boot
                  </span>
                  <span className="text-[var(--text-quiet)]">
                    = loaded on session start
                  </span>
                  <span className="rounded bg-[rgba(15,118,110,0.14)] px-2 py-0.5 font-medium text-[var(--success)]">
                    always
                  </span>
                  <span className="text-[var(--text-quiet)]">
                    = loaded every interaction
                  </span>
                  <span className="rounded bg-[var(--surface-muted)] px-2 py-0.5 font-medium text-[var(--text-muted)]">
                    on-demand
                  </span>
                  <span className="text-[var(--text-quiet)]">
                    = loaded when referenced
                  </span>
                </div>

                {/* Directory tree */}
                <div className="space-y-2">
                  {directories.map((dir) => (
                    <DirectoryNode key={dir.path} dir={dir} />
                  ))}
                </div>

                {/* Top files by token count */}
                <div className="mt-6">
                  <h3 className="mb-3 text-sm font-semibold text-[var(--text)]">
                    Top 10 files by token count
                  </h3>
                  <div className="space-y-1">
                    {inventory
                      .slice()
                      .sort((a, b) => b.tokens - a.tokens)
                      .slice(0, 10)
                      .map((file) => (
                        <div
                          key={file.path}
                          className="flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-[var(--surface-muted)]"
                        >
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm text-[var(--text)]">
                              {file.path}
                            </p>
                          </div>
                          <div className="flex shrink-0 items-center gap-4">
                            {/* Proportion bar */}
                            <div className="h-2 w-32 overflow-hidden rounded-full bg-[var(--surface-muted)]">
                              <div
                                className="h-full rounded-full bg-[var(--accent)]"
                                style={{
                                  width: `${Math.min(100, (file.tokens / totals.totalTokens) * 100)}%`,
                                }}
                              />
                            </div>
                            <span className="w-24 text-right text-sm font-semibold tabular-nums text-[var(--text)]">
                              {formatTokenCount(file.tokens)}
                            </span>
                            <span className="w-14 text-right text-xs tabular-nums text-[var(--text-muted)]">
                              {toPercent(file.tokens, totals.totalTokens)}
                            </span>
                          </div>
                        </div>
                      ))}
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* ── TAB: Daily Usage ─────────────────────────────────── */}
          <TabsContent value="daily">
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              {/* Token usage chart */}
              <Card>
                <CardHeader>
                  <h2 className="text-lg font-semibold text-[var(--text)]">
                    Daily token usage (last 5 days)
                  </h2>
                  <p className="mt-1 text-sm text-[var(--text-muted)]">
                    Stacked by model — hover for details
                  </p>
                </CardHeader>
                <CardContent>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={dailyChartData}>
                        <CartesianGrid
                          strokeDasharray="3 3"
                          stroke="var(--border)"
                          vertical={false}
                        />
                        <XAxis
                          dataKey="date"
                          tick={{ fontSize: 11, fill: "var(--text-muted)" }}
                          tickLine={false}
                          axisLine={false}
                        />
                        <YAxis
                          tick={{ fontSize: 11, fill: "var(--text-muted)" }}
                          tickLine={false}
                          axisLine={false}
                          tickFormatter={(v: number) => formatTokenCount(v)}
                        />
                        <Tooltip content={<UsageTooltip />} />
                        {Object.entries(MODEL_COLORS).map(([model, color]) => (
                          <Bar
                            key={model}
                            dataKey={model}
                            stackId="tokens"
                            fill={color}
                            radius={[2, 2, 0, 0]}
                          />
                        ))}
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  {/* Legend */}
                  <div className="mt-3 flex flex-wrap items-center justify-center gap-4">
                    {Object.entries(MODEL_COLORS).map(([model, color]) => (
                      <div key={model} className="flex items-center gap-2">
                        <span
                          className="h-3 w-3 rounded-full"
                          style={{ backgroundColor: color }}
                        />
                        <span className="text-xs text-[var(--text-muted)]">
                          {model}
                        </span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>

              {/* Cost chart */}
              <Card>
                <CardHeader>
                  <h2 className="text-lg font-semibold text-[var(--text)]">
                    Daily cost breakdown (last 5 days)
                  </h2>
                  <p className="mt-1 text-sm text-[var(--text-muted)]">
                    Estimated cost by model
                  </p>
                </CardHeader>
                <CardContent>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={dailyCostData}>
                        <CartesianGrid
                          strokeDasharray="3 3"
                          stroke="var(--border)"
                          vertical={false}
                        />
                        <XAxis
                          dataKey="date"
                          tick={{ fontSize: 11, fill: "var(--text-muted)" }}
                          tickLine={false}
                          axisLine={false}
                        />
                        <YAxis
                          tick={{ fontSize: 11, fill: "var(--text-muted)" }}
                          tickLine={false}
                          axisLine={false}
                          tickFormatter={(v: number) => formatCost(v)}
                        />
                        <Tooltip content={<CostTooltip />} />
                        {Object.entries(MODEL_COLORS).map(([model, color]) => (
                          <Area
                            key={model}
                            type="monotone"
                            dataKey={model}
                            stackId="cost"
                            stroke={color}
                            fill={color}
                            fillOpacity={0.3}
                          />
                        ))}
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                  {/* Legend */}
                  <div className="mt-3 flex flex-wrap items-center justify-center gap-4">
                    {Object.entries(MODEL_COLORS).map(([model, color]) => (
                      <div key={model} className="flex items-center gap-2">
                        <span
                          className="h-3 w-3 rounded-full"
                          style={{ backgroundColor: color }}
                        />
                        <span className="text-xs text-[var(--text-muted)]">
                          {model}
                        </span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>

              {/* Model breakdown table */}
              <Card className="xl:col-span-2">
                <CardHeader>
                  <h2 className="text-lg font-semibold text-[var(--text)]">
                    Per-model breakdown (5-day totals)
                  </h2>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-[var(--border)]">
                          <th className="py-2 pr-4 text-left font-semibold text-[var(--text-muted)]">
                            Model
                          </th>
                          <th className="py-2 px-4 text-right font-semibold text-[var(--text-muted)]">
                            Sessions
                          </th>
                          <th className="py-2 px-4 text-right font-semibold text-[var(--text-muted)]">
                            Total tokens
                          </th>
                          <th className="py-2 px-4 text-right font-semibold text-[var(--text-muted)]">
                            Share
                          </th>
                          <th className="py-2 px-4 text-right font-semibold text-[var(--text-muted)]">
                            Avg/session
                          </th>
                          <th className="py-2 pl-4 text-right font-semibold text-[var(--text-muted)]">
                            Est. cost
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {modelBreakdown.map((row) => (
                          <tr
                            key={row.model}
                            className="border-b border-[var(--table-divide)] transition hover:bg-[var(--surface-muted)]"
                          >
                            <td className="py-2.5 pr-4 font-medium text-[var(--text)]">
                              <div className="flex items-center gap-2">
                                <span
                                  className="h-3 w-3 rounded-full"
                                  style={{
                                    backgroundColor:
                                      MODEL_COLORS[row.model] ?? "#94a3b8",
                                  }}
                                />
                                {row.model}
                              </div>
                            </td>
                            <td className="py-2.5 px-4 text-right tabular-nums text-[var(--text)]">
                              {row.sessions}
                            </td>
                            <td className="py-2.5 px-4 text-right font-semibold tabular-nums text-[var(--text)]">
                              {formatTokenCount(row.tokens)}
                            </td>
                            <td className="py-2.5 px-4 text-right tabular-nums text-[var(--text-muted)]">
                              {row.sharePct !== undefined
                                ? `${row.sharePct}%`
                                : toPercent(
                                    row.tokens,
                                    dailyTotals.totalTokens,
                                  )}
                            </td>
                            <td className="py-2.5 px-4 text-right tabular-nums text-[var(--text-muted)]">
                              {formatTokenCount(
                                Math.round(
                                  row.tokens / Math.max(row.sessions, 1),
                                ),
                              )}
                            </td>
                            <td className="py-2.5 pl-4 text-right font-medium tabular-nums text-[var(--text)]">
                              {formatCost(row.cost)}
                            </td>
                          </tr>
                        ))}
                        <tr className="font-semibold">
                          <td className="py-2.5 pr-4 text-[var(--text)]">
                            Total
                          </td>
                          <td className="py-2.5 px-4 text-right tabular-nums text-[var(--text)]">
                            {dailyTotals.totalSessions}
                          </td>
                          <td className="py-2.5 px-4 text-right tabular-nums text-[var(--text)]">
                            {formatTokenCount(dailyTotals.totalTokens)}
                          </td>
                          <td className="py-2.5 px-4 text-right tabular-nums text-[var(--text-muted)]">
                            100%
                          </td>
                          <td className="py-2.5 px-4 text-right tabular-nums text-[var(--text-muted)]">
                            {formatTokenCount(
                              Math.round(
                                dailyTotals.totalTokens /
                                  Math.max(dailyTotals.totalSessions, 1),
                              ),
                            )}
                          </td>
                          <td className="py-2.5 pl-4 text-right tabular-nums text-[var(--text)]">
                            {formatCost(dailyTotals.totalCost)}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          {/* ── TAB: Session Boot ────────────────────────────────── */}
          <TabsContent value="boot">
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-lg font-semibold text-[var(--text)]">
                      Session boot cost breakdown
                    </h2>
                    <p className="mt-1 text-sm text-[var(--text-muted)]">
                      Every new AI session loads these resources into context
                      before any user interaction. This is the fixed cost per
                      session.
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-2xl font-bold tabular-nums text-[var(--text)]">
                      {formatTokenCount(bootTotal)}
                    </p>
                    <p className="text-xs text-[var(--text-muted)]">
                      {formatCost(bootCost)} per session
                    </p>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="space-y-1">
                  {sessionBootCosts
                    .slice()
                    .sort((a, b) => b.tokens - a.tokens)
                    .map((item) => (
                      <div
                        key={item.label}
                        className="flex items-center gap-3 rounded-lg px-3 py-3 hover:bg-[var(--surface-muted)]"
                      >
                        <Zap className="h-4 w-4 shrink-0 text-[var(--warning)]" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium text-[var(--text)]">
                            {item.label}
                          </p>
                          <p className="text-xs text-[var(--text-muted)]">
                            {item.description}
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-4">
                          {/* Proportion bar */}
                          <div className="h-2 w-32 overflow-hidden rounded-full bg-[var(--surface-muted)]">
                            <div
                              className="h-full rounded-full bg-[var(--warning)]"
                              style={{
                                width: `${Math.min(100, (item.tokens / bootTotal) * 100)}%`,
                              }}
                            />
                          </div>
                          <span className="w-20 text-right text-sm font-semibold tabular-nums text-[var(--text)]">
                            {formatTokenCount(item.tokens)}
                          </span>
                          <span className="w-14 text-right text-xs tabular-nums text-[var(--text-muted)]">
                            {toPercent(item.tokens, bootTotal)}
                          </span>
                          <span className="w-16 text-right text-xs tabular-nums text-[var(--text-muted)]">
                            {formatCost(item.cost)}
                          </span>
                        </div>
                      </div>
                    ))}
                </div>

                {/* Boot files specifically */}
                <div className="mt-6 rounded-xl border border-[var(--border)] bg-[var(--surface-muted)] p-4">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4 text-[var(--warning)]" />
                    <h3 className="text-sm font-semibold text-[var(--text)]">
                      Boot-loaded files ({bootFiles.length} files)
                    </h3>
                  </div>
                  <p className="mt-1 text-xs text-[var(--text-muted)]">
                    These files are loaded into context on every new session or
                    interaction. Reducing their size has a direct multiplier
                    effect on total token usage.
                  </p>
                  <div className="mt-3 space-y-1">
                    {bootFiles
                      .slice()
                      .sort((a, b) => b.tokens - a.tokens)
                      .map((f) => (
                        <div
                          key={f.path}
                          className="flex items-center justify-between rounded-lg px-2 py-1.5 text-sm"
                        >
                          <span className="font-medium text-[var(--text)]">
                            {f.path}
                          </span>
                          <div className="flex items-center gap-3">
                            <Badge
                              variant={
                                f.loadContext === "always"
                                  ? "accent"
                                  : "default"
                              }
                            >
                              {f.loadContext}
                            </Badge>
                            <span className="tabular-nums text-[var(--text)]">
                              {formatTokenCount(f.tokens)} tokens
                            </span>
                            <span className="tabular-nums text-[var(--text-muted)]">
                              {formatBytes(f.bytes)}
                            </span>
                          </div>
                        </div>
                      ))}
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* ── TAB: Cron & Jobs ─────────────────────────────────── */}
          <TabsContent value="cron">
            <Card>
              <CardHeader>
                <h2 className="text-lg font-semibold text-[var(--text)]">
                  Cron jobs & recurring processes
                </h2>
                <p className="mt-1 text-sm text-[var(--text-muted)]">
                  Estimated token consumption of periodic and event-driven
                  processes. Processes with 0 tokens do not consume LLM tokens
                  directly (they are pure API/infrastructure operations).
                </p>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {cronJobs.map((job) => (
                    <div
                      key={job.name}
                      className="rounded-xl border border-[var(--border)] p-4 transition hover:bg-[var(--surface-muted)]"
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <RefreshCw className="h-4 w-4 shrink-0 text-[var(--accent)]" />
                            <h3 className="text-sm font-semibold text-[var(--text)]">
                              {job.name}
                            </h3>
                          </div>
                          <p className="mt-1 text-sm text-[var(--text-muted)]">
                            {job.description}
                          </p>
                          <div className="mt-2 flex items-center gap-2">
                            <Timer className="h-3 w-3 text-[var(--text-quiet)]" />
                            <span className="text-xs text-[var(--text-quiet)]">
                              {job.frequency}
                            </span>
                          </div>
                        </div>
                        <div className="shrink-0 text-right">
                          {job.estimatedTotalTokens > 0 ? (
                            <>
                              <p className="text-lg font-bold tabular-nums text-[var(--text)]">
                                {formatTokenCount(job.estimatedTotalTokens)}
                              </p>
                              <p className="text-xs text-[var(--text-muted)]">
                                tokens / run
                              </p>
                              <div className="mt-1 text-xs text-[var(--text-quiet)]">
                                <span>
                                  ↑{formatTokenCount(job.estimatedInputTokens)}{" "}
                                  in
                                </span>
                                <span className="mx-1">·</span>
                                <span>
                                  ↓{formatTokenCount(job.estimatedOutputTokens)}{" "}
                                  out
                                </span>
                              </div>
                            </>
                          ) : (
                            <Badge variant="default">No LLM tokens</Badge>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>

                {/* Summary */}
                <div className="mt-6 rounded-xl border border-[var(--border)] bg-[var(--surface-muted)] p-4">
                  <div className="flex items-center gap-2">
                    <Lightbulb className="h-4 w-4 text-[var(--accent)]" />
                    <h3 className="text-sm font-semibold text-[var(--text)]">
                      Key insight
                    </h3>
                  </div>
                  <p className="mt-1 text-sm text-[var(--text-muted)]">
                    The CI pipeline and gateway heartbeat use zero LLM tokens —
                    they are pure infrastructure jobs. The biggest recurring
                    token costs come from{" "}
                    <strong>agent session refreshes</strong> (~10.5K
                    tokens/turn) and <strong>context pruning cycles</strong>{" "}
                    (~5.5K tokens each). Reducing boot context size directly
                    reduces the per-turn refresh cost.
                  </p>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* ── TAB: Optimization ────────────────────────────────── */}
          <TabsContent value="optimize">
            <div className="space-y-4">
              {/* Summary banner */}
              <Card className="border-[var(--accent)] bg-[var(--accent-soft)]">
                <CardContent className="pt-6">
                  <div className="flex items-center gap-4">
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-[var(--accent)] text-white">
                      <TrendingDown className="h-6 w-6" />
                    </div>
                    <div>
                      <h2 className="text-lg font-bold text-[var(--accent-strong)]">
                        Optimization audit: save ~
                        {formatTokenCount(totalSavings)} tokens per session
                      </h2>
                      <p className="mt-0.5 text-sm text-[var(--accent-strong)]">
                        {recommendations.length} recommendations identified ·{" "}
                        {formatCost(totalSavingsCost)} estimated savings per
                        session · no functionality loss
                      </p>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Recommendations */}
              {recommendations.map((rec, i) => (
                <RecommendationCard key={i} rec={rec} />
              ))}

              {/* Additional structural observations */}
              <Card>
                <CardHeader>
                  <h2 className="text-lg font-semibold text-[var(--text)]">
                    Structural audit notes
                  </h2>
                </CardHeader>
                <CardContent>
                  <div className="space-y-4 text-sm text-[var(--text-muted)]">
                    <div>
                      <h4 className="font-semibold text-[var(--text)]">
                        1. Duplicate content between AGENTS.md and
                        .github/copilot-instructions.md
                      </h4>
                      <p className="mt-1">
                        These two files are byte-for-byte identical (2,591 bytes
                        each). <code>AGENTS.md</code> is consumed by AI coding
                        agents at boot, while{" "}
                        <code>.github/copilot-instructions.md</code> is loaded
                        by GitHub Copilot on every interaction. Both are
                        injected into the same context window, doubling the cost
                        of the repository guidelines.
                      </p>
                    </div>
                    <div>
                      <h4 className="font-semibold text-[var(--text)]">
                        2. openclaw_baseline_config.md dominates the docs/
                        budget
                      </h4>
                      <p className="mt-1">
                        At 12,888 bytes (~3,222 tokens), this single file
                        accounts for{" "}
                        <strong>{toPercent(3222, totals.totalTokens)}</strong>{" "}
                        of total context file tokens. It contains a full JSON
                        config block (~200 lines) and then a verbose
                        line-by-line commentary that largely restates the same
                        information. A concise version could deliver the same
                        value at 30–40% of the size.
                      </p>
                    </div>
                    <div>
                      <h4 className="font-semibold text-[var(--text)]">
                        3. Sparse subdirectories add structural noise
                      </h4>
                      <p className="mt-1">
                        Five docs/ subdirectories contain only a stub README
                        under 1KB: <code>architecture</code> (209B),{" "}
                        <code>production</code> (33B),{" "}
                        <code>getting-started</code> (714B),{" "}
                        <code>troubleshooting</code> (325B),{" "}
                        <code>development</code> (954B). When an AI agent
                        indexes the project tree, each directory adds overhead
                        to context even before files are read. Consolidating
                        stubs into <code>docs/README.md</code> reduces both file
                        count and indexing cost.
                      </p>
                    </div>
                    <div>
                      <h4 className="font-semibold text-[var(--text)]">
                        4. Context pruning configuration is well-tuned
                      </h4>
                      <p className="mt-1">
                        The OpenClaw baseline config has good pruning defaults:
                        45-min TTL, soft-trim (900 head + 900 tail chars), and
                        hard-clear with placeholder. The{" "}
                        <code>reserveTokensFloor</code> of 12,000 provides a
                        healthy buffer. No changes needed here — the savings
                        should come from reducing what goes <em>into</em>{" "}
                        context, not how it&apos;s pruned.
                      </p>
                    </div>
                    <div>
                      <h4 className="font-semibold text-[var(--text)]">
                        5. Frontend README.md and backend/templates/README.md
                        are unexpectedly large
                      </h4>
                      <p className="mt-1">
                        At 4,966B and 5,657B respectively, these are the 2nd and
                        3rd largest .md files. They contain detailed development
                        guides that are rarely needed during normal AI-assisted
                        coding. Consider adding <code>.copilotignore</code>{" "}
                        rules to exclude them from automatic loading.
                      </p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </DashboardPageLayout>
  );
}
