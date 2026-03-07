import { SignInButton } from "@/auth/clerk";

import { Button } from "@/components/ui/button";

type SignedOutPanelProps = {
  message: string;
  forceRedirectUrl: string;
  signUpForceRedirectUrl?: string;
  mode?: "modal" | "redirect";
  buttonLabel?: string;
  buttonTestId?: string;
};

export function SignedOutPanel({
  message,
  forceRedirectUrl,
  signUpForceRedirectUrl,
  mode = "modal",
  buttonLabel = "Sign in",
  buttonTestId,
}: SignedOutPanelProps) {
  return (
    <div className="col-span-2 flex min-h-[calc(100vh-64px)] items-center justify-center page-bg p-10 text-center">
      <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] px-8 py-6 shadow-sm">
        <p className="text-sm text-[var(--text-muted)]">{message}</p>
        <SignInButton
          mode={mode}
          forceRedirectUrl={forceRedirectUrl}
          signUpForceRedirectUrl={signUpForceRedirectUrl}
        >
          <Button className="mt-4" data-testid={buttonTestId}>
            {buttonLabel}
          </Button>
        </SignInButton>
      </div>
    </div>
  );
}
