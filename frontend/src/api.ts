export type Persona = "manager" | "operations";

let accessToken: string | null = null;
let selectedPersona: Persona | null = null;
export function tokenPresent() {
  return accessToken !== null;
}
export function selectedDemoPersona() {
  return selectedPersona;
}
export async function signIn(persona: Persona) {
  const response = await fetch("/demo-auth/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ persona }),
  });
  if (!response.ok)
    throw new Error("Local demo authentication is unavailable.");
  const body = (await response.json()) as { access_token: string };
  accessToken = body.access_token;
  selectedPersona = persona;
}
export function signOut() {
  accessToken = null;
  selectedPersona = null;
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (!accessToken) throw new Error("Sign in is required.");
  const response = await fetch(path, {
    ...init,
    headers: { Authorization: `Bearer ${accessToken}`, ...init.headers },
  });
  const body = await response.json();
  if (!response.ok)
    throw Object.assign(new Error(body?.error?.message ?? "Request failed."), {
      code: body?.error?.code,
    });
  return body as T;
}
export function idempotencyKey() {
  return crypto.randomUUID();
}
