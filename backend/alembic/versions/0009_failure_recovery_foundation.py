"""Add the immutable failure-recovery policy and AI assessment fields.

Revision ID: 0009_failure_recovery_foundation
Revises: 0008_callback_command_authorization_binding
"""

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "0009_failure_recovery_foundation"
down_revision: str | None = "0008_callback_command_authorization_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FAILURE_STAGES = (
    "BeforeDispatch",
    "Dispatch",
    "ProviderProcessing",
    "ResponseValidation",
    "CallbackDelivery",
    "Reconciliation",
    "InternalCommit",
)
PROVIDER_INVOCATIONS = ("NotInvoked", "Invoked", "InvocationUnknown", "NotApplicable")
CUSTOMER_SIDE_EFFECTS = ("NotApplicable", "KnownNotApplied", "Applied", "Unknown")
RECOVERY_DISPOSITIONS = (
    "RetrySameOperation",
    "ReviseProposal",
    "ReconcileBeforeRetry",
    "ReplaySameCommand",
    "Terminal",
    "NoDomainChange",
)
RECONCILIATION_STATUSES = ("NotRequired", "Required")
RECOVERY_TARGETS = ("TriagePending", "ActionPendingExecution")

DEMO_POLICY_ID = uuid.UUID("6e862dfd-25c4-5f9b-873d-011f86c4bab5")
DEMO_POLICY_KEY = "phase2-demonstration-failure-recovery"
DEMO_POLICY_SEMANTIC_VERSION = "1.0.0"
DEMO_POLICY_REVISION = 1
DEMO_POLICY_EFFECTIVE_AT = datetime(2026, 7, 13, tzinfo=UTC)
DEMO_POLICY_CONTENT_DIGEST = "7eca0e59bbb41878817c52db02350b2e271b254e65e399e77bea4073ade4d1f0"
DEMO_POLICY_CANONICAL_JSON = (
    '{"attempt_budgets":[{"maximum_attempts":3,"operation_kind":"AIInterpretation"},{"max'
    'imum_attempts":3,"operation_kind":"OutboundAction"}],"failure_code_catalog":[{"code"'
    ':"WORKFLOW_FAILED_BEFORE_PROVIDER_INVOCATION","consumes_attempt":true,"consumes_budg'
    'et":true,"outcomes":[{"customer_side_effects":["NotApplicable"],"disposition":"Retry'
    'SameOperation","operation_kind":"AIInterpretation","provider_invocations":["NotInvok'
    'ed"],"stages":["BeforeDispatch"]},{"customer_side_effects":["KnownNotApplied"],"disp'
    'osition":"RetrySameOperation","operation_kind":"OutboundAction","provider_invocation'
    's":["NotInvoked"],"stages":["BeforeDispatch"]}],"required_sanitized_evidence":["work'
    'flow_error_class","adapter_version"]},{"code":"PROVIDER_CONNECTION_FAILED","consumes'
    '_attempt":true,"consumes_budget":true,"outcomes":[{"customer_side_effects":["NotAppl'
    'icable"],"disposition":"RetrySameOperation","operation_kind":"AIInterpretation","pro'
    'vider_invocations":["InvocationUnknown"],"stages":["Dispatch"]},{"customer_side_effe'
    'cts":["Unknown"],"disposition":"ReconcileBeforeRetry","operation_kind":"OutboundActi'
    'on","provider_invocations":["InvocationUnknown"],"stages":["Dispatch"]}],"required_s'
    'anitized_evidence":["correlation_hash","timeout_class"]},{"code":"PROVIDER_TIMEOUT",'
    '"consumes_attempt":true,"consumes_budget":true,"outcomes":[{"customer_side_effects":'
    '["NotApplicable"],"disposition":"RetrySameOperation","operation_kind":"AIInterpretat'
    'ion","provider_invocations":["Invoked"],"stages":["ProviderProcessing"]},{"customer_'
    'side_effects":["Unknown"],"disposition":"ReconcileBeforeRetry","operation_kind":"Out'
    'boundAction","provider_invocations":["Invoked"],"stages":["ProviderProcessing"]}],"r'
    'equired_sanitized_evidence":["duration_ms","correlation_hash","safe_hint"]},{"code":'
    '"PROVIDER_RATE_LIMITED","consumes_attempt":true,"consumes_budget":true,"outcomes":[{'
    '"customer_side_effects":["NotApplicable"],"disposition":"RetrySameOperation","operat'
    'ion_kind":"AIInterpretation","provider_invocations":["Invoked"],"stages":["ProviderP'
    'rocessing"]},{"customer_side_effects":["KnownNotApplied"],"disposition":"RetrySameOp'
    'eration","operation_kind":"OutboundAction","provider_invocations":["Invoked"],"stage'
    's":["ProviderProcessing"]},{"customer_side_effects":["Unknown"],"disposition":"Recon'
    'cileBeforeRetry","operation_kind":"OutboundAction","provider_invocations":["Invoked"'
    '],"stages":["ProviderProcessing"]}],"required_sanitized_evidence":["rate_limit_class'
    '","retry_after_at"]},{"code":"PROVIDER_TEMPORARILY_UNAVAILABLE","consumes_attempt":t'
    'rue,"consumes_budget":true,"outcomes":[{"customer_side_effects":["NotApplicable"],"d'
    'isposition":"RetrySameOperation","operation_kind":"AIInterpretation","provider_invoc'
    'ations":["Invoked"],"stages":["ProviderProcessing"]},{"customer_side_effects":["Know'
    'nNotApplied"],"disposition":"RetrySameOperation","operation_kind":"OutboundAction","'
    'provider_invocations":["Invoked"],"stages":["ProviderProcessing"]},{"customer_side_e'
    'ffects":["Unknown"],"disposition":"ReconcileBeforeRetry","operation_kind":"OutboundA'
    'ction","provider_invocations":["Invoked"],"stages":["ProviderProcessing"]}],"require'
    'd_sanitized_evidence":["provider_status_class","correlation_hash"]},{"code":"PROVIDE'
    'R_AUTHENTICATION_FAILED","consumes_attempt":true,"consumes_budget":true,"outcomes":['
    '{"customer_side_effects":["NotApplicable"],"disposition":"Terminal","operation_kind"'
    ':"AIInterpretation","provider_invocations":["NotInvoked"],"stages":["Dispatch"]},{"c'
    'ustomer_side_effects":["KnownNotApplied"],"disposition":"Terminal","operation_kind":'
    '"OutboundAction","provider_invocations":["NotInvoked"],"stages":["Dispatch"]}],"requ'
    'ired_sanitized_evidence":["credential_version_reference","adapter_version"]},{"code"'
    ':"PROVIDER_AUTHORIZATION_FAILED","consumes_attempt":true,"consumes_budget":true,"out'
    'comes":[{"customer_side_effects":["NotApplicable"],"disposition":"Terminal","operati'
    'on_kind":"AIInterpretation","provider_invocations":["Invoked"],"stages":["ProviderPr'
    'ocessing"]},{"customer_side_effects":["KnownNotApplied"],"disposition":"Terminal","o'
    'peration_kind":"OutboundAction","provider_invocations":["Invoked"],"stages":["Provid'
    'erProcessing"]}],"required_sanitized_evidence":["provider_decision_class","correlati'
    'on_hash"]},{"code":"PROVIDER_CONFIGURATION_INVALID","consumes_attempt":true,"consume'
    's_budget":true,"outcomes":[{"customer_side_effects":["NotApplicable"],"disposition":'
    '"Terminal","operation_kind":"AIInterpretation","provider_invocations":["NotInvoked"]'
    ',"stages":["BeforeDispatch"]},{"customer_side_effects":["KnownNotApplied"],"disposit'
    'ion":"Terminal","operation_kind":"OutboundAction","provider_invocations":["NotInvoke'
    'd"],"stages":["BeforeDispatch"]}],"required_sanitized_evidence":["configuration_vers'
    'ion","configuration_digest"]},{"code":"PROVIDER_REQUEST_REJECTED","consumes_attempt"'
    ':true,"consumes_budget":true,"outcomes":[{"customer_side_effects":["NotApplicable"],'
    '"disposition":"Terminal","operation_kind":"AIInterpretation","provider_invocations":'
    '["Invoked"],"stages":["ProviderProcessing"]},{"customer_side_effects":["KnownNotAppl'
    'ied"],"disposition":"Terminal","operation_kind":"OutboundAction","provider_invocatio'
    'ns":["Invoked"],"stages":["ProviderProcessing"]}],"required_sanitized_evidence":["re'
    'jection_class","field_reason_codes"]},{"code":"PROVIDER_RESPONSE_SCHEMA_INVALID","co'
    'nsumes_attempt":true,"consumes_budget":true,"outcomes":[{"customer_side_effects":["N'
    'otApplicable"],"disposition":"RetrySameOperation","operation_kind":"AIInterpretation'
    '","provider_invocations":["Invoked"],"stages":["ResponseValidation"]},{"customer_sid'
    'e_effects":["KnownNotApplied"],"disposition":"RetrySameOperation","operation_kind":"'
    'OutboundAction","provider_invocations":["Invoked"],"stages":["ResponseValidation"]},'
    '{"customer_side_effects":["Unknown"],"disposition":"ReconcileBeforeRetry","operation'
    '_kind":"OutboundAction","provider_invocations":["Invoked"],"stages":["ResponseValida'
    'tion"]}],"required_sanitized_evidence":["schema_version","validation_reason_codes","'
    'response_hash"]},{"code":"OUTBOUND_DESTINATION_REJECTED","consumes_attempt":true,"co'
    'nsumes_budget":true,"outcomes":[{"customer_side_effects":["KnownNotApplied"],"dispos'
    'ition":"ReviseProposal","operation_kind":"OutboundAction","provider_invocations":["I'
    'nvoked"],"stages":["ProviderProcessing"]}],"required_sanitized_evidence":["destinati'
    'on_reference_hash","rejection_code"]},{"code":"OUTBOUND_PAYLOAD_REJECTED","consumes_'
    'attempt":true,"consumes_budget":true,"outcomes":[{"customer_side_effects":["KnownNot'
    'Applied"],"disposition":"ReviseProposal","operation_kind":"OutboundAction","provider'
    '_invocations":["Invoked"],"stages":["ProviderProcessing"]}],"required_sanitized_evid'
    'ence":["proposal_digest","field_reason_codes"]},{"code":"CALLBACK_RESPONSE_LOST_AFTE'
    'R_COMMIT","consumes_attempt":false,"consumes_budget":false,"outcomes":[{"customer_si'
    'de_effects":["NotApplicable"],"disposition":"ReplaySameCommand","operation_kind":"AI'
    'Interpretation","provider_invocations":["NotApplicable"],"stages":["CallbackDelivery'
    '"]},{"customer_side_effects":["NotApplicable"],"disposition":"ReplaySameCommand","op'
    'eration_kind":"OutboundAction","provider_invocations":["NotApplicable"],"stages":["C'
    'allbackDelivery"]}],"required_sanitized_evidence":["callback_command_id","command_ke'
    'y_digest","response_loss_class"]},{"code":"CALLBACK_AUTHENTICATION_FAILED","consumes'
    '_attempt":false,"consumes_budget":false,"outcomes":[{"customer_side_effects":["NotAp'
    'plicable"],"disposition":"NoDomainChange","operation_kind":"AIInterpretation","provi'
    'der_invocations":["NotApplicable"],"stages":["CallbackDelivery"]},{"customer_side_ef'
    'fects":["NotApplicable"],"disposition":"NoDomainChange","operation_kind":"OutboundAc'
    'tion","provider_invocations":["NotApplicable"],"stages":["CallbackDelivery"]}],"requ'
    'ired_sanitized_evidence":["credential_version_reference","denial_code"]},{"code":"CA'
    'LLBACK_CREDENTIAL_INVALID","consumes_attempt":false,"consumes_budget":false,"outcome'
    's":[{"customer_side_effects":["NotApplicable"],"disposition":"NoDomainChange","opera'
    'tion_kind":"AIInterpretation","provider_invocations":["NotApplicable"],"stages":["Ca'
    'llbackDelivery"]},{"customer_side_effects":["NotApplicable"],"disposition":"NoDomain'
    'Change","operation_kind":"OutboundAction","provider_invocations":["NotApplicable"],"'
    'stages":["CallbackDelivery"]}],"required_sanitized_evidence":["attempt_id","credenti'
    'al_version","expiry_class"]},{"code":"ATTEMPT_PENDING_STALE","consumes_attempt":true'
    ',"consumes_budget":true,"outcomes":[{"customer_side_effects":["NotApplicable"],"disp'
    'osition":"RetrySameOperation","operation_kind":"AIInterpretation","provider_invocati'
    'ons":["NotInvoked"],"stages":["BeforeDispatch"]},{"customer_side_effects":["KnownNot'
    'Applied"],"disposition":"RetrySameOperation","operation_kind":"OutboundAction","prov'
    'ider_invocations":["NotInvoked"],"stages":["BeforeDispatch"]}],"required_sanitized_e'
    'vidence":["created_at","assessed_at","unclaimed_proof"]},{"code":"AI_ATTEMPT_RUNNING'
    '_STALE","consumes_attempt":true,"consumes_budget":true,"outcomes":[{"customer_side_e'
    'ffects":["NotApplicable"],"disposition":"RetrySameOperation","operation_kind":"AIInt'
    'erpretation","provider_invocations":["InvocationUnknown"],"stages":["ProviderProcess'
    'ing"]}],"required_sanitized_evidence":["started_at","assessed_at","callback_absence"'
    ']},{"code":"OUTBOUND_OUTCOME_UNCERTAIN","consumes_attempt":true,"consumes_budget":tr'
    'ue,"outcomes":[{"customer_side_effects":["Unknown"],"disposition":"ReconcileBeforeRe'
    'try","operation_kind":"OutboundAction","provider_invocations":["InvocationUnknown","'
    'Invoked"],"stages":["ProviderProcessing"]}],"required_sanitized_evidence":["started_'
    'at","correlation_hash","uncertainty_reason"]},{"code":"RECONCILIATION_CONFIRMED_SUCC'
    'ESS","consumes_attempt":false,"consumes_budget":false,"outcomes":[{"customer_side_ef'
    'fects":["Applied"],"disposition":"NoDomainChange","operation_kind":"OutboundAction",'
    '"provider_invocations":["Invoked"],"stages":["Reconciliation"]}],"required_sanitized'
    '_evidence":["provider_correlation","evidence_hash","confirmed_at"]},{"code":"RECONCI'
    'LIATION_CONFIRMED_NOT_APPLIED","consumes_attempt":false,"consumes_budget":false,"out'
    'comes":[{"customer_side_effects":["KnownNotApplied"],"disposition":"RetrySameOperati'
    'on","operation_kind":"OutboundAction","provider_invocations":["Invoked","InvocationU'
    'nknown"],"stages":["Reconciliation"]}],"required_sanitized_evidence":["provider_corr'
    'elation","evidence_hash","confirmed_at"]},{"code":"RECONCILIATION_PERMANENT_REJECTIO'
    'N","consumes_attempt":false,"consumes_budget":false,"outcomes":[{"customer_side_effe'
    'cts":["KnownNotApplied"],"disposition":"Terminal","operation_kind":"OutboundAction",'
    '"provider_invocations":["Invoked"],"stages":["Reconciliation"]}],"required_sanitized'
    '_evidence":["rejection_class","evidence_hash"]},{"code":"OUTBOUND_OUTCOME_UNRESOLVED'
    '","consumes_attempt":false,"consumes_budget":false,"outcomes":[{"customer_side_effec'
    'ts":["Unknown"],"disposition":"Terminal","operation_kind":"OutboundAction","provider'
    '_invocations":["Invoked","InvocationUnknown"],"stages":["Reconciliation"]}],"require'
    'd_sanitized_evidence":["deadline","assessed_at","correlation_hash","evidence_hash"]}'
    ',{"code":"RETRY_BUDGET_EXHAUSTED","consumes_attempt":false,"consumes_budget":false,"'
    'outcomes":[{"customer_side_effects":["NotApplicable","KnownNotApplied","Applied","Un'
    'known"],"disposition":"Terminal","operation_kind":"AIInterpretation","provider_invoc'
    'ations":["NotInvoked","Invoked","InvocationUnknown","NotApplicable"],"stages":["Befo'
    'reDispatch","Dispatch","ProviderProcessing","ResponseValidation","CallbackDelivery",'
    '"Reconciliation","InternalCommit"]},{"customer_side_effects":["NotApplicable","Known'
    'NotApplied","Applied","Unknown"],"disposition":"Terminal","operation_kind":"Outbound'
    'Action","provider_invocations":["NotInvoked","Invoked","InvocationUnknown","NotAppli'
    'cable"],"stages":["BeforeDispatch","Dispatch","ProviderProcessing","ResponseValidati'
    'on","CallbackDelivery","Reconciliation","InternalCommit"]}],"required_sanitized_evid'
    'ence":["attempt_number","maximum_attempts","policy_identity"]},{"code":"MANAGER_TERM'
    'INAL_DISPOSITION","consumes_attempt":false,"consumes_budget":false,"outcomes":[{"cus'
    'tomer_side_effects":["NotApplicable","KnownNotApplied","Applied","Unknown"],"disposi'
    'tion":"Terminal","operation_kind":"AIInterpretation","provider_invocations":["NotInv'
    'oked","Invoked","InvocationUnknown","NotApplicable"],"stages":["BeforeDispatch","Dis'
    'patch","ProviderProcessing","ResponseValidation","CallbackDelivery","Reconciliation"'
    ',"InternalCommit"]},{"customer_side_effects":["NotApplicable","KnownNotApplied","App'
    'lied","Unknown"],"disposition":"Terminal","operation_kind":"OutboundAction","provide'
    'r_invocations":["NotInvoked","Invoked","InvocationUnknown","NotApplicable"],"stages"'
    ':["BeforeDispatch","Dispatch","ProviderProcessing","ResponseValidation","CallbackDel'
    'ivery","Reconciliation","InternalCommit"]}],"required_sanitized_evidence":["actor_id'
    '","actor_role","rationale_reference","prior_failure_code"]},{"code":"ADMINISTRATOR_T'
    'ERMINAL_DISPOSITION","consumes_attempt":false,"consumes_budget":false,"outcomes":[{"'
    'customer_side_effects":["NotApplicable","KnownNotApplied","Applied","Unknown"],"disp'
    'osition":"Terminal","operation_kind":"AIInterpretation","provider_invocations":["Not'
    'Invoked","Invoked","InvocationUnknown","NotApplicable"],"stages":["BeforeDispatch","'
    'Dispatch","ProviderProcessing","ResponseValidation","CallbackDelivery","Reconciliati'
    'on","InternalCommit"]},{"customer_side_effects":["NotApplicable","KnownNotApplied","'
    'Applied","Unknown"],"disposition":"Terminal","operation_kind":"OutboundAction","prov'
    'ider_invocations":["NotInvoked","Invoked","InvocationUnknown","NotApplicable"],"stag'
    'es":["BeforeDispatch","Dispatch","ProviderProcessing","ResponseValidation","Callback'
    'Delivery","Reconciliation","InternalCommit"]}],"required_sanitized_evidence":["actor'
    '_id","actor_role","rationale_reference","prior_failure_code"]},{"code":"INTERNAL_TRA'
    'NSACTION_FAILED_BEFORE_COMMIT","consumes_attempt":false,"consumes_budget":false,"out'
    'comes":[{"customer_side_effects":["NotApplicable"],"disposition":"ReplaySameCommand"'
    ',"operation_kind":"AIInterpretation","provider_invocations":["NotApplicable"],"stage'
    's":["InternalCommit"]},{"customer_side_effects":["NotApplicable"],"disposition":"Rep'
    'laySameCommand","operation_kind":"OutboundAction","provider_invocations":["NotApplic'
    'able"],"stages":["InternalCommit"]}],"required_sanitized_evidence":["command_id","co'
    'rrelation_id","safe_error_class"]}],"globally_forbidden_evidence":["secrets","creden'
    'tials","unrestricted_provider_bodies","stack_traces","customer_text","raw_contact_de'
    'tails","arbitrary_metadata"],"operation_kind_rules":[{"allowed_customer_side_effects'
    '":["NotApplicable"],"material_revision_preserves_budget":false,"operation_kind":"AII'
    'nterpretation","success_closes_operation":true},{"allowed_customer_side_effects":["N'
    'otApplicable","KnownNotApplied","Applied","Unknown"],"material_revision_preserves_bu'
    'dget":true,"operation_kind":"OutboundAction","success_closes_operation":true}],"reco'
    'nciliation_rules":{"confirmed_not_applied_code":"RECONCILIATION_CONFIRMED_NOT_APPLIE'
    'D","confirmed_success_code":"RECONCILIATION_CONFIRMED_SUCCESS","deadline_seconds_aft'
    'er_started":900,"operation_kind":"OutboundAction","permanent_rejection_code":"RECONC'
    'ILIATION_PERMANENT_REJECTION","uncertainty_code":"OUTBOUND_OUTCOME_UNCERTAIN","unkno'
    'wn_side_effect_disposition":"ReconcileBeforeRetry","unresolved_terminal_code":"OUTBO'
    'UND_OUTCOME_UNRESOLVED"},"recovery_disposition_rules":[{"allowed_dispositions":["Ret'
    'rySameOperation","ReplaySameCommand","Terminal","NoDomainChange"],"customer_side_eff'
    'ect":"NotApplicable","operation_kind":"AIInterpretation"},{"allowed_dispositions":["'
    'ReplaySameCommand","Terminal","NoDomainChange"],"customer_side_effect":"NotApplicabl'
    'e","operation_kind":"OutboundAction"},{"allowed_dispositions":["RetrySameOperation",'
    '"ReviseProposal","Terminal"],"customer_side_effect":"KnownNotApplied","operation_kin'
    'd":"OutboundAction"},{"allowed_dispositions":["NoDomainChange"],"customer_side_effec'
    't":"Applied","operation_kind":"OutboundAction"},{"allowed_dispositions":["ReconcileB'
    'eforeRetry","Terminal"],"customer_side_effect":"Unknown","operation_kind":"OutboundA'
    'ction"}],"retry_delay_schedule":[{"delay_seconds":30,"failed_attempt_number":1,"oper'
    'ation_kind":"AIInterpretation"},{"delay_seconds":120,"failed_attempt_number":2,"oper'
    'ation_kind":"AIInterpretation"},{"delay_seconds":60,"failed_attempt_number":1,"opera'
    'tion_kind":"OutboundAction"},{"delay_seconds":300,"failed_attempt_number":2,"operati'
    'on_kind":"OutboundAction"}],"stale_attempt_thresholds":{"ai_running_seconds":300,"pe'
    'nding_seconds":120},"terminalization_rules":{"exhaustion_code":"RETRY_BUDGET_EXHAUST'
    'ED","final_attempt_is_terminal":true,"success_permanently_closes_operation":true,"te'
    'rminal_work_can_reopen":false,"unresolved_outbound_code":"OUTBOUND_OUTCOME_UNRESOLVE'
    'D"}}'
)
if (
    hashlib.sha256(DEMO_POLICY_CANONICAL_JSON.encode("utf-8")).hexdigest()
    != DEMO_POLICY_CONTENT_DIGEST
):
    raise RuntimeError("demonstration failure-recovery policy digest drift")
