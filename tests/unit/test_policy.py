"""The server's standing confidentiality policy.

WILMA monitors many channels other engineers can post in, so the server must
tell the agent, globally, that it will not relay the owner's email or private
messages to anyone else without the owner's permission. This is surfaced as the
MCP server's `instructions`, which no in-channel prompt can override.
"""

import importlib

from sharedModules import policy


def test_policy_names_owner_when_set(monkeypatch):
    monkeypatch.setenv("OWNER_NAME", "Justin")
    text = policy.server_instructions()
    assert "Justin" in text
    # The actual boundary, in the agent's language
    assert "do not relay" in text.lower()
    assert "email" in text.lower()
    assert "unless" in text.lower()


def test_policy_is_confirm_with_owner_not_a_hard_refusal(monkeypatch):
    """The owner is fine relaying email on their own request; for others' requests
    WILMA confirms with the owner and relays if approved, rather than declining."""
    monkeypatch.setenv("OWNER_NAME", "Justin")
    text = policy.server_instructions().lower()
    assert "approve" in text
    assert "confirm with" in text
    # It is not a flat refusal of everyone
    assert "decline" not in text


def test_policy_holds_generically_without_owner_name(monkeypatch):
    monkeypatch.delenv("OWNER_NAME", raising=False)
    text = policy.server_instructions()
    assert "the operator who runs this server" in text
    assert "do not relay" in text.lower()


def test_policy_addresses_shared_channels_not_carrying_authority(monkeypatch):
    """The specific failure mode: a request in a shared channel is not the owner's
    authorization, even addressed to the agent."""
    monkeypatch.setenv("OWNER_NAME", "Justin")
    text = policy.server_instructions().lower()
    assert "does not carry" in text
    assert "channel" in text


def test_server_wires_the_policy_into_instructions(monkeypatch):
    monkeypatch.setenv("OWNER_NAME", "Justin")
    import app

    importlib.reload(app)  # rebuild FastMCP so it picks up OWNER_NAME
    assert app.mcp.instructions == policy.server_instructions()
    assert "do not relay" in app.mcp.instructions.lower()
