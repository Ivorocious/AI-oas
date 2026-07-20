import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Link,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";
import { useState } from "react";
import {
  api,
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
const get =
  <T,>(path: string) =>
  () =>
    api<Envelope<T>>(path).then((value) => value.result);

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
  const query = useQuery({
    queryKey: ["queue"],
    queryFn: get<{ items: RequestItem[] }>(
      "/api/v1/service-requests?status=AwaitingApproval",
    ),
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
        <p role="alert">Unable to load the protected queue.</p>
      </Shell>
    );
  return (
    <Shell onSignOut={onSignOut}>
      <section>
        <p className="eyebrow">Authoritative queue</p>
        <h1>Awaiting approval</h1>
        {query.data!.items.length === 0 ? (
          <p>No pending approvals.</p>
        ) : (
          <div className="cards">
            {query.data!.items.map((item) => (
              <Link className="card" to={`/requests/${item.id}`} key={item.id}>
                <strong>{item.status}</strong>
                <span>Priority: {item.priority ?? "—"}</span>
                <span>Queue: {item.current_queue ?? "—"}</span>
                <span>Request v{item.version}</span>
              </Link>
            ))}
          </div>
        )}
      </section>
    </Shell>
  );
}

function Detail({ onSignOut }: { onSignOut: () => void }) {
  const { requestId } = useParams();
  const client = useQueryClient();
  const [notice, setNotice] = useState("");
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
    const rationale =
      decision === "reject"
        ? window.prompt("Required rejection rationale (20+ characters)")
        : undefined;
    if (decision === "reject" && (!rationale || rationale.length < 20)) {
      setNotice("A rejection rationale of at least 20 characters is required.");
      return;
    }
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
            ...(rationale ? { rationale } : {}),
          }),
        },
      );
      await client.invalidateQueries();
      setNotice(
        `${decision === "approve" ? "Approved" : "Rejected"}. Refetched authoritative state and audit evidence.`,
      );
    } catch (issue) {
      const error = issue as Error & { code?: string };
      setNotice(`${error.code ?? "ERROR"}: ${error.message}`);
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
        <p role="alert">This protected request could not be loaded.</p>
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
                    <button onClick={() => decide("approve")}>
                      Approve exact proposal
                    </button>
                    <button
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
            {approvals.data?.items.length ? (
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
            {interpretations.data?.items.map((item, index) => (
              <p key={index}>
                {item.summary} · confidence {item.confidence}
              </p>
            )) || <p>Loading evidence…</p>}
          </article>
          <article>
            <h2>Routing evidence</h2>
            {routing.data?.items.map((item, index) => (
              <p key={index}>
                {item.final_category} → {item.final_queue} · policy{" "}
                {item.policy_semantic_version}
              </p>
            )) || <p>Loading evidence…</p>}
          </article>
        </div>
        <article aria-labelledby="audited-timeline-heading">
          <h2 id="audited-timeline-heading">Audited timeline</h2>
          {timeline.data?.items ? (
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
