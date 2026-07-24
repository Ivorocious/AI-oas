import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Link,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";
import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  idempotencyKey,
  selectedDemoPersona,
  signIn,
  signOut,
  tokenPresent,
  type Persona,
} from "./api";

type RequestItem = {
  id: string;
  status: string;
  priority: string | null;
  current_queue: string | null;
  version: number;
};
type RequestDetail = { service_request: RequestItem };
type Proposal = {
  id: string;
  state: string;
  version: number;
  payload_digest: string;
  content: string;
  destination_value: string;
  proposal_number: number;
};
type Approval = {
  id: string;
  decision: string;
  decided_at: string;
  payload_digest: string;
};
type Envelope<T> = { result: T };
type Page = { next_cursor: string | null };
type Paged<T> = T & { page: Page };

const get =
  <T,>(path: string) =>
  () =>
    api<Envelope<T>>(path).then((value) => value.result);

const getPage =
  <T,>(path: string, cursor: string | null) =>
  () => {
    const url = new URL(path, window.location.origin);
    url.searchParams.set("limit", "1");
    if (cursor) url.searchParams.set("cursor", cursor);
    return api<Envelope<Paged<T>>>(`${url.pathname}${url.search}`).then(
      (value) => value.result,
    );
  };

function safeError(error: unknown, fallback: string) {
  if (!(error instanceof ApiError)) return fallback;
  if (error.status === 401) return "Your session expired. Sign in again.";
  if (error.status === 404) return "This protected item is unavailable.";
  if (error.status === 403)
    return "You are not permitted to perform this action.";
  if (error.status === 409)
    return "This item changed or was already resolved. Refresh to see the authoritative state.";
  if (error.status === 422 || error.code === "INVALID_REJECTION_RATIONALE")
    return "Enter a rejection rationale of at least 20 characters.";
  if (error.retryable)
    return "The service is temporarily unavailable. Try again.";
  return error.message || fallback;
}

function friendlyQueue(value: string | null) {
  const labels: Record<string, string> = {
    HumanReview: "Human review",
    PriorityRequests: "Priority requests",
    StandardRequests: "Standard requests",
    DuplicateReview: "Duplicate review",
    FailedRetryRequired: "Failed / retry required",
  };
  return value ? (labels[value] ?? value) : "Not assigned";
}

function StatePanel({
  eyebrow,
  title,
  children,
  busy = false,
}: {
  eyebrow: string;
  title: string;
  children?: React.ReactNode;
  busy?: boolean;
}) {
  return (
    <section className={`state-panel${busy ? " is-busy" : ""}`}>
      <p className="eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      {children}
    </section>
  );
}

function Shell({
  children,
  onSignOut,
}: {
  children: React.ReactNode;
  onSignOut: () => void;
}) {
  const navigate = useNavigate();
  const persona = selectedDemoPersona();
  const role = persona === "manager" ? "Manager approver" : "Operations agent";
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to dashboard content
      </a>
      <header className="topbar">
        <Link className="brand" to="/requests" aria-label="AI Operations home">
          <span className="brand-mark" aria-hidden="true">
            AO
          </span>
          <span>
            <strong>AI Operations</strong>
            <small>Service request control room</small>
          </span>
        </Link>
        <div
          className="session-context"
          aria-label="Current demonstration session"
        >
          <span className="live-dot" aria-hidden="true" />
          <span>
            <strong>{role}</strong>
            <small>Local synthetic session</small>
          </span>
        </div>
        <button
          className="button button-quiet sign-out"
          onClick={() => {
            signOut();
            onSignOut();
            navigate("/sign-in");
          }}
        >
          Sign out
        </button>
      </header>
      <div className="environment-strip">
        <span>Recruiter demonstration</span>
        <span aria-hidden="true">·</span>
        <span>Backend-authoritative state</span>
        <span aria-hidden="true">·</span>
        <span>Access token held in memory only</span>
      </div>
      <main id="main-content" className="page-shell" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}

