"use client";

import DropdownSelect, {
  type DropdownSelectOption,
} from "@/components/ui/dropdown-select";
import { cn } from "@/lib/utils";

export type SearchableSelectOption = DropdownSelectOption;

type SearchableSelectProps = {
  value?: string;
  onValueChange: (value: string) => void;
  options: SearchableSelectOption[];
  placeholder?: string;
  ariaLabel: string;
  disabled?: boolean;
  triggerClassName?: string;
  contentClassName?: string;
  itemClassName?: string;
  searchEnabled?: boolean;
  searchPlaceholder?: string;
  emptyMessage?: string;
};

const baseTriggerClassName =
  "w-auto h-auto rounded-xl border-2 border-[var(--input-border)] bg-[var(--input-bg)] px-4 py-3 text-left text-sm font-semibold text-[var(--text)] shadow-sm transition-all duration-200 hover:border-[var(--input-border-strong)] focus:border-[var(--text)] focus:ring-4 focus:ring-[var(--accent-soft)]";
const baseContentClassName =
  "rounded-xl border-2 border-[var(--popover-border)] bg-[var(--popover-bg)] shadow-xl";
const baseItemClassName =
  "px-4 py-3 text-sm text-[var(--text-muted)] transition-colors data-[selected=true]:bg-[var(--surface-strong)] data-[selected=true]:text-[var(--text)] data-[selected=true]:font-semibold hover:bg-[var(--surface-muted)]";

export default function SearchableSelect({
  value,
  onValueChange,
  options,
  placeholder,
  ariaLabel,
  disabled = false,
  triggerClassName,
  contentClassName,
  itemClassName,
  searchEnabled,
  searchPlaceholder,
  emptyMessage,
}: SearchableSelectProps) {
  return (
    <DropdownSelect
      value={value}
      onValueChange={onValueChange}
      options={options}
      placeholder={placeholder}
      ariaLabel={ariaLabel}
      disabled={disabled}
      triggerClassName={cn(baseTriggerClassName, triggerClassName)}
      contentClassName={cn(baseContentClassName, contentClassName)}
      itemClassName={cn(baseItemClassName, itemClassName)}
      searchEnabled={searchEnabled}
      searchPlaceholder={searchPlaceholder}
      emptyMessage={emptyMessage}
    />
  );
}
