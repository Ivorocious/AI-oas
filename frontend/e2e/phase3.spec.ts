import { expect, test } from "@playwright/test";

test("OperationsAgent is read-only while ManagerApprover decides the exact active proposal", async ({
  page,
  request,
}) => {
  await page.goto("/sign-in");
  const operationsTokenResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/demo-auth/token") &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Sign in as OperationsAgent" })
    .click();
  const operationsToken = (await operationsTokenResponse)
    .json()
    .then((body) => body.access_token as string);
  await expect(
    page.getByRole("heading", { name: "Awaiting approval" }),
  ).toBeVisible();
  await expect(
    page.evaluate(() => ({
      local: localStorage.length,
      session: sessionStorage.length,
    })),
  ).resolves.toEqual({ local: 0, session: 0 });
  const awaitingApprovalRequest = page
    .getByRole("link")
    .filter({ has: page.getByText("AwaitingApproval", { exact: true }) });
  await awaitingApprovalRequest.click();
  await expect(
    page.getByText("Decision controls are not available"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve exact proposal" }),
  ).toHaveCount(0);

  const token = await operationsToken;
  const requestId = page.url().split("/").pop()!;
  const requestDetail = await request.get(
    `http://127.0.0.1:8000/api/v1/service-requests/${requestId}`,
    {
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  const actions = await request.get(
    `http://127.0.0.1:8000/api/v1/service-requests/${requestId}/proposed-actions`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  const action = (await actions.json()).result.items.find(
    (item: { state: string }) => item.state === "PendingApproval",
  );
  const denied = await request.post(
    `http://127.0.0.1:8000/api/v1/proposed-actions/${action.id}/commands/approve`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Idempotency-Key": "phase3-operations-denied",
      },
      data: {
        schema_version: "1.0",
        expected_versions: {
          service_request: (await requestDetail.json()).result.service_request
            .version,
          proposed_action: action.version,
        },
        expected_payload_digest: action.payload_digest,
      },
    },
  );
  expect(denied.status()).toBe(403);

  await page.getByRole("button", { name: "Sign out" }).click();
  await page
    .getByRole("button", { name: "Sign in as ManagerApprover" })
    .click();
  await page
    .getByRole("link")
    .filter({ has: page.getByText("AwaitingApproval", { exact: true }) })
    .click();
  await expect(
    page.getByText("Synthetic bounded repair interpretation."),
  ).toBeVisible();
  await expect(
    page.getByText("Repair → PriorityRequests · policy 1.0.0", {
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Approve exact proposal" }).click();
  await expect(
    page.getByText("Refetched authoritative state and audit evidence."),
  ).toBeVisible();
  const requestSummary = page.getByRole("group", {
    name: "Request summary",
  });
  await expect(
    requestSummary.getByText("ActionPendingExecution", { exact: true }),
  ).toBeVisible();
  const auditedTimeline = page.getByRole("list", { name: "Audited timeline" });
  const approvalEvent = auditedTimeline.getByRole("listitem").filter({
    has: page.getByText("service_request.action_approved", { exact: true }),
  });
  await expect(
    approvalEvent.getByLabel("Outcome: ActionPendingExecution", {
      exact: true,
    }),
  ).toBeVisible();
  const immutableApprovalEvent = auditedTimeline.getByRole("listitem").filter({
    has: page.getByText("approval.approved", { exact: true }),
  });
  await expect(
    immutableApprovalEvent.getByLabel("Outcome: Approved", { exact: true }),
  ).toBeVisible();
});
