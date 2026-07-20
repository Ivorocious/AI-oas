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
});
