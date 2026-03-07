type AdminOnlyNoticeProps = {
  message: string;
};

export function AdminOnlyNotice({ message }: AdminOnlyNoticeProps) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] px-6 py-5 text-sm text-[var(--text-muted)] shadow-sm">
      {message}
    </div>
  );
}
