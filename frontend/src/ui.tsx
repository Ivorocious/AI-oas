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

function Shell({
  children,
  onSignOut,
}: {
  children: React.ReactNode;
  onSignOut: () => void;
}) {
  const navigate = useNavigate();
  return (
    <main>
      <header>
        <Link to="/requests">AI Operations</Link>
        <span>Local synthetic persona · browser-memory token</span>
        <button
          onClick={() => {
            signOut();
            onSignOut();
            navigate("/sign-in");
          }}
        >
          Sign out
        </button>
      </header>
      {children}
    </main>
  );
}

function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const choose = async (persona: Persona) => {
    try {
      await signIn(persona);
      onSignedIn();
      navigate("/requests");
    } catch (issue) {
      setError(issue instanceof Error ? issue.message : "Unable to sign in.");
    }
  };
  return (
    <section className="sign-in">
      <p className="eyebrow">Portfolio demo</p>
      <h1>Operator dashboard</h1>
      <p>Choose a synthetic persona. Roles remain resolved by the backend.</p>
      <button onClick={() => choose("manager")}>
        Sign in as ManagerApprover
      </button>
      <button className="secondary" onClick={() => choose("operations")}>
        Sign in as OperationsAgent
      </button>
      {error && <p role="alert">{error}</p>}
    </section>
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
        <p>Loading protected queue…</p>
      </Shell>
    );
  if (query.isError)
    return (
      <Shell onSignOut={onSignOut}>
        <p role="alert">
          {safeError(query.error, "Unable to load the protected queue.")}
        </p>
        <button onClick={() => query.refetch()}>Retry queue</button>
      </Shell>
    );
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const hasNext = Boolean(query.hasNextPage);
  return (
    <Shell onSignOut={onSignOut}>
      <section>
        <p className="eyebrow">Authoritative queue</p>
        <h1>Awaiting approval</h1>
        {items.length === 0 ? (
          <p>No pending approvals.</p>
        ) : (
          <div className="cards">
            {items.map((item) => (
              <Link className="card" to={`/requests/${item.id}`} key={item.id}>
                <strong>{item.status}</strong>
                <span>Priority: {item.priority ?? "—"}</span>
                <span>Queue: {item.current_queue ?? "—"}</span>
                <span>Request v{item.version}</span>
              </Link>
            ))}
          </div>
        )}
        <button
          disabled={!hasNext || query.isFetchingNextPage}
          onClick={() => query.fetchNextPage()}
        >
          {query.isFetchingNextPage
            ? "Loading more approvals…"
            : hasNext
              ? "Load more approvals"
              : "All approvals loaded"}
        </button>
        {query.isFetching && !query.isFetchingNextPage && (
          <p role="status">Refreshing protected queue…</p>
        )}
      </section>
    </Shell>
  );
}

