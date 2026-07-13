"""Manager/administrator terminal-disposition transport boundary."""

from ai_operations_automation.terminal_failure.models import (
    MarkTerminalFailureCommand,
    MarkTerminalFailureExpectedVersions,
    MarkTerminalFailureRequest,
    MarkTerminalFailureResponse,
    MarkTerminalFailureResult,
    MarkTerminalFailureVersions,
    TerminalRationale,
)
from ai_operations_automation.terminal_failure.parsing import (
    MAX_COMMAND_BODY_BYTES,
    command_idempotency_key,
    parse_mark_terminal_failure_command,
    validate_json_content_type,
)

__all__ = [
    "MAX_COMMAND_BODY_BYTES",
    "MarkTerminalFailureCommand",
    "MarkTerminalFailureExpectedVersions",
    "MarkTerminalFailureRequest",
    "MarkTerminalFailureResponse",
    "MarkTerminalFailureResult",
    "MarkTerminalFailureVersions",
    "TerminalRationale",
    "command_idempotency_key",
    "parse_mark_terminal_failure_command",
    "validate_json_content_type",
]
