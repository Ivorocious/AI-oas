import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, selectedDemoPersona, signIn, signOut, tokenPresent } from "./api";

describe("browser-memory demo API client", () => {
  beforeEach(() => {
    signOut();
    vi.unstubAllGlobals();
  });

  it("keeps the issued access token in module memory and attaches it only to API calls", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ access_token: "memory-only" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ result: { id: "request-1" } }),
      });
    vi.stubGlobal("fetch", fetch);

    await signIn("manager");
    expect(tokenPresent()).toBe(true);
    expect(selectedDemoPersona()).toBe("manager");
    await expect(api("/api/v1/service-requests/request-1")).resolves.toEqual({
      result: { id: "request-1" },
    });
    expect(fetch.mock.calls[0][0]).toBe("/demo-auth/token");
    expect(fetch.mock.calls[1][1].headers).toEqual({
      Authorization: "Bearer memory-only",
    });

    signOut();
    expect(tokenPresent()).toBe(false);
    await expect(api("/api/v1/service-requests/request-1")).rejects.toThrow(
      "Sign in is required.",
    );
  });

  it("clears an expired session and reports a safe session error", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ access_token: "expired" }),
      })
      .mockResolvedValueOnce({
        status: 401,
        ok: false,
        json: async () => ({ error: { code: "TOKEN_EXPIRED" } }),
      });
    vi.stubGlobal("fetch", fetch);
    await signIn("manager");
    await expect(api("/api/v1/service-requests")).rejects.toMatchObject({
      status: 401,
      code: "TOKEN_EXPIRED",
    });
    expect(tokenPresent()).toBe(false);
    expect(selectedDemoPersona()).toBeNull();
  });

  it("preserves opaque cursors and filters when the caller supplies the next URL", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ access_token: "operations-memory" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          result: { items: [], page: { next_cursor: null } },
        }),
      });
    vi.stubGlobal("fetch", fetch);
    await signIn("operations");
    await api(
      "/api/v1/service-requests?status=AwaitingApproval&limit=1&cursor=opaque.next.cursor",
    );
    expect(fetch.mock.calls[1][0]).toContain("status=AwaitingApproval");
    expect(fetch.mock.calls[1][0]).toContain("cursor=opaque.next.cursor");
  });

  it("maps transient and conflict responses without exposing internals", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ access_token: "memory-only" }),
      })
      .mockResolvedValueOnce({
        status: 409,
        ok: false,
        json: async () => ({
          error: {
            code: "STALE_VERSION",
            message: "Refresh required",
            retryable: false,
            current_versions: { service_request: 3 },
          },
        }),
      });
    vi.stubGlobal("fetch", fetch);
    await signIn("manager");
    await expect(api("/decision", { method: "POST" })).rejects.toMatchObject({
      status: 409,
      code: "STALE_VERSION",
      currentVersions: { service_request: 3 },
    });
    expect(await Promise.resolve(fetch.mock.calls[1][0])).toBe("/decision");
  });
});