function SignIn({
  onSignedIn,
  sessionMessage,
}: {
  onSignedIn: () => void;
  sessionMessage?: string;
}) {
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [signingIn, setSigningIn] = useState<Persona | null>(null);
  const choose = async (persona: Persona) => {
    setError("");
    setSigningIn(persona);
    try {
      await signIn(persona);
      onSignedIn();
      navigate("/requests");
    } catch (issue) {
      setError(issue instanceof Error ? issue.message : "Unable to sign in.");
      setSigningIn(null);
    }
  };
  return (
    <main className="sign-in-page">
      <section className="sign-in-intro" aria-labelledby="sign-in-heading">
        <div className="sign-in-brand">
          <span className="brand-mark" aria-hidden="true">
            AO
          </span>
          <span>AI Operations Automation Suite</span>
        </div>
        <div>
          <p className="eyebrow">Local portfolio demonstration</p>
          <h1 id="sign-in-heading">
            Review the decision, then trust the evidence.
          </h1>
          <p className="lede">
            Follow a service request from its protected approval queue through
            an exact, auditable human decision. The backend remains the
            authority at every step.
          </p>
        </div>
        <dl className="demo-principles">
          <div>
            <dt>01</dt>
            <dd>Advisory AI interpretation</dd>
          </div>
          <div>
            <dt>02</dt>
            <dd>Deterministic routing policy</dd>
          </div>
          <div>
            <dt>03</dt>
            <dd>Human approval and immutable audit</dd>
          </div>
        </dl>
      </section>
      <section className="sign-in-panel" aria-labelledby="persona-heading">
        <p className="eyebrow">Choose a safe synthetic identity</p>
        <h2 id="persona-heading">Enter the operator dashboard</h2>
        <p className="supporting-copy">
          Persona selection is presentation only. Roles and permissions are
          resolved and enforced by the backend.
        </p>
        {sessionMessage && (
          <div className="notice notice-warning" role="status">
            {sessionMessage}
          </div>
        )}
        <div className="persona-list">
          <button
            className="persona-option"
            aria-label="Sign in as ManagerApprover"
            disabled={signingIn !== null}
            onClick={() => choose("manager")}
          >
            <span className="persona-copy">
              <strong>ManagerApprover</strong>
              <small>
                Inspect evidence and decide the exact active proposal
              </small>
            </span>
            <span aria-hidden="true">→</span>
          </button>
          <button
            className="persona-option persona-secondary"
            aria-label="Sign in as OperationsAgent"
            disabled={signingIn !== null}
            onClick={() => choose("operations")}
          >
            <span className="persona-copy">
              <strong>OperationsAgent</strong>
              <small>
                Inspect permitted evidence in a read-only decision view
              </small>
            </span>
            <span aria-hidden="true">→</span>
          </button>
        </div>
        {signingIn && (
          <p className="inline-status" role="status">
            Opening the protected{" "}
            {signingIn === "manager" ? "manager" : "operations"} view…
          </p>
        )}
        {error && (
          <div className="notice notice-error" role="alert">
            <strong>Sign-in unavailable</strong>
            <span>{error}</span>
          </div>
        )}
        <p className="security-note">
          Local only · synthetic data · ephemeral signing key · no refresh token
        </p>
      </section>
    </main>
  );
}

