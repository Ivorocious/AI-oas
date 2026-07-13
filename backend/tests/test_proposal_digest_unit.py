from ai_operations_automation.proposal.digest import proposal_payload_digest


def payload(**changes):
    value = {
        "action_type": "CustomerMessage",
        "destination": {"kind": "Email", "value": "customer@example.test"},
        "content": "Your service request is ready.",
        "scheduling": None,
    }
    value.update(changes)
    return value


def test_digest_is_stable_and_key_order_independent() -> None:
    original = payload()
    reordered = {
        "content": original["content"],
        "scheduling": None,
        "destination": {"value": "customer@example.test", "kind": "Email"},
        "action_type": "CustomerMessage",
    }
    assert proposal_payload_digest(original) == proposal_payload_digest(reordered)


def test_digest_changes_for_execution_content_but_excludes_metadata() -> None:
    original = payload()
    with_metadata = {**original, "id": "ignored", "version": 99, "actor_id": "ignored"}
    assert proposal_payload_digest(original) == proposal_payload_digest(with_metadata)
    assert proposal_payload_digest(original) != proposal_payload_digest(
        payload(content="A materially different response.")
    )
