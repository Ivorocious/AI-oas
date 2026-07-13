"""Database-backed selection of the immutable deployed decision policy."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_operations_automation.db.models.decision import (
    DecisionPolicyVersion as DecisionPolicyRow,
)
from ai_operations_automation.deterministic_decision.policy import DEMO_DECISION_POLICY


def select_active_decision_policy(session: Session, database_now: datetime):
    """Select and validate the policy effective at PostgreSQL database time."""
    rows = session.scalars(
        select(DecisionPolicyRow)
        .where(
            DecisionPolicyRow.status == "Active",
            DecisionPolicyRow.effective_at <= database_now,
        )
        .order_by(
            DecisionPolicyRow.effective_at.desc(),
            DecisionPolicyRow.revision.desc(),
        )
        .limit(2)
        .with_for_update(key_share=True)
    ).all()
    if len(rows) != 1:
        raise RuntimeError("exactly one effective decision policy must exist")
    row = rows[0]
    policy = DEMO_DECISION_POLICY
    if (
        row.id != policy.id
        or row.policy_key != policy.policy_key
        or row.semantic_version != policy.semantic_version
        or row.revision != policy.revision
        or row.content_digest != policy.content_digest
        or row.effective_at != policy.effective_at
        or row.status != policy.status.value
        or row.policy_snapshot != policy.content.model_dump(mode="json")
    ):
        raise RuntimeError("the effective decision policy does not match deployment code")
    return policy