function Detail({ onSignOut }: { onSignOut: () => void }) {
  const { requestId } = useParams();
  const client = useQueryClient();
  const [notice, setNotice] = useState("");
  const [rationale, setRationale] = useState("");
  const [deciding, setDeciding] = useState(false);
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
    setDeciding(true);
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
      await client.invalidateQueries({ queryKey: ["request", requestId] });
    } finally {
      setDeciding(false);
    }
  };
  if (request.isLoading || proposals.isLoading)
    return (
      <Shell onSignOut={onSignOut}>
        <p>Loading request…</p>
      </Shell>
    );
  if (request.isError || proposals.isError)
    return (
      <Shell onSignOut={onSignOut}>
        <p role="alert">
          {safeError(
            request.error ?? proposals.error,
            "This protected request could not be loaded.",
          )}
        </p>
        <button
          onClick={() => {
            void request.refetch();
            void proposals.refetch();
          }}
        >
          Retry request
        </button>
      </Shell>
    );
  return (
    <Shell onSignOut={onSignOut}>
      <section>
        <Link to="/requests">← Queue</Link>
        <h1>Request {requestId?.slice(0, 8)}</h1>
        <div role="group" aria-label="Request summary">
          <p>
            <b>{request.data!.service_request.status}</b> ·{" "}
            {request.data!.service_request.current_queue} · request v
            {request.data!.service_request.version}
          </p>
        </div>
        {notice && <p role="status">{notice}</p>}
        <div className="grid">
          <article>
            <h2>Active proposal</h2>
            {proposal ? (
              <>
                <p>{proposal.content}</p>
                <p>To: {proposal.destination_value}</p>
                <p>
                  Proposal v{proposal.version} · digest{" "}
                  {proposal.payload_digest.slice(0, 12)}…
                </p>
                {showDecisionControls ? (
                  <>
                    <button
                      disabled={deciding}
                      onClick={() => decide("approve")}
                    >
                      Approve exact proposal
                    </button>
                    <label>
                      Rejection rationale
                      <textarea
                        value={rationale}
                        onChange={(event) => setRationale(event.target.value)}
                        minLength={20}
                        aria-describedby="rationale-help"
                      />
                    </label>
                    <small id="rationale-help">
                      Required for rejection (20 characters minimum).
                    </small>
                    <button
                      disabled={deciding}
                      className="secondary"
                      onClick={() => decide("reject")}
                    >
                      Reject with rationale
                    </button>
                  </>
                ) : (
                  <p>
                    Decision controls are not available to this presentation
                    persona. The backend remains authoritative.
                  </p>
                )}
              </>
            ) : (
              <p>No active proposal or your role is not permitted to decide.</p>
            )}
          </article>
          <article>
            <h2>Proposal history</h2>
            {proposals.data!.items.map((item) => (
              <p key={item.id}>
                #{item.proposal_number}: {item.state} (v{item.version})
              </p>
            ))}
          </article>
          <article>
            <h2>Immutable approval history</h2>
            {approvals.isLoading ? (
              <p>Loading approval evidence…</p>
            ) : approvals.isError ? (
              <p role="alert">
                {safeError(
                  approvals.error,
                  "Approval evidence is unavailable.",
                )}
              </p>
            ) : approvals.data?.items.length ? (
              approvals.data.items.map((item) => (
                <p key={item.id}>
                  {item.decision} · {new Date(item.decided_at).toLocaleString()}{" "}
                  · {item.payload_digest.slice(0, 12)}…
                </p>
              ))
            ) : (
              <p>No immutable decision recorded.</p>
            )}
          </article>
          <article>
            <h2>Interpretation</h2>
            {interpretations.isLoading ? (
              <p>Loading evidence…</p>
            ) : interpretations.isError ? (
              <p role="alert">Evidence is unavailable.</p>
            ) : (
              interpretations.data?.items.map((item, index) => (
                <p key={index}>
                  {item.summary} · confidence {item.confidence}
                </p>
              )) || <p>No interpretation evidence.</p>
            )}
          </article>
          <article>
            <h2>Routing evidence</h2>
            {routing.isLoading ? (
              <p>Loading routing evidence…</p>
            ) : routing.isError ? (
              <p role="alert">Routing evidence is unavailable.</p>
            ) : (
              routing.data?.items.map((item, index) => (
                <p key={index}>
                  {item.final_category} → {item.final_queue} · policy{" "}
                  {item.policy_semantic_version}
                </p>
              )) || <p>No routing evidence.</p>
            )}
          </article>
        </div>
        <article aria-labelledby="audited-timeline-heading">
          <h2 id="audited-timeline-heading">Audited timeline</h2>
          {timeline.isLoading ? (
            <p>Loading timeline…</p>
          ) : timeline.isError ? (
            <p role="alert">Audit evidence is unavailable.</p>
          ) : timeline.data?.items ? (
            <div role="list" aria-label="Audited timeline">
              {timeline.data.items.map((item) => (
                <div
                  key={item.id}
                  role="listitem"
                  aria-labelledby={`timeline-event-${item.id}`}
                  aria-describedby={`timeline-outcome-${item.id}`}
                >
                  <p>
                    {new Date(item.occurred_at).toLocaleString()} ·{" "}
                    <span id={`timeline-event-${item.id}`}>
                      {item.event_name}
                    </span>{" "}
                    ·{" "}
                    <span
                      id={`timeline-outcome-${item.id}`}
                      aria-label={`Outcome: ${item.outcome}`}
                    >
                      {item.outcome}
                    </span>
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <p>Loading timeline…</p>
          )}
        </article>
      </section>
    </Shell>
  );
}

export function App() {
  const [authenticated, setAuthenticated] = useState(tokenPresent());
  useEffect(() => {
    const expire = () => setAuthenticated(false);
    window.addEventListener("demo-auth-expired", expire);
    return () => window.removeEventListener("demo-auth-expired", expire);
  }, []);
  return (
    <Routes>
      <Route
        path="/sign-in"
        element={
          authenticated ? (
            <Navigate to="/requests" />
          ) : (
            <SignIn onSignedIn={() => setAuthenticated(true)} />
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
