import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UseQueryResult } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { Modal, Pagination, QueryBoundary, SecretReveal, StatusBadge } from "./ui";

describe("StatusBadge", () => {
  it.each([
    ["pending", "Pending"],
    ["succeeded", "Delivered"],
    ["dead", "Dead"],
  ] as const)("%s reads as %s", (status, label) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

describe("Modal", () => {
  it("is an accessible, labelled dialog that takes focus", () => {
    render(
      <Modal title="Edit endpoint" onClose={() => {}}>
        <p>body</p>
      </Modal>,
    );
    const dialog = screen.getByRole("dialog", { name: "Edit endpoint" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveFocus();
  });

  it("closes on Escape and on a backdrop click, but not on a click inside", async () => {
    const onClose = vi.fn();
    render(
      <Modal title="T" onClose={onClose}>
        <button>inside</button>
      </Modal>,
    );
    await userEvent.click(screen.getByText("inside"));
    expect(onClose).not.toHaveBeenCalled();

    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);

    const backdrop = screen.getByRole("dialog").parentElement as HTMLElement;
    fireEvent.mouseDown(backdrop);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("returns focus to the element that opened it", () => {
    const opener = document.createElement("button");
    document.body.append(opener);
    opener.focus();
    const { unmount } = render(<Modal title="T" onClose={() => {}}>x</Modal>);
    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });
});

describe("SecretReveal", () => {
  it("shows the secret with a one-time warning and lets the user copy it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const onClose = vi.fn();
    render(<SecretReveal title="Signing secret" value="whsec_abc123" onClose={onClose} />);

    expect(screen.getByText("whsec_abc123")).toBeInTheDocument();
    expect(screen.getByText(/shown only once/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("whsec_abc123");
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "I have saved it" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("does not crash when the clipboard is unavailable", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
      configurable: true,
    });
    render(<SecretReveal title="Key" value="hh_x" onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument(); // still usable
  });
});

describe("QueryBoundary", () => {
  const query = (partial: Partial<UseQueryResult<string>>) => partial as UseQueryResult<string>;

  it("shows a spinner while loading", () => {
    render(<QueryBoundary query={query({ isPending: true, isError: false })}>{() => <p>data</p>}</QueryBoundary>);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText("data")).not.toBeInTheDocument();
  });

  it("shows the error message", () => {
    render(
      <QueryBoundary query={query({ isPending: false, isError: true, error: new Error("Delivery not found") })}>
        {() => <p>data</p>}
      </QueryBoundary>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Delivery not found");
  });

  it("renders the data once loaded", () => {
    render(
      <QueryBoundary query={query({ isPending: false, isError: false, data: "hello" })}>
        {(value) => <p>{value} world</p>}
      </QueryBoundary>,
    );
    expect(screen.getByText("hello world")).toBeInTheDocument();
  });
});

describe("Pagination", () => {
  it("renders nothing when there is a single page", () => {
    const { container } = render(<Pagination page={0} hasNext={false} onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("disables unavailable directions and reports changes", async () => {
    const onChange = vi.fn();
    render(<Pagination page={0} hasNext onChange={onChange} />);
    expect(screen.getByRole("button", { name: /newer/i })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /older/i }));
    expect(onChange).toHaveBeenCalledWith(1);
  });
});