function Queue({ onSignOut }: { onSignOut: () => void }) {
  const query = useInfiniteQuery({
    queryKey: ["queue", "AwaitingApproval"],
    queryFn: ({ pageParam }) =>
      getPage<{ items: RequestItem[] }>(
        "/api/v1/service-requests?status=AwaitingApproval",
        pageParam,
      )(),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.page.next_cursor ?? undefined,
  });

  if (query.isLoading)
    return (
      <Shell onSignOut={onSignOut}>
        <StatePanel eyebrow="Protected queue" title="Loading approvals…" busy>
          <p>Requesting the current backend-authoritative queue.</p>
          <div className="loading-bars" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        </StatePanel>
      </Shell>
    );

  if (query.isError)
    return (
      <Shell onSignOut={onSignOut}>
        <StatePanel
          eyebrow="Protected queue"
          title="The queue could not be loaded"
        >
          <div className="notice notice-error" role="alert">
            {safeError(query.error, "Unable to load the protected queue.")}
          </div>
          <button
            className="button button-primary"
            onClick={() => query.refetch()}
          >
            Retry queue
          </button>
        </StatePanel>
      </Shell>
    );

  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const hasNext = Boolean(query.hasNextPage);
  return (
    <Shell onSignOut={onSignOut}>
      <section className="queue-page" aria-labelledby="queue-heading">
        <div className="page-heading">
          <div>
            <p className="eyebrow">Authoritative queue</p>
            <h1 id="queue-heading">Awaiting approval</h1>
            <p className="lede compact">
              Customer-facing proposals waiting for an authorized human
              decision.
            </p>
          </div>
          <div
            className="metric"
            aria-label={`${items.length} approvals loaded`}
          >
            <strong>{items.length.toString().padStart(2, "0")}</strong>
            <span>Loaded approvals</span>
          </div>
        </div>

        <div className="queue-toolbar" aria-label="Queue context">
          <span className="filter-chip">Status · AwaitingApproval</span>
          <span>Page size 1 · signed cursor pagination</span>
          {query.isFetching && !query.isFetchingNextPage && (
            <span className="inline-status" role="status">
              Refreshing protected queue…
            </span>
          )}
        </div>

        {items.length === 0 ? (
          <div className="empty-state">
            <span className="empty-mark" aria-hidden="true">
              ✓
            </span>
            <div>
              <h2>No pending approvals</h2>
              <p>The backend returned an empty AwaitingApproval queue.</p>
            </div>
          </div>
        ) : (
          <div className="request-list" aria-label="Requests awaiting approval">
            <div className="request-list-heading" aria-hidden="true">
              <span>Request</span>
              <span>Priority</span>
              <span>Current queue</span>
              <span>Record</span>
            </div>
            {items.map((item) => (
              <Link
                className="request-row"
                to={`/requests/${item.id}`}
                key={item.id}
              >
                <span className="request-identity">
                  <span className="status-badge status-pending">
                    {item.status}
                  </span>
                  <strong>Service request {item.id.slice(0, 8)}</strong>
                  <small>{item.id}</small>
                </span>
                <span>
                  <small className="mobile-label">Priority</small>
                  <span
                    className={`priority priority-${item.priority?.toLowerCase() ?? "none"}`}
                  >
                    {item.priority ?? "—"}
                  </span>
                </span>
                <span>
                  <small className="mobile-label">Current queue</small>
                  <strong>{friendlyQueue(item.current_queue)}</strong>
                  <small className="wire-value">
                    {item.current_queue ?? "—"}
                  </small>
                </span>
                <span className="row-action">
                  <span>Request v{item.version}</span>
                  <strong aria-hidden="true">Review →</strong>
                </span>
              </Link>
            ))}
          </div>
        )}

        <div className="pagination-bar">
          <p>
            Showing {items.length} protected{" "}
            {items.length === 1 ? "record" : "records"}
          </p>
          <button
            className="button button-secondary"
            disabled={!hasNext || query.isFetchingNextPage}
            onClick={() => query.fetchNextPage()}
          >
            {query.isFetchingNextPage
              ? "Loading more approvals…"
              : hasNext
                ? "Load more approvals"
                : "All approvals loaded"}
          </button>
        </div>
      </section>
    </Shell>
  );
}

