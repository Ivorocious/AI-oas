"""Database-backed selection of the immutable deployed failure policy."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_operations_automation.db.models.failure_recovery import (
    FailureRecoveryPolicyVersion as FailureRecoveryPolicyRow,
)
from ai_operations_automation.failure_recovery.policy import DEMO_FAILURE_RECOVERY_POLICY


def select_active_failure_policy(session: Session, database_now: datetime):
    """Select and validate the deployment-controlled policy effective at database time."""
    row = session.scalar(
        select(FailureRecoveryPolicyRow)
        .where(
            FailureRecoveryPolicyRow.status == "Active",
            FailureRecoveryPolicyRow.effective_at <= database_now,
        )
        .order_by(
            FailureRecoveryPolicyRow.effective_at.desc(),
            FailureRecoveryPolicyRow.revision.desc(),
        )
        .limit(1)
    )
    policy = DEMO_FAILURE_RECOVERY_POLICY
    expected_content = policy.content.model_dump(mode="json")
    if row is None:
        raise RuntimeError("no effective failure recovery policy exists")
    if (
        row.id != policy.id
        or row.policy_key != policy.policy_key
        or row.semantic_version != policy.semantic_version
        or row.revision != policy.revision
        or row.content_digest != policy.content_digest
        or row.effective_at != policy.effective_at
        or row.policy_snapshot != expected_content
        or row.operation_kind_rules != expected_content["operation_kind_rules"]
        or row.failure_code_catalog != expected_content["failure_code_catalog"]
        or row.attempt_budgets != expected_content["attempt_budgets"]
        or row.retry_delay_schedule != expected_content["retry_delay_schedule"]
        or row.stale_attempt_thresholds != expected_content["stale_attempt_thresholds"]
        or row.reconciliation_rules != expected_content["reconciliation_rules"]
        or row.recovery_disposition_rules != expected_content["recovery_disposition_rules"]
        or row.terminalization_rules != expected_content["terminalization_rules"]
    ):
        raise RuntimeError("the effective failure recovery policy does not match deployment code")
    return policy