DEMO_POLICY_CONTENT = json.loads(DEMO_POLICY_CANONICAL_JSON)


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _create_policy_table() -> None:
    op.create_table(
        "failure_recovery_policy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_key", sa.String(100), nullable=False),
        sa.Column("semantic_version", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("policy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("operation_kind_rules", postgresql.JSONB(), nullable=False),
        sa.Column("failure_code_catalog", postgresql.JSONB(), nullable=False),
        sa.Column("attempt_budgets", postgresql.JSONB(), nullable=False),
        sa.Column("retry_delay_schedule", postgresql.JSONB(), nullable=False),
        sa.Column("stale_attempt_thresholds", postgresql.JSONB(), nullable=False),
        sa.Column("reconciliation_rules", postgresql.JSONB(), nullable=False),
        sa.Column("recovery_disposition_rules", postgresql.JSONB(), nullable=False),
        sa.Column("terminalization_rules", postgresql.JSONB(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retirement_reason", sa.String(100), nullable=True),
        sa.Column("retirement_reference", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "policy_key ~ '^[a-z][a-z0-9._-]{0,99}$'",
            name=op.f("ck_failure_recovery_policy_versions_policy_key_valid"),
        ),
        sa.CheckConstraint(
            "semantic_version ~ '^[0-9]+[.][0-9]+[.][0-9]+(-[0-9A-Za-z.-]+)?$'",
            name=op.f("ck_failure_recovery_policy_versions_semantic_version_valid"),
        ),
        sa.CheckConstraint(
            "revision > 0",
            name=op.f("ck_failure_recovery_policy_versions_revision_positive"),
        ),
        sa.CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_failure_recovery_policy_versions_content_digest_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('Draft', 'Active', 'Retired')",
            name=op.f("ck_failure_recovery_policy_versions_status_valid"),
        ),
        *(
            sa.CheckConstraint(
                f"jsonb_typeof({field}) = 'array'",
                name=op.f(f"ck_failure_recovery_policy_versions_{field}_array"),
            )
            for field in (
                "operation_kind_rules",
                "failure_code_catalog",
                "attempt_budgets",
                "retry_delay_schedule",
                "recovery_disposition_rules",
            )
        ),
        *(
            sa.CheckConstraint(
                f"jsonb_typeof({field}) = 'object'",
                name=op.f(f"ck_failure_recovery_policy_versions_{field}_object"),
            )
            for field in (
                "policy_snapshot",
                "stale_attempt_thresholds",
                "reconciliation_rules",
                "terminalization_rules",
            )
        ),
        sa.CheckConstraint(
            "(status IN ('Draft', 'Active') AND retired_at IS NULL "
            "AND retirement_reason IS NULL AND retirement_reference IS NULL) OR "
            "(status = 'Retired' AND retired_at IS NOT NULL "
            "AND retired_at >= effective_at AND retirement_reason IS NOT NULL "
            "AND retirement_reason ~ '^[A-Z][A-Z0-9_]{0,99}$')",
            name=op.f("ck_failure_recovery_policy_versions_retirement_fields_consistent"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_failure_recovery_policy_versions"),
        ),
        sa.UniqueConstraint(
            "policy_key",
            "semantic_version",
            "revision",
            name="uq_failure_recovery_policy_versions_identity",
        ),
        sa.UniqueConstraint(
            "content_digest",
            name="uq_failure_recovery_policy_versions_content_digest",
        ),
        sa.UniqueConstraint(
            "id",
            "semantic_version",
            "revision",
            "content_digest",
            name="uq_failure_recovery_policy_versions_assessment_identity",
        ),
    )
    op.create_index(
        "uq_failure_recovery_policy_versions_one_active",
        "failure_recovery_policy_versions",
        ["policy_key"],
        unique=True,
        postgresql_where=sa.text("status = 'Active'"),
    )
    op.create_index(
        "ix_failure_recovery_policy_versions_status_effective",
        "failure_recovery_policy_versions",
        ["status", "effective_at"],
    )


def _seed_demonstration_policy() -> None:
    policy_values = {
        "id": DEMO_POLICY_ID,
        "policy_key": DEMO_POLICY_KEY,
        "semantic_version": DEMO_POLICY_SEMANTIC_VERSION,
        "revision": DEMO_POLICY_REVISION,
        "content_digest": DEMO_POLICY_CONTENT_DIGEST,
        "effective_at": DEMO_POLICY_EFFECTIVE_AT,
        "status": "Active",
        "policy_snapshot": DEMO_POLICY_CONTENT,
        "operation_kind_rules": DEMO_POLICY_CONTENT["operation_kind_rules"],
        "failure_code_catalog": DEMO_POLICY_CONTENT["failure_code_catalog"],
        "attempt_budgets": DEMO_POLICY_CONTENT["attempt_budgets"],
        "retry_delay_schedule": DEMO_POLICY_CONTENT["retry_delay_schedule"],
        "stale_attempt_thresholds": DEMO_POLICY_CONTENT["stale_attempt_thresholds"],
        "reconciliation_rules": DEMO_POLICY_CONTENT["reconciliation_rules"],
        "recovery_disposition_rules": DEMO_POLICY_CONTENT["recovery_disposition_rules"],
        "terminalization_rules": DEMO_POLICY_CONTENT["terminalization_rules"],
    }
    if not context.is_offline_mode():
        policy_versions = sa.table(
            "failure_recovery_policy_versions",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("policy_key", sa.String(100)),
            sa.column("semantic_version", sa.String(32)),
            sa.column("revision", sa.Integer()),
            sa.column("content_digest", sa.String(64)),
            sa.column("effective_at", sa.DateTime(timezone=True)),
            sa.column("status", sa.String(16)),
            sa.column("policy_snapshot", postgresql.JSONB()),
            sa.column("operation_kind_rules", postgresql.JSONB()),
            sa.column("failure_code_catalog", postgresql.JSONB()),
            sa.column("attempt_budgets", postgresql.JSONB()),
            sa.column("retry_delay_schedule", postgresql.JSONB()),
            sa.column("stale_attempt_thresholds", postgresql.JSONB()),
            sa.column("reconciliation_rules", postgresql.JSONB()),
            sa.column("recovery_disposition_rules", postgresql.JSONB()),
            sa.column("terminalization_rules", postgresql.JSONB()),
        )
        op.get_bind().execute(policy_versions.insert().values(**policy_values))
        return

    def sql_string(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def jsonb_literal(value: object) -> str:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        canonical = canonical.replace(":", r"\:")
        return f"{sql_string(canonical)}::jsonb"

    op.execute(
        "INSERT INTO failure_recovery_policy_versions "
        "(id, policy_key, semantic_version, revision, content_digest, effective_at, status, "
        "policy_snapshot, operation_kind_rules, failure_code_catalog, attempt_budgets, "
        "retry_delay_schedule, stale_attempt_thresholds, reconciliation_rules, "
        "recovery_disposition_rules, terminalization_rules) VALUES ("
        f"{sql_string(str(DEMO_POLICY_ID))}::uuid, {sql_string(DEMO_POLICY_KEY)}, "
        f"{sql_string(DEMO_POLICY_SEMANTIC_VERSION)}, {DEMO_POLICY_REVISION}, "
        f"{sql_string(DEMO_POLICY_CONTENT_DIGEST)}, "
        f"{sql_string(DEMO_POLICY_EFFECTIVE_AT.isoformat())}::timestamptz, 'Active', "
        f"{jsonb_literal(DEMO_POLICY_CONTENT)}, "
        f"{jsonb_literal(DEMO_POLICY_CONTENT['operation_kind_rules'])}, "
        f"{jsonb_literal(DEMO_POLICY_CONTENT['failure_code_catalog'])}, "
        f"{jsonb_literal(DEMO_POLICY_CONTENT['attempt_budgets'])}, "
        f"{jsonb_literal(DEMO_POLICY_CONTENT['retry_delay_schedule'])}, "
        f"{jsonb_literal(DEMO_POLICY_CONTENT['stale_attempt_thresholds'])}, "
        f"{jsonb_literal(DEMO_POLICY_CONTENT['reconciliation_rules'])}, "
        f"{jsonb_literal(DEMO_POLICY_CONTENT['recovery_disposition_rules'])}, "
        f"{jsonb_literal(DEMO_POLICY_CONTENT['terminalization_rules'])})"
    )


def _add_attempt_recovery_fields() -> None:
    columns = (
        sa.Column("failure_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("failure_policy_semantic_version", sa.String(32), nullable=True),
        sa.Column("failure_policy_revision", sa.Integer(), nullable=True),
        sa.Column("failure_policy_digest", sa.String(64), nullable=True),
        sa.Column("failure_stage", sa.String(32), nullable=True),
        sa.Column("provider_invocation", sa.String(32), nullable=True),
        sa.Column("customer_side_effect", sa.String(32), nullable=True),
        sa.Column("recovery_disposition", sa.String(32), nullable=True),
        sa.Column("maximum_attempts", sa.SmallInteger(), nullable=True),
        sa.Column("remaining_attempts", sa.SmallInteger(), nullable=True),
        sa.Column("next_eligible_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_retry_after_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_status", sa.String(32), nullable=True),
        sa.Column("reconciliation_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sanitized_evidence_reference", sa.String(200), nullable=True),
        sa.Column("sanitized_evidence_hash", sa.String(64), nullable=True),
        sa.Column("terminal_reason", sa.String(100), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in columns:
        op.add_column("integration_attempts", column)

    op.create_foreign_key(
        "fk_attempt_failure_recovery_policy_identity",
        "integration_attempts",
        "failure_recovery_policy_versions",
        [
            "failure_policy_id",
            "failure_policy_semantic_version",
            "failure_policy_revision",
            "failure_policy_digest",
        ],
        ["id", "semantic_version", "revision", "content_digest"],
        ondelete="RESTRICT",
    )
    checks = (
        (
            "failure_policy_digest_valid",
            "failure_policy_digest IS NULL OR failure_policy_digest ~ '^[0-9a-f]{64}$'",
        ),
        (
            "sanitized_evidence_hash_valid",
            "sanitized_evidence_hash IS NULL OR sanitized_evidence_hash ~ '^[0-9a-f]{64}$'",
        ),
        (
            "terminal_reason_valid",
            "terminal_reason IS NULL OR terminal_reason ~ '^[A-Z][A-Z0-9_]{0,99}$'",
        ),
        (
            "failure_stage_valid",
            f"failure_stage IS NULL OR failure_stage IN ({_sql_values(FAILURE_STAGES)})",
        ),
        (
            "provider_invocation_valid",
            "provider_invocation IS NULL OR provider_invocation IN "
            f"({_sql_values(PROVIDER_INVOCATIONS)})",
        ),
        (
            "customer_side_effect_valid",
            "customer_side_effect IS NULL OR customer_side_effect IN "
            f"({_sql_values(CUSTOMER_SIDE_EFFECTS)})",
        ),
        (
            "recovery_disposition_valid",
            "recovery_disposition IS NULL OR recovery_disposition IN "
            f"({_sql_values(RECOVERY_DISPOSITIONS)})",
        ),
        (
            "reconciliation_status_valid",
            "reconciliation_status IS NULL OR reconciliation_status IN "
            f"({_sql_values(RECONCILIATION_STATUSES)})",
        ),
        (
            "recovery_assessment_complete",
            "(failure_policy_id IS NULL AND failure_policy_semantic_version IS NULL "
            "AND failure_policy_revision IS NULL AND failure_policy_digest IS NULL "
            "AND failure_stage IS NULL AND provider_invocation IS NULL "
            "AND customer_side_effect IS NULL AND recovery_disposition IS NULL "
            "AND maximum_attempts IS NULL AND remaining_attempts IS NULL "
            "AND next_eligible_at IS NULL AND provider_retry_after_at IS NULL "
            "AND reconciliation_status IS NULL AND reconciliation_deadline IS NULL "
            "AND sanitized_evidence_reference IS NULL AND sanitized_evidence_hash IS NULL "
            "AND terminal_reason IS NULL AND assessed_at IS NULL) OR "
            "(failure_policy_id IS NOT NULL "
            "AND failure_policy_semantic_version IS NOT NULL "
            "AND char_length(trim(failure_policy_semantic_version)) > 0 "
            "AND failure_policy_revision IS NOT NULL AND failure_policy_revision > 0 "
            "AND failure_policy_digest IS NOT NULL "
            "AND failure_stage IS NOT NULL AND provider_invocation IS NOT NULL "
            "AND customer_side_effect IS NOT NULL AND recovery_disposition IS NOT NULL "
            "AND maximum_attempts IS NOT NULL "
            "AND maximum_attempts BETWEEN attempt_number AND 3 "
            "AND remaining_attempts IS NOT NULL "
            "AND remaining_attempts = maximum_attempts - attempt_number "
            "AND reconciliation_status IS NOT NULL "
            "AND sanitized_evidence_hash IS NOT NULL AND assessed_at IS NOT NULL "
            "AND assessed_at >= created_at)",
        ),
        (
            "ai_recovery_assessment_valid",
            "failure_policy_id IS NULL OR (operation_kind = 'AIInterpretation' "
            "AND customer_side_effect = 'NotApplicable' "
            "AND reconciliation_status = 'NotRequired' AND reconciliation_deadline IS NULL "
            "AND ((state = 'RetryableFailure' "
            "AND recovery_disposition = 'RetrySameOperation' "
            "AND remaining_attempts > 0 AND next_eligible_at IS NOT NULL "
            "AND next_eligible_at >= assessed_at "
            "AND (provider_retry_after_at IS NULL "
            "OR (provider_retry_after_at >= assessed_at "
            "AND next_eligible_at >= provider_retry_after_at)) "
            "AND terminal_reason IS NULL) OR "
            "(state = 'TerminalFailure' AND recovery_disposition = 'Terminal' "
            "AND next_eligible_at IS NULL AND provider_retry_after_at IS NULL "
            "AND terminal_reason IS NOT NULL)))",
        ),
    )
    for name, condition in checks:
        op.create_check_constraint(
            op.f(f"ck_integration_attempts_{name}"),
            "integration_attempts",
            condition,
        )
    op.create_index(
        "ix_integration_attempts_policy_assessed",
        "integration_attempts",
        ["failure_policy_id", "assessed_at"],
    )
    op.create_index(
        "ix_integration_attempts_recovery_eligibility",
        "integration_attempts",
        ["state", "next_eligible_at"],
    )


def _add_request_recovery_fields() -> None:
    op.add_column("service_requests", sa.Column("recovery_target", sa.String(32)))
    op.add_column(
        "service_requests",
        sa.Column("recovery_attempt_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column("service_requests", sa.Column("failure_summary_code", sa.String(100)))
    op.add_column(
        "service_requests",
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
    )
    op.create_foreign_key(
        "fk_service_request_recovery_attempt",
        "service_requests",
        "integration_attempts",
        ["recovery_attempt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_service_requests_failure_summary_code_valid"),
        "service_requests",
        "failure_summary_code IS NULL OR failure_summary_code ~ '^[A-Z][A-Z0-9_]{0,99}$'",
    )
    op.create_check_constraint(
        op.f("ck_service_requests_recovery_fields_consistent"),
        "service_requests",
        "(status = 'RetryableFailure' "
        "AND recovery_target IS NOT NULL "
        f"AND recovery_target IN ({_sql_values(RECOVERY_TARGETS)}) "
        "AND recovery_attempt_id IS NOT NULL AND failure_summary_code IS NOT NULL "
        "AND current_queue IS NOT NULL "
        "AND current_queue = 'FailedRetryRequired' AND terminal_at IS NULL) OR "
        "(status = 'TerminalFailure' AND recovery_target IS NULL "
        "AND recovery_attempt_id IS NOT NULL AND failure_summary_code IS NOT NULL "
        "AND current_queue IS NULL AND terminal_at IS NOT NULL "
        "AND terminal_at >= created_at) OR "
        "(status NOT IN ('RetryableFailure', 'TerminalFailure') "
        "AND recovery_target IS NULL AND recovery_attempt_id IS NULL "
        "AND failure_summary_code IS NULL AND terminal_at IS NULL)",
    )
    op.create_index(
        "ix_service_requests_recovery_attempt_id",
        "service_requests",
        ["recovery_attempt_id"],
    )


def upgrade() -> None:
    _create_policy_table()
    _seed_demonstration_policy()
    _add_attempt_recovery_fields()
    _add_request_recovery_fields()


def downgrade() -> None:
    op.drop_index("ix_service_requests_recovery_attempt_id", table_name="service_requests")
    op.drop_constraint(
        op.f("ck_service_requests_recovery_fields_consistent"),
        "service_requests",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_service_requests_failure_summary_code_valid"),
        "service_requests",
        type_="check",
    )
    op.drop_constraint(
        "fk_service_request_recovery_attempt",
        "service_requests",
        type_="foreignkey",
    )
    for column in (
        "terminal_at",
        "failure_summary_code",
        "recovery_attempt_id",
        "recovery_target",
    ):
        op.drop_column("service_requests", column)

    op.drop_index(
        "ix_integration_attempts_recovery_eligibility",
        table_name="integration_attempts",
    )
    op.drop_index(
        "ix_integration_attempts_policy_assessed",
        table_name="integration_attempts",
    )
    for name in (
        "ai_recovery_assessment_valid",
        "recovery_assessment_complete",
        "reconciliation_status_valid",
        "recovery_disposition_valid",
        "customer_side_effect_valid",
        "provider_invocation_valid",
        "failure_stage_valid",
        "terminal_reason_valid",
        "sanitized_evidence_hash_valid",
        "failure_policy_digest_valid",
    ):
        op.drop_constraint(
            op.f(f"ck_integration_attempts_{name}"),
            "integration_attempts",
            type_="check",
        )
    op.drop_constraint(
        "fk_attempt_failure_recovery_policy_identity",
        "integration_attempts",
        type_="foreignkey",
    )
    for column in (
        "assessed_at",
        "terminal_reason",
        "sanitized_evidence_hash",
        "sanitized_evidence_reference",
        "reconciliation_deadline",
        "reconciliation_status",
        "provider_retry_after_at",
        "next_eligible_at",
        "remaining_attempts",
        "maximum_attempts",
        "recovery_disposition",
        "customer_side_effect",
        "provider_invocation",
        "failure_stage",
        "failure_policy_digest",
        "failure_policy_revision",
        "failure_policy_semantic_version",
        "failure_policy_id",
    ):
        op.drop_column("integration_attempts", column)
    op.drop_table("failure_recovery_policy_versions")
