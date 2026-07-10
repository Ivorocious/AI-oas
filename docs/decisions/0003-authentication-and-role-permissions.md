# ADR 0003: Authentication and Role Permissions

- Status: Accepted
- Date: 2026-07-10
- Scope: MVP technical design

## Context

The API and event contracts establish one FastAPI command boundary, but they require explicit identity and permission rules before implementation. Public intake must remain open without turning submission identifiers into read credentials. Human operations and approval need fixed authority, while n8n requires enough machine access to orchestrate attempts without becoming a lifecycle authority.

Approval also requires separation of duties. An endpoint-level approval permission is insufficient if a manager or administrator can approve content they created or materially revised. Machine callbacks need narrower authorization than a reusable service credential because they report evidence that can trigger important state changes.

The MVP targets one demonstration organization and does not need enterprise federation or dynamic policy management.

## Decision

1. `PublicCustomer` is the unauthenticated authorization subject for public intake only. Safe submission identifiers grant no query access.
2. Human authentication uses short-lived Supabase Auth access tokens. FastAPI validates signature via trusted JWKS, issuer, audience, times, subject, and access-token type.
3. FastAPI maps the verified Supabase subject to an active application actor and loads one fixed role—`OperationsAgent`, `ManagerApprover`, or `Administrator`—from Postgres for every protected request.
4. Roles in request JSON, frontend state, or token custom claims are not authoritative. Role assignment is controlled, not self-service, and no MVP user-management UI is required.
5. n8n authenticates as `WorkflowService` using HMAC-SHA256 over method, canonical path/query, timestamp, nonce, and SHA-256 body digest. Verification uses constant-time comparison, bounded clock skew, nonce replay prevention, environment-specific secrets, and fail-closed rotation/disablement.
6. Result callbacks additionally require a high-entropy opaque credential bound to one backend-created attempt, operation kind, WorkflowService identity, and expiry. Only its cryptographic hash is persisted.
7. `BackendService` is a trusted internal identity, not an external shared credential. `EventPublisher` has outbox-publication privileges only and no lifecycle API authority.
8. Authorization has three layers: authenticate the subject, check centralized endpoint/field permission, then enforce domain guards in the service layer.
9. Fixed permissions follow the complete command/query matrix in the [authentication and authorization model](../authentication-and-authorization.md). Deny is the default.
10. `OperationsAgent` cannot approve/reject, complete Urgent review, mark work terminal, or use machine callbacks. `ManagerApprover` adds those human authorities except role/machine management. `Administrator` adds security audit and controlled configuration but cannot bypass domain guards.
11. No human can approve or reject an active proposal they created or materially revised. Proposal submission freezes actor-UUID attribution used by the self-approval guard; role changes do not erase it.
12. Backend and workflow identities can never create human approval decisions. External providers are evidence sources, not API actors.
13. Customer and provider data is projected by field-level least privilege. Event envelopes and general logs remain PII-minimized, and secrets/callback credentials are never exposed through ordinary queries.
14. Authentication/permission failures use the existing stable error envelope, with `404` substituted where `403` would expose a protected record.
15. Security-relevant actions by trusted actors create canonical audit evidence. Failures before a trusted actor or aggregate can be established remain sanitized security telemetry only.

The detailed design is in [authentication and authorization](../authentication-and-authorization.md). API and event boundaries remain governed by [ADR 0002](0002-api-command-and-event-boundaries.md).

## Alternatives considered

### Trust role claims embedded in the access token

Rejected. Stale or client-influenced claims could continue authorizing a changed/disabled role. Loading the application-controlled role per protected request gives clear MVP revocation behavior.

### Enforce authorization only in the frontend

Rejected. Routes and UI controls can be bypassed and cannot enforce lifecycle, version, idempotency, attempt, or self-approval guards.

### Dynamic roles and editable permission policies

Rejected. Three fixed human roles and three machine identities cover the MVP with less configuration risk and a reviewable matrix.

### One shared machine API key

Rejected. A bearer-style shared key lacks request integrity, replay protection, environment separation, and clear rotation behavior. HMAC authenticates the exact request.

### Machine authentication alone for callbacks

Rejected. A compromised or misconfigured workflow credential could otherwise report results for arbitrary attempts. Attempt scope provides least privilege and limits replay.

### Allow administrator self-approval

Rejected. Administrative authority does not remove separation of duties. Emergency/break-glass approval is outside the MVP.

### Let AI or mock-email providers call FastAPI callbacks directly

Rejected. Providers are not canonical actors. n8n owns the constrained adapter invocation and reports evidence under WorkflowService plus attempt scope.

### Long-lived role cache

Rejected for the MVP because it obscures revocation timing. A future cache requires explicit invalidation and a short documented authorization-staleness bound.

## Consequences

### Positive

- Every endpoint has an explicit subject decision and deny-by-default behavior.
- Human role changes take effect on the next protected request.
- Operations staff cannot approve customer-facing content, and no approver can decide their own work.
- n8n has the minimum authority needed for orchestration without generic state access.
- Request signing plus nonce checks protects machine request integrity and replay.
- Attempt credentials limit callback authority to one operation.
- Field projections and event rules reduce PII and secret exposure.
- Security denials can be audited without treating untrusted caller claims as verified identities.

### Costs and tradeoffs

- Every protected human request requires an application actor/role lookup.
- Machine callers must canonicalize requests, synchronize clocks, persist nonces, and rotate secrets safely.
- Proposal versions require immutable contributor attribution for self-approval checks.
- Permission and field-projection tests must cover 20 command intents and 13 queries.
- Hiding resource existence can make some authorization failures less descriptive to clients.

### Follow-up design work

- Define Postgres actor/profile, role, nonce, machine identity, callback credential, attribution, and security-audit constraints.
- Define Supabase project/token settings, application setup, disabled-user behavior, and controlled demo-user seeding.
- Choose implementation libraries and secret storage for JWKS/HMAC/callback verification.
- Define permission and field-projection contract tests for every matrix row.
- Define administrator role/machine/configuration procedures without adding self-service role changes.

Later implementation may refine mechanics but cannot trust client-supplied roles, permit self-approval, let service identities fabricate approvals, weaken callback scope, or bypass FastAPI/domain enforcement without a superseding ADR.
