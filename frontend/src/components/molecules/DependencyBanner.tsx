import { type ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface DependencyBannerDependency {
  id: string;
  title: string;
  statusLabel: string;
  isBlocking?: boolean;
  isDone?: boolean;
  onClick?: () => void;
  disabled?: boolean;
}

interface DependencyBannerProps {
  variant?: DependencyBannerVariant;
  dependencies?: DependencyBannerDependency[];
  children?: ReactNode;
  className?: string;
  emptyMessage?: string;
}

type DependencyBannerVariant = "blocked" | "resolved";

const toneClassByVariant: Record<DependencyBannerVariant, string> = {
  blocked: "pill-rose border",
  resolved: "pill-blue border",
};

export function DependencyBanner({
  variant = "blocked",
  dependencies = [],
  children,
  className,
  emptyMessage = "No dependencies.",
}: DependencyBannerProps) {
  return (
    <div className={cn("space-y-2", className)}>
      {dependencies.length > 0 ? (
        dependencies.map((dependency) => {
          const isBlocking = dependency.isBlocking === true;
          const isDone = dependency.isDone === true;
          return (
            <button
              key={dependency.id}
              type="button"
              onClick={dependency.onClick}
              disabled={dependency.disabled}
              className={cn(
                "w-full rounded-lg border px-3 py-2 text-left transition",
                isBlocking
                  ? "border alert-danger hover:bg-rose-100/40"
                  : isDone
                    ? "border alert-success hover:bg-emerald-100/40"
                    : "border-[var(--border)] bg-[var(--surface)] hover:page-bg",
                dependency.disabled && "cursor-not-allowed opacity-60",
              )}
            >
              <div className="flex items-center justify-between gap-3">
                <p className="truncate text-sm font-medium text-[var(--text)]">
                  {dependency.title}
                </p>
                <span
                  className={cn(
                    "text-[10px] font-semibold uppercase tracking-wide",
                    isBlocking
                      ? "text-[var(--danger)]"
                      : isDone
                        ? "text-[var(--success)]"
                        : "text-[var(--text-muted)]",
                  )}
                >
                  {dependency.statusLabel}
                </span>
              </div>
            </button>
          );
        })
      ) : (
        <p className="text-sm text-[var(--text-muted)]">{emptyMessage}</p>
      )}
      {children ? (
        <div
          className={cn(
            "rounded-lg border p-3 text-xs",
            toneClassByVariant[variant],
          )}
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}
