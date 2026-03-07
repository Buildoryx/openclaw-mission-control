/**
 * Token estimation utilities for the Token Usage Dashboard.
 *
 * Uses the widely-accepted heuristic that 1 token ≈ 4 characters (English text)
 * and 1 token ≈ 0.75 words. These are rough estimates — actual tokenisation
 * depends on the model's BPE vocabulary — but they are reliable enough for
 * cost-awareness dashboards.
 *
 * References:
 *   - OpenAI tokenizer docs: "A helpful rule of thumb is that one token
 *     generally corresponds to ~4 characters of text for common English text."
 *   - Claude/Anthropic uses a similar BPE scheme with comparable ratios.
 */

// ── Constants ────────────────────────────────────────────────────────────────

/** Average characters per token (English prose / markdown). */
export const CHARS_PER_TOKEN = 4;

/** Average words per token. */
export const WORDS_PER_TOKEN = 0.75;

/**
 * Approximate cost per 1K input tokens by model family (USD).
 * These are *input* pricing as of mid-2025; update as needed.
 */
export const MODEL_INPUT_COST_PER_1K: Record<string, number> = {
  "claude-opus-4": 0.015,
  "claude-sonnet-4": 0.003,
  "claude-haiku-3.5": 0.0008,
  "gpt-4o": 0.005,
  "gpt-4.1": 0.002,
  "gpt-4.1-mini": 0.0004,
  "gpt-4.1-nano": 0.0001,
  "o3": 0.01,
  "o3-mini": 0.001,
  "o4-mini": 0.001,
  "gemini-2.5-pro": 0.00125,
  "gemini-2.5-flash": 0.00015,
  default: 0.003,
};

/**
 * Approximate cost per 1K output tokens by model family (USD).
 */
export const MODEL_OUTPUT_COST_PER_1K: Record<string, number> = {
  "claude-opus-4": 0.075,
  "claude-sonnet-4": 0.015,
  "claude-haiku-3.5": 0.004,
  "gpt-4o": 0.015,
  "gpt-4.1": 0.008,
  "gpt-4.1-mini": 0.0016,
  "gpt-4.1-nano": 0.0004,
  "o3": 0.04,
  "o3-mini": 0.004,
  "o4-mini": 0.004,
  "gemini-2.5-pro": 0.01,
  "gemini-2.5-flash": 0.0006,
  default: 0.015,
};

// ── Core estimation functions ────────────────────────────────────────────────

/** Estimate token count from byte size. */
export function estimateTokensFromBytes(bytes: number): number {
  return Math.ceil(bytes / CHARS_PER_TOKEN);
}

/** Estimate token count from character count. */
export function estimateTokensFromChars(chars: number): number {
  return Math.ceil(chars / CHARS_PER_TOKEN);
}

/** Estimate token count from word count. */
export function estimateTokensFromWords(words: number): number {
  return Math.ceil(words / WORDS_PER_TOKEN);
}

/** Estimate cost in USD for a given token count and model. */
export function estimateInputCost(
  tokens: number,
  model: string = "default",
): number {
  const rate =
    MODEL_INPUT_COST_PER_1K[model] ?? MODEL_INPUT_COST_PER_1K["default"];
  return (tokens / 1000) * rate;
}

/** Estimate output cost in USD for a given token count and model. */
export function estimateOutputCost(
  tokens: number,
  model: string = "default",
): number {
  const rate =
    MODEL_OUTPUT_COST_PER_1K[model] ?? MODEL_OUTPUT_COST_PER_1K["default"];
  return (tokens / 1000) * rate;
}

// ── Formatting helpers ───────────────────────────────────────────────────────

