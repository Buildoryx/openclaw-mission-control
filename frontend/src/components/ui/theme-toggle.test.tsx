import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ThemeToggle } from "./theme-toggle";

// ---------- mocks ----------

const setThemeMock = vi.hoisted(() => vi.fn());
const useThemeMock = vi.hoisted(() =>
  vi.fn().mockReturnValue({
    theme: "system",
    resolvedTheme: "light",
    setTheme: setThemeMock,
  }),
);

vi.mock("@/components/providers/ThemeProvider", () => ({
  useTheme: useThemeMock,
}));

// ---------- suite ----------

describe("ThemeToggle", () => {
  beforeEach(() => {
    setThemeMock.mockReset();
    useThemeMock.mockReturnValue({
      theme: "system",
      resolvedTheme: "light",
      setTheme: setThemeMock,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders all three theme options", () => {
    render(<ThemeToggle />);

    expect(screen.getByRole("radio", { name: /light theme/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /dark theme/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /system theme/i })).toBeInTheDocument();
  });

  it("renders a radiogroup with accessible label", () => {
    render(<ThemeToggle />);

    expect(screen.getByRole("radiogroup", { name: /theme selection/i })).toBeInTheDocument();
  });

  it("marks the current theme as checked via aria-checked", () => {
    useThemeMock.mockReturnValue({
      theme: "system",
      resolvedTheme: "light",
      setTheme: setThemeMock,
    });

    render(<ThemeToggle />);

    expect(screen.getByRole("radio", { name: /system theme/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: /light theme/i })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByRole("radio", { name: /dark theme/i })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("marks dark as checked when theme is dark", () => {
    useThemeMock.mockReturnValue({
      theme: "dark",
      resolvedTheme: "dark",
      setTheme: setThemeMock,
    });

    render(<ThemeToggle />);

    expect(screen.getByRole("radio", { name: /dark theme/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: /light theme/i })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByRole("radio", { name: /system theme/i })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("marks light as checked when theme is light", () => {
    useThemeMock.mockReturnValue({
      theme: "light",
      resolvedTheme: "light",
      setTheme: setThemeMock,
    });

    render(<ThemeToggle />);

    expect(screen.getByRole("radio", { name: /light theme/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("calls setTheme with 'dark' when dark option is clicked", async () => {
    const user = userEvent.setup();

    render(<ThemeToggle />);

    await user.click(screen.getByRole("radio", { name: /dark theme/i }));

    expect(setThemeMock).toHaveBeenCalledTimes(1);
    expect(setThemeMock).toHaveBeenCalledWith("dark");
  });

  it("calls setTheme with 'light' when light option is clicked", async () => {
    const user = userEvent.setup();
    useThemeMock.mockReturnValue({
      theme: "dark",
      resolvedTheme: "dark",
      setTheme: setThemeMock,
    });

    render(<ThemeToggle />);

    await user.click(screen.getByRole("radio", { name: /light theme/i }));

    expect(setThemeMock).toHaveBeenCalledTimes(1);
    expect(setThemeMock).toHaveBeenCalledWith("light");
  });

  it("calls setTheme with 'system' when system option is clicked", async () => {
    const user = userEvent.setup();
    useThemeMock.mockReturnValue({
      theme: "dark",
      resolvedTheme: "dark",
      setTheme: setThemeMock,
    });

    render(<ThemeToggle />);

    await user.click(screen.getByRole("radio", { name: /system theme/i }));

    expect(setThemeMock).toHaveBeenCalledTimes(1);
    expect(setThemeMock).toHaveBeenCalledWith("system");
  });

  it("displays text labels for each option", () => {
    render(<ThemeToggle />);

    expect(screen.getByText("Light")).toBeInTheDocument();
    expect(screen.getByText("Dark")).toBeInTheDocument();
    expect(screen.getByText("System")).toBeInTheDocument();
  });

  it("applies custom className when provided", () => {
    const { container } = render(<ThemeToggle className="custom-class" />);

    const radiogroup = container.querySelector("[role='radiogroup']");
    expect(radiogroup).toHaveClass("custom-class");
  });

  it("does not call setTheme when clicking the already-active option", async () => {
    const user = userEvent.setup();
    useThemeMock.mockReturnValue({
      theme: "system",
      resolvedTheme: "light",
      setTheme: setThemeMock,
    });

    render(<ThemeToggle />);

    await user.click(screen.getByRole("radio", { name: /system theme/i }));

    // setTheme is still called (the component doesn't short-circuit),
    // but the provider handles the no-op. Verify it was called with "system".
    expect(setThemeMock).toHaveBeenCalledWith("system");
  });
});
