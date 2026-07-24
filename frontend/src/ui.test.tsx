// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { signIn, signOut } from "./api";
import { App } from "./ui";

function response(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function renderApp(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("recruiter-facing operator dashboard", () => {
  beforeEach(() => {
    signOut();
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    cleanup();
    signOut();
  });

  it("orients the observer to backend authority and distinct synthetic roles", () => {
    renderApp("/sign-in");

    expect(
      screen.getByRole("heading", {
        name: "Review the decision, then trust the evidence.",
      }),
    ).toBeVisible();
    expect(
      screen.getByText(
        /Roles and permissions are resolved and enforced by the backend/,
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: /ManagerApprover/ }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: /OperationsAgent/ }),
    ).toBeVisible();
  });

  it("returns an expired in-memory session to sign-in with a meaningful announcement", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response({ access_token: crypto.randomUUID() }))
      .mockResolvedValueOnce(
        response({ error: { code: "TOKEN_EXPIRED" } }, 401),
      );
    vi.stubGlobal("fetch", fetch);
    await signIn("manager");

    renderApp("/requests");

    expect(
      await screen.findByText(
        "Your session expired. Sign in again to continue safely.",
      ),
    ).toHaveAttribute("role", "status");
    expect(
      screen.getByRole("heading", { name: "Enter the operator dashboard" }),
    ).toBeVisible();
  });

  it("explains the rejection rationale requirement before issuing a command", async () => {
    const requestId = "11111111-1111-4111-8111-111111111111";
    const proposalId = "22222222-2222-4222-8222-222222222222";
    const fetch = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === "/demo-auth/token")
          return response({ access_token: crypto.randomUUID() });
        if (path === `/api/v1/service-requests/${requestId}`)
          return response({
            result: {
              service_request: {
                id: requestId,
                status: "AwaitingApproval",
                priority: "High",
                current_queue: "HumanReview",
                version: 6,
              },
            },
          });
        if (path.endsWith("/proposed-actions"))
          return response({
            result: {
              items: [
                {
                  id: proposalId,
                  state: "PendingApproval",
                  version: 2,
                  payload_digest: "a".repeat(64),
                  content:
                    "Synthetic proposal content for focused UI validation.",
                  destination_value: "synthetic@example.com",
                  proposal_number: 1,
                },
              ],
            },
          });
        if (path.endsWith("/approvals"))
          return response({ result: { items: [] } });
        if (
          path.endsWith("/timeline") ||
          path.endsWith("/ai-interpretations") ||
          path.endsWith("/routing-decisions")
        )
          return response({ result: { items: [] } });
        if (init?.method === "POST")
          throw new Error(
            "A decision command must not be sent for invalid input.",
          );
        return response({ error: { code: "UNEXPECTED_TEST_PATH" } }, 500);
      },
    );
    vi.stubGlobal("fetch", fetch);
    await signIn("manager");
    renderApp(`/requests/${requestId}`);

    fireEvent.click(
      await screen.findByRole("button", { name: "Reject with rationale" }),
    );

    expect(
      await screen.findByText(
        "A rejection rationale of at least 20 characters is required.",
      ),
    ).toBeVisible();
    await waitFor(() => {
      expect(
        fetch.mock.calls.some(
          ([path, init]) =>
            String(path).includes("/commands/reject") &&
            (init as RequestInit | undefined)?.method === "POST",
        ),
      ).toBe(false);
    });
  });
});