/** Format a token count into a compact human-readable string. */
export function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${(tokens / 1_000_000).toFixed(1)}M`;
  }
  if (tokens >= 1_000) {
    return `${(tokens / 1_000).toFixed(1)}K`;
  }
  return tokens.toLocaleString("en-US");
}

/** Format a byte size into a human-readable string. */
export function formatBytes(bytes: number): string {
  if (bytes >= 1_048_576) {
    return `${(bytes / 1_048_576).toFixed(1)} MB`;
  }
  if (bytes >= 1_024) {
    return `${(bytes / 1_024).toFixed(1)} KB`;
  }
  return `${bytes} B`;
}

/** Format a USD cost as a short currency string. */
export function formatCost(usd: number): string {
  if (usd < 0.01) {
    return `$${usd.toFixed(4)}`;
  }
  if (usd < 1) {
    return `$${usd.toFixed(3)}`;
  }
  return `$${usd.toFixed(2)}`;
}

// ── File inventory types ─────────────────────────────────────────────────────

export type ContextFileEntry = {
  /** Relative path from repo root. */
  path: string;
  /** Parent directory (logical grouping). */
  directory: string;
  /** File name only. */
  name: string;
  /** Raw size in bytes. */
  bytes: number;
  /** Word count. */
  words: number;
  /** Line count. */
  lines: number;
  /** Estimated tokens. */
  tokens: number;
  /** Estimated input cost (default model). */
  cost: number;
  /** When this file is typically loaded (boot / on-demand / cron / always). */
  loadContext: "boot" | "on-demand" | "cron" | "always" | "ci";
};

export type ContextDirectory = {
  name: string;
  path: string;
  files: ContextFileEntry[];
  totalBytes: number;
  totalTokens: number;
  totalCost: number;
};

export type DailyUsageEntry = {
  date: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  inputCost: number;
  outputCost: number;
  totalCost: number;
  sessions: number;
};

export type CronJobUsage = {
  name: string;
  description: string;
  frequency: string;
  estimatedInputTokens: number;
  estimatedOutputTokens: number;
  estimatedTotalTokens: number;
  estimatedDailyCost: number;
  runsPerDay: number;
};

export type SessionBootCost = {
  label: string;
  description: string;
  tokens: number;
  cost: number;
};

// ── Static inventory of .md context files ────────────────────────────────────

/**
 * Build the full inventory of markdown context files from raw size data.
 * This is called with pre-measured file metadata.
 */
export function buildContextInventory(
  files: Array<{
    path: string;
    bytes: number;
    words: number;
    lines: number;
    loadContext: ContextFileEntry["loadContext"];
  }>,
): ContextFileEntry[] {
  return files.map((f) => {
    const parts = f.path.split("/");
    const name = parts[parts.length - 1];
    const directory = parts.length > 1 ? parts.slice(0, -1).join("/") : ".";
    const tokens = estimateTokensFromBytes(f.bytes);
    const cost = estimateInputCost(tokens);

    return {
      path: f.path,
      directory,
      name,
      bytes: f.bytes,
      words: f.words,
      lines: f.lines,
      tokens,
      cost,
      loadContext: f.loadContext,
    };
  });
}

/**
 * Group an inventory into logical directories for the collapsible tree view.
 */
export function groupByDirectory(
  entries: ContextFileEntry[],
): ContextDirectory[] {
  const map = new Map<string, ContextFileEntry[]>();

  for (const entry of entries) {
    const existing = map.get(entry.directory);
    if (existing) {
      existing.push(entry);
    } else {
      map.set(entry.directory, [entry]);
    }
  }

  const directories: ContextDirectory[] = [];
  for (const [dirPath, files] of map) {
    const totalBytes = files.reduce((s, f) => s + f.bytes, 0);
    const totalTokens = files.reduce((s, f) => s + f.tokens, 0);
    const totalCost = files.reduce((s, f) => s + f.cost, 0);
    const name = dirPath === "." ? "(root)" : dirPath;

    directories.push({ name, path: dirPath, files, totalBytes, totalTokens, totalCost });
  }

  directories.sort((a, b) => b.totalTokens - a.totalTokens);
  return directories;
}

/**
 * Compute totals across all entries.
 */
export function computeTotals(entries: ContextFileEntry[]): {
  totalBytes: number;
  totalTokens: number;
  totalCost: number;
  totalFiles: number;
} {
  return {
    totalBytes: entries.reduce((s, f) => s + f.bytes, 0),
    totalTokens: entries.reduce((s, f) => s + f.tokens, 0),
    totalCost: entries.reduce((s, f) => s + f.cost, 0),
    totalFiles: entries.length,
  };
}

/**
 * Compute session boot cost from the files that are loaded on every new session.
 */
export function computeSessionBootCost(
  entries: ContextFileEntry[],
): SessionBootCost[] {
  const bootFiles = entries.filter(
    (f) => f.loadContext === "boot" || f.loadContext === "always",
  );
  const systemPromptTokens = 1500; // approximate system prompt overhead
  const toolDefinitionsTokens = 800; // tool schemas injected at boot

  const costs: SessionBootCost[] = [
    {
      label: "System prompt",
      description: "Base system instructions injected by the AI provider",
      tokens: systemPromptTokens,
      cost: estimateInputCost(systemPromptTokens),
    },
    {
      label: "Tool definitions",
      description: "Function/tool schemas loaded into context at session start",
      tokens: toolDefinitionsTokens,
      cost: estimateInputCost(toolDefinitionsTokens),
    },
  ];

  for (const file of bootFiles) {
    costs.push({
      label: file.path,
      description: `Loaded at ${file.loadContext === "always" ? "every interaction" : "session boot"}`,
      tokens: file.tokens,
      cost: file.cost,
    });
  }

  return costs;
}

/**
 * Estimate cron job token usage based on known job patterns.
 */
export function buildCronJobEstimates(): CronJobUsage[] {
  return [
    {
      name: "CI Pipeline (ci.yml)",
      description:
        "Full CI check triggered on every PR and push to master. " +
        "Includes lint, typecheck, test, build, installer smoke tests, and E2E.",
      frequency: "Per PR / push to master",
      estimatedInputTokens: 0,
      estimatedOutputTokens: 0,
      estimatedTotalTokens: 0,
      estimatedDailyCost: 0,
      runsPerDay: 0,
    },
    {
      name: "Gateway health heartbeat",
      description:
        "Periodic gateway status polling from the Dashboard page " +
        "(healthz endpoint + gateway status queries). No LLM tokens consumed directly.",
      frequency: "Every 30s while dashboard is open",
      estimatedInputTokens: 0,
      estimatedOutputTokens: 0,
      estimatedTotalTokens: 0,
      estimatedDailyCost: 0,
      runsPerDay: 0,
    },
    {
      name: "Agent session refresh",
      description:
        "When an agent session is active, context is refreshed on each turn. " +
        "Boot files + conversation history are re-sent as input tokens.",
      frequency: "Per agent turn",
      estimatedInputTokens: 8500,
      estimatedOutputTokens: 2000,
      estimatedTotalTokens: 10500,
      estimatedDailyCost: 0,
      runsPerDay: 0,
    },
    {
      name: "Context pruning cycle",
      description:
        "Triggered when conversation context approaches the token limit. " +
        "Compacts history and flushes durable memories.",
      frequency: "Every ~45 min per active session (configurable TTL)",
      estimatedInputTokens: 4000,
      estimatedOutputTokens: 1500,
      estimatedTotalTokens: 5500,
      estimatedDailyCost: 0,
      runsPerDay: 0,
    },
    {
      name: "Memory flush (pre-compaction)",
      description:
        "Saves durable notes before conversation compaction kicks in. " +
        "Triggered when softThresholdTokens (5000) is approached.",
      frequency: "Before each compaction event",
      estimatedInputTokens: 3000,
      estimatedOutputTokens: 800,
      estimatedTotalTokens: 3800,
      estimatedDailyCost: 0,
      runsPerDay: 0,
    },
    {
      name: "QMD memory retrieval",
      description:
        "Queries the QMD memory backend on boot and periodically (every 15 min). " +
        "Injects up to 1800 chars (~450 tokens) of memory snippets.",
      frequency: "On boot + every 15 min per active session",
      estimatedInputTokens: 450,
      estimatedOutputTokens: 0,
      estimatedTotalTokens: 450,
      estimatedDailyCost: 0,
      runsPerDay: 0,
    },
  ];
}

/**
 * Generate simulated daily usage data for the last N days, broken down by model.
 * In production this would come from an API; here we generate realistic estimates
 * based on the project's known activity patterns.
 */
export function generateDailyUsageEstimates(days: number = 5): DailyUsageEntry[] {
  const models = ["claude-sonnet-4", "claude-opus-4", "claude-haiku-3.5"];
  const entries: DailyUsageEntry[] = [];
  const now = new Date();

  for (let d = days - 1; d >= 0; d--) {
    const date = new Date(now);
    date.setDate(date.getDate() - d);
    const dateStr = date.toISOString().split("T")[0];

    // Weekend vs weekday pattern
    const dayOfWeek = date.getDay();
    const isWeekday = dayOfWeek > 0 && dayOfWeek < 6;
    const activityMultiplier = isWeekday ? 1.0 : 0.3;

    for (const model of models) {
      // Different models have different usage profiles
      let baseInput: number;
      let baseOutput: number;
      let baseSessions: number;

      switch (model) {
        case "claude-sonnet-4":
          baseInput = 85000;
          baseOutput = 32000;
          baseSessions = 12;
          break;
        case "claude-opus-4":
          baseInput = 25000;
          baseOutput = 15000;
          baseSessions = 3;
          break;
        case "claude-haiku-3.5":
          baseInput = 40000;
          baseOutput = 12000;
          baseSessions = 8;
          break;
        default:
          baseInput = 20000;
          baseOutput = 8000;
          baseSessions = 5;
      }

      // Add some daily variance (±30%)
      const variance = 0.7 + Math.random() * 0.6;
      const inputTokens = Math.round(baseInput * activityMultiplier * variance);
      const outputTokens = Math.round(baseOutput * activityMultiplier * variance);
      const sessions = Math.max(
        1,
        Math.round(baseSessions * activityMultiplier * variance),
      );

      const inputCost = estimateInputCost(inputTokens, model);
      const outputCost = estimateOutputCost(outputTokens, model);

      entries.push({
        date: dateStr,
        model,
        inputTokens,
        outputTokens,
        totalTokens: inputTokens + outputTokens,
        inputCost,
        outputCost,
        totalCost: inputCost + outputCost,
        sessions,
      });
    }
  }

  return entries;
}

// ── Percentage / proportion helpers ──────────────────────────────────────────

/** Return a percentage string like "42.1%" */
export function toPercent(part: number, whole: number): string {
  if (whole === 0) return "0%";
  return `${((part / whole) * 100).toFixed(1)}%`;
}

/**
 * Classify token efficiency of a file.
 * Returns a severity intended for color-coding in the UI.
 */
export function tokenEfficiencyClass(
  tokens: number,
): "low" | "moderate" | "high" | "critical" {
  if (tokens < 500) return "low";
  if (tokens < 1500) return "moderate";
  if (tokens < 4000) return "high";
  return "critical";
}
