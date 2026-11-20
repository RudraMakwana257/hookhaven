import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import LoginPage from "./page";

const replace = vi.fn();
const login = vi.fn();
const register = vi.fn();
let authStatus: "anonymous" | "authenticated" = "anonymous";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/auth", () => ({
  API_URL: "http://api.test",
  useAuth: () => ({ state: { status: authStatus }, login, register }),
}));

beforeEach(() => {
  authStatus = "anonymous";
  replace.mockReset();
  login.mockReset();
  register.mockReset();
});

async function fill(email: string, password: string) {
  await userEvent.type(screen.getByLabelText("Email"), email);
  await userEvent.type(screen.getByLabelText("Password"), password);
}

describe("LoginPage", () => {
  it("signs in and goes to the dashboard", async () => {
    login.mockResolvedValue(undefined);
    render(<LoginPage />);
    await fill("dev@example.com", "correct-horse-1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(login).toHaveBeenCalledWith("dev@example.com", "correct-horse-1");
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("shows the server's message when credentials are wrong", async () => {
    login.mockRejectedValue(new ApiError(401, "Invalid email or password"));
    render(<LoginPage />);
    await fill("dev@example.com", "nope-nope-1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
    expect(replace).not.toHaveBeenCalled();
  });

  it("tells the user how long to wait when rate limited", async () => {
    login.mockRejectedValue(new ApiError(429, "Rate limit exceeded", 42));
    render(<LoginPage />);
    await fill("dev@example.com", "whatever-1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 42s");
  });

  it("explains an unreachable API instead of a cryptic fetch error", async () => {
    login.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<LoginPage />);
    await fill("dev@example.com", "whatever-1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the API at http://api.test");
  });

  it("switches to registration and enforces the password rule in the browser", async () => {
    register.mockResolvedValue(undefined);
    render(<LoginPage />);
    await userEvent.click(screen.getByRole("button", { name: "Create an account" }));

    expect(screen.getByRole("heading", { name: "Create your account" })).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveAttribute("minLength", "8");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "new-password");

    await fill("new@example.com", "long-enough-pw");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(register).toHaveBeenCalledWith("new@example.com", "long-enough-pw");
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("sends signed-in users straight to the dashboard", () => {
    authStatus = "authenticated";
    render(<LoginPage />);
    expect(replace).toHaveBeenCalledWith("/");
  });
});
