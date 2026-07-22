export type Persona = "manager" | "operations";

let accessToken: string | null = null;
let selectedPersona: Persona | null = null;

export class ApiError extends Error {
  code?: string;
  status: number;
  retryable: boolean;
  currentVersions?: Record<string, number>;

  constructor(
    message: string,
    status: number,
    body?: {
      error?: {
        code?: string;
        retryable?: boolean;
        current_versions?: Record<string, number>;
      };
    },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.error?.code;
    this.retryable = Boolean(body?.error?.retryable) || status >= 500;
    this.currentVersions = body?.error?.current_versions;
  }
}

export function tokenPresent() {
  return accessToken !== null;
}
export function selectedDemoPersona() {
  return selectedPersona;
}

function announceSessionExpired() {
  accessToken = null;
  selectedPersona = null;
  if (typeof window !== "undefined")
    window.dispatchEvent(new Event("demo-auth-expired"));
}

export async function signIn(persona: Persona) {
  const response = await fetch("/demo-auth/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ persona }),
  });
  if (!response.ok)
    throw new ApiError(
      "Local demo authentication is unavailable.",
      response.status,
    );
  const body = (await response.json()) as { access_token: string };
  accessToken = body.access_token;
  selectedPersona = persona;
}

export function signOut() {
  accessToken = null;
  selectedPersona = null;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (!accessToken)
    throw new ApiError("Sign in is required.", 401, {
      error: { code: "SESSION_REQUIRED" },
    });
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { Authorization: `Bearer ${accessToken}`, ...init.headers },
    });
  } catch {
    throw new ApiError("The service could not be reached. Try again.", 0, {
      error: { code: "NETWORK_ERROR", retryable: true },
    });
  }
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) {
    announceSessionExpired();
    throw new ApiError("Your session expired. Sign in again.", 401, body);
  }
  if (!response.ok)
    throw new ApiError(
      body?.error?.message ?? "The request could not be completed safely.",
      response.status,
      body,
    );
  return body as T;
}

export function idempotencyKey() {
  return crypto.randomUUID();
}
