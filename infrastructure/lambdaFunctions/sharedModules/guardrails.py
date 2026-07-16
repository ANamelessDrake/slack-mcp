"""Server-side conversation guardrails (DESIGN.md section 8).

Two agents that auto-respond to each other are an infinite loop; these checks
live in the server so no prompt can talk an agent around them.

- Turn budget: at most AGENT_TURN_BUDGET consecutive agent messages in a
  conversation scope (a thread, or a channel's top level) with no human
  message. A human message resets the count.
- Cooldown: an agent must wait AGENT_COOLDOWN_SECONDS after a different
  agent's message in the same scope before sending.
"""

import os
import time

from sharedModules.dynamo import recent_messages

SCAN_WINDOW = 25


def agent_send_veto(channel: str, thread_ts: str, sender_agent_id: str) -> str | None:
    """Return a refusal message if this send violates a guardrail, else None."""
    budget = int(os.environ.get("AGENT_TURN_BUDGET", "6"))
    cooldown = float(os.environ.get("AGENT_COOLDOWN_SECONDS", "3"))

    recent = recent_messages(channel, limit=SCAN_WINDOW)
    if thread_ts:
        scope = [
            m for m in recent
            if m.get("thread_ts") == thread_ts or m.get("ts") == thread_ts
        ]
    else:
        scope = [m for m in recent if not m.get("thread_ts")]
    if not scope:
        return None

    consecutive = 0
    for message in reversed(scope):
        if message.get("sender_type") == "agent":
            consecutive += 1
        else:
            break
    if consecutive >= budget:
        return (
            f"Turn budget reached: {consecutive} consecutive agent messages with no "
            "human reply in this conversation. Do not send again here until a human "
            "responds."
        )

    last = scope[-1]
    if last.get("sender_type") == "agent" and last.get("agent_id") != sender_agent_id:
        try:
            last_ts = float(last.get("ts", "0"))
        except ValueError:
            last_ts = 0.0
        elapsed = time.time() - last_ts
        if elapsed < cooldown:
            return (
                f"Cooldown: another agent posted {elapsed:.1f}s ago; wait at least "
                f"{cooldown:.0f}s between agent messages in a conversation."
            )
    return None