function Detail({ onSignOut }: { onSignOut: () => void }) {
  const { requestId } = useParams();
  const client = useQueryClient();
  const [notice, setNotice] = useState("");
  const [rationale, setRationale] = useState("");
  const [deciding, setDeciding] = useState<"approve" | "reject" | null>(null);
  const request = useQuery({
    queryKey: ["request", requestId],
    queryFn: get<RequestDetail>(`/api/v1/service-requests/${requestId}`),
  });
  const timeline = useQuery({
    queryKey: ["timeline", requestId],
    queryFn: get<{
      items: Array<{
        id: string;
        event_name: string;
        occurred_at: string;
        outcome: string;
      }>;
    }>(`/api/v1/service-requests/${requestId}/timeline`),
  });
  const interpretations = useQuery({
    queryKey: ["interpretations", requestId],
    queryFn: get<{ items: Array<{ summary: string; confidence: string }> }>(
      `/api/v1/service-requests/${requestId}/ai-interpretations`,
    ),
  });
  const routing = useQuery({
    queryKey: ["routing", requestId],
    queryFn: get<{
      items: Array<{
        final_category: string;
        final_queue: string;
        policy_semantic_version: string;
      }>;
    }>(`/api/v1/service-requests/${requestId}/routing-decisions`),
  });
  const proposals = useQuery({
    queryKey: ["proposals", requestId],
    queryFn: get<{ items: Proposal[] }>(
      `/api/v1/service-requests/${requestId}/proposed-actions`,
    ),
  });
  const proposal = proposals.data?.items.find(
    (item) => item.state === "PendingApproval",
  );
  const historyProposal = proposal ?? proposals.data?.items[0];
  const approvals = useQuery({
    enabled: Boolean(historyProposal),
    queryKey: ["approvals", historyProposal?.id],
    queryFn: historyProposal
      ? get<{ items: Approval[] }>(
          `/api/v1/proposed-actions/${historyProposal.id}/approvals`,
        )
      : async () => ({ items: [] }),
  });
  const showDecisionControls = selectedDemoPersona() === "manager";

  const decide = async (decision: "approve" | "reject") => {
    if (!proposal || !request.data) return;
    const serviceRequest = request.data.service_request;
    if (decision === "reject" && rationale.trim().length < 20) {
      setNotice("A rejection rationale of at least 20 characters is required.");
      return;
    }
    setNotice("");
    setDeciding(decision);
    try {
      await api(
        `/api/v1/proposed-actions/${proposal.id}/commands/${decision}`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey(),
          },
          body: JSON.stringify({
            schema_version: "1.0",
            expected_versions: {
              service_request: serviceRequest.version,
              proposed_action: proposal.version,
            },
            expected_payload_digest: proposal.payload_digest,
            ...(decision === "reject" ? { rationale: rationale.trim() } : {}),
          }),
        },
      );
      await client.invalidateQueries();
      setRationale("");
      setNotice(
        `${decision === "approve" ? "Approved" : "Rejected"}. Refetched authoritative state and audit evidence.`,
      );
    } catch (issue) {
      setNotice(
        safeError(issue, "The decision could not be completed safely."),
      );
      await client.invalidateQueries();
    } finally {
      setDeciding(null);
    }
  };

  if (request.isLoading || proposals.isLoading)
    return (
      <Shell onSignOut={onSignOut}>
        <StatePanel
          eyebrow="Protected request"
          title="Loading request evidence…"
          busy
        >
          <p>Resolving the current request and exact active proposal.</p>
          <div className="loading-bars" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        </StatePanel>
      </Shell>
    );

  if (request.isError || proposals.isError)
    return (
      <Shell onSignOut={onSignOut}>
        <StatePanel eyebrow="Protected request" title="Request unavailable">
          <div className="notice notice-error" role="alert">
            {safeError(
              request.error ?? proposals.error,
              "This protected request could not be loaded.",
            )}
          </div>
          <button
            className="button button-primary"
            onClick={() => {
              void request.refetch();
              void proposals.refetch();
            }}
          >
            Retry request
          </button>
        </StatePanel>
      </Shell>
    );

  const serviceRequest = request.data!.service_request;
  const rationaleLength = rationale.trim().length;
  return (
    <Shell onSignOut={onSignOut}>
      <article className="detail-page" aria-labelledby="request-heading">
        <nav className="breadcrumb" aria-label="Breadcrumb">
          <Link to="/requests">Approval queue</Link>
          <span aria-hidden="true">/</span>
          <span aria-current="page">Request {requestId?.slice(0, 8)}</span>
        </nav>

        <header className="detail-heading">
          <div>
            <p className="eyebrow">Protected service request</p>
            <h1 id="request-heading">Request {requestId?.slice(0, 8)}</h1>
            <p className="record-id">{requestId}</p>
          </div>
          <div
            className="summary-chips"
            role="group"
            aria-label="Request summary"
          >
            <span className="status-badge status-pending">
              {serviceRequest.status}
            </span>
            <span
              className={`priority priority-${serviceRequest.priority?.toLowerCase() ?? "none"}`}
            >
              {serviceRequest.priority ?? "No priority"}
            </span>
            <span>{friendlyQueue(serviceRequest.current_queue)}</span>
            <span>request v{serviceRequest.version}</span>
          </div>
        </header>

        {notice && (
          <div className="notice notice-info" role="status" aria-live="polite">
            <span className="notice-mark" aria-hidden="true">
              i
            </span>
            <span>{notice}</span>
          </div>
        )}

        <div className="detail-layout">
          <section
            className="decision-card"
            aria-labelledby="active-proposal-heading"
          >
            <div className="section-heading">
              <div>
                <p className="eyebrow">
                  {proposal ? "Decision required" : "Decision resolved"}
                </p>
                <h2 id="active-proposal-heading">
                  {proposal ? "Active proposal" : "Proposal state"}
                </h2>
              </div>
              {proposal && (
                <span className="status-badge status-pending">
                  Exact payload
                </span>
              )}
            </div>
            {proposal ? (
              <>
                <blockquote className="proposal-content">
                  {proposal.content}
                </blockquote>
                <dl className="proposal-meta">
                  <div>
                    <dt>Destination</dt>
                    <dd>{proposal.destination_value}</dd>
                  </div>
                  <div>
                    <dt>Proposal</dt>
                    <dd>
                      #{proposal.proposal_number} · version {proposal.version}
                    </dd>
                  </div>
                  <div>
                    <dt>Payload proof</dt>
                    <dd>
                      <code>{proposal.payload_digest.slice(0, 12)}…</code>
                    </dd>
                  </div>
                </dl>
                {showDecisionControls ? (
                  <div className="decision-controls">
                    <div className="approve-zone">
                      <div>
                        <strong>Approve this exact proposal</strong>
                        <p>
                          The backend verifies the version and payload digest
                          before accepting.
                        </p>
                      </div>
                      <button
                        className="button button-primary"
                        disabled={deciding !== null}
                        onClick={() => decide("approve")}
                      >
                        {deciding === "approve"
                          ? "Approving…"
                          : "Approve exact proposal"}
                      </button>
                    </div>
                    <div className="reject-zone">
                      <label htmlFor="rejection-rationale">
                        Rejection rationale
                      </label>
                      <textarea
                        id="rejection-rationale"
                        value={rationale}
                        onChange={(event) => setRationale(event.target.value)}
                        minLength={20}
                        maxLength={1000}
                        aria-invalid={
                          rationaleLength > 0 && rationaleLength < 20
                        }
                        aria-describedby="rationale-help"
                        placeholder="Explain why this proposal should be revised…"
                      />
                      <div className="field-help" id="rationale-help">
                        <span>
                          Required for rejection (20 characters minimum).
                        </span>
                        <span>{rationaleLength}/1000</span>
                      </div>
                      <button
                        className="button button-danger"
                        disabled={deciding !== null}
                        onClick={() => decide("reject")}
                      >
                        {deciding === "reject"
                          ? "Rejecting…"
                          : "Reject with rationale"}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="read-only-callout">
                    <span className="lock-mark" aria-hidden="true">
                      Read only
                    </span>
                    <div>
                      <strong>Manager decision required</strong>
                      <p>
                        Decision controls are not available to this presentation
                        persona. The backend remains authoritative.
                      </p>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="empty-state compact-empty">
                <span className="empty-mark" aria-hidden="true">
                  —
                </span>
                <div>
                  <h3>No active proposal</h3>
                  <p>
                    No pending proposal remains in the backend-authoritative
                    state.
                  </p>
                </div>
              </div>
            )}
          </section>

          <aside
            className="evidence-column"
            aria-label="Request evidence summary"
          >
            <section
              className="evidence-card"
              aria-labelledby="interpretation-heading"
            >
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Advisory evidence</p>
                  <h2 id="interpretation-heading">AI interpretation</h2>
                </div>
                <span className="evidence-label">Not authoritative</span>
              </div>
              {interpretations.isLoading ? (
                <p className="inline-status" role="status">
                  Loading interpretation evidence…
                </p>
              ) : interpretations.isError ? (
                <p className="inline-error" role="alert">
                  Interpretation evidence is unavailable.
                </p>
              ) : interpretations.data?.items.length ? (
                interpretations.data.items.map((item, index) => (
                  <div className="evidence-entry" key={index}>
                    <p>{item.summary}</p>
                    <span>
                      Confidence <strong>{item.confidence}</strong>
                    </span>
                  </div>
                ))
              ) : (
                <p className="muted">No interpretation evidence.</p>
              )}
            </section>

            <section
              className="evidence-card"
              aria-labelledby="routing-heading"
            >
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Authoritative policy</p>
                  <h2 id="routing-heading">Routing decision</h2>
                </div>
              </div>
              {routing.isLoading ? (
                <p className="inline-status" role="status">
                  Loading routing evidence…
                </p>
              ) : routing.isError ? (
                <p className="inline-error" role="alert">
                  Routing evidence is unavailable.
                </p>
              ) : routing.data?.items.length ? (
                routing.data.items.map((item, index) => (
                  <div className="routing-path" key={index}>
                    <p>
                      <strong>{item.final_category}</strong>
                      <span aria-hidden="true">→</span>
                      <strong>{item.final_queue}</strong>
                    </p>
                    <span>
                      {item.final_category} → {item.final_queue} · policy{" "}
                      {item.policy_semantic_version}
                    </span>
                  </div>
                ))
              ) : (
                <p className="muted">No routing evidence.</p>
              )}
            </section>

            <section
              className="evidence-card"
              aria-labelledby="proposal-history-heading"
            >
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Version trail</p>
                  <h2 id="proposal-history-heading">Proposal history</h2>
                </div>
                <span className="count-badge">
                  {proposals.data!.items.length}
                </span>
              </div>
              {proposals.data!.items.length ? (
                <ol className="history-list">
                  {proposals.data!.items.map((item) => (
                    <li key={item.id}>
                      <span>#{item.proposal_number}</span>
                      <strong>{item.state}</strong>
                      <small>v{item.version}</small>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="muted">No proposal history.</p>
              )}
            </section>

            <section
              className="evidence-card"
              aria-labelledby="approval-history-heading"
            >
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Immutable record</p>
                  <h2 id="approval-history-heading">Approval history</h2>
                </div>
              </div>
              {approvals.isLoading ? (
                <p className="inline-status" role="status">
                  Loading approval evidence…
                </p>
              ) : approvals.isError ? (
                <p className="inline-error" role="alert">
                  {safeError(
                    approvals.error,
                    "Approval evidence is unavailable.",
                  )}
                </p>
              ) : approvals.data?.items.length ? (
                <ol className="approval-list">
                  {approvals.data.items.map((item) => (
                    <li key={item.id}>
                      <strong>{item.decision}</strong>
                      <time dateTime={item.decided_at}>
                        {new Date(item.decided_at).toLocaleString()}
                      </time>
                      <code>{item.payload_digest.slice(0, 12)}…</code>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="muted">No immutable decision recorded.</p>
              )}
            </section>
          </aside>
        </div>

        <section
          className="timeline-card"
          aria-labelledby="audited-timeline-heading"
        >
          <div className="section-heading timeline-heading">
            <div>
              <p className="eyebrow">Backend evidence</p>
              <h2 id="audited-timeline-heading">Audited timeline</h2>
              <p>Newest first · append-oriented lifecycle evidence</p>
            </div>
            <span className="count-badge">
              {timeline.data?.items.length ?? 0} events
            </span>
          </div>
          {timeline.isLoading ? (
            <p className="inline-status" role="status">
              Loading audited timeline…
            </p>
          ) : timeline.isError ? (
            <div className="notice notice-error" role="alert">
              Audit evidence is unavailable.
            </div>
          ) : timeline.data?.items.length ? (
            <ol className="timeline" aria-label="Audited timeline">
              {timeline.data.items.map((item) => (
                <li
                  key={item.id}
                  aria-labelledby={`timeline-event-${item.id}`}
                  aria-describedby={`timeline-outcome-${item.id}`}
                >
                  <span className="timeline-dot" aria-hidden="true" />
                  <time dateTime={item.occurred_at}>
                    {new Date(item.occurred_at).toLocaleString()}
                  </time>
                  <code id={`timeline-event-${item.id}`}>
                    {item.event_name}
                  </code>
                  <span
                    className="outcome-badge"
                    id={`timeline-outcome-${item.id}`}
                    aria-label={`Outcome: ${item.outcome}`}
                  >
                    {item.outcome}
                  </span>
                </li>
              ))}
            </ol>
          ) : (
            <div className="empty-state compact-empty">
              <span className="empty-mark" aria-hidden="true">
                —
              </span>
              <div>
                <h3>No audit events</h3>
                <p>The backend returned an empty timeline.</p>
              </div>
            </div>
          )}
        </section>
      </article>
    </Shell>
  );
}

export function App() {
  const [authenticated, setAuthenticated] = useState(tokenPresent());
  const [sessionMessage, setSessionMessage] = useState("");
  useEffect(() => {
    const expire = () => {
      setSessionMessage(
        "Your session expired. Sign in again to continue safely.",
      );
      setAuthenticated(false);
    };
    window.addEventListener("demo-auth-expired", expire);
    return () => window.removeEventListener("demo-auth-expired", expire);
  }, []);
  const signedIn = () => {
    setSessionMessage("");
    setAuthenticated(true);
  };
  return (
    <Routes>
      <Route
        path="/sign-in"
        element={
          authenticated ? (
            <Navigate to="/requests" />
          ) : (
            <SignIn onSignedIn={signedIn} sessionMessage={sessionMessage} />
          )
        }
      />
      <Route
        path="/requests"
        element={
          authenticated ? (
            <Queue onSignOut={() => setAuthenticated(false)} />
          ) : (
            <Navigate to="/sign-in" />
          )
        }
      />
      <Route
        path="/requests/:requestId"
        element={
          authenticated ? (
            <Detail onSignOut={() => setAuthenticated(false)} />
          ) : (
            <Navigate to="/sign-in" />
          )
        }
      />
      <Route
        path="*"
        element={<Navigate to={authenticated ? "/requests" : "/sign-in"} />}
      />
    </Routes>
  );
}
