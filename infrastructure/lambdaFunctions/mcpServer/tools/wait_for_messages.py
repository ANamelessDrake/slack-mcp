import time

from sharedModules.dynamo import heartbeat_session, set_cursor
from sharedModules.events import EventSubscription
from sharedModules.identity import current_agent_id

from .check_messages import check_messages

MAX_WAIT_SECONDS = 110  # stays under the Lambda's 120s timeout


def wait_for_messages(
    timeout_seconds: int = 60, channel: str = "", mentions_only: bool = False
) -> dict:
    """Wait for the next new Slack message and return it as soon as it arrives.

    Use this to hold a live conversation: it blocks until someone posts a new
    message, then returns immediately (or returns empty with timed_out true if
    nothing arrives within timeout_seconds, max 110). Call it again to keep
    waiting. Pass a channel ID to watch one channel, or leave it empty to watch
    all of them. Messages you sent yourself are never returned. Set
    mentions_only to true to wake only for messages that @mention you.
    """
    identity = current_agent_id()
    timeout = max(5, min(int(timeout_seconds), MAX_WAIT_SECONDS))
    deadline = time.monotonic() + timeout
    heartbeat_session(identity, timeout)

    subscription = EventSubscription(
        f"slack/messages/{channel}" if channel else "slack/messages/*"
    )
    try:
        subscription.connect()

        # Subscribe first, then drain the backlog: a message landing between the
        # two is caught by at least one path, and the cursor dedupes the overlap.
        backlog = check_messages(channel, mentions_only=mentions_only)
        if not backlog.get("ok", False):
            return backlog
        if backlog["messages"]:
            return {"ok": True, "messages": backlog["messages"]}

        while True:
            event = subscription.next_event(deadline)
            if event is None:
                return {"ok": True, "messages": [], "timed_out": True}
            set_cursor(identity, event["channel"], event["ts"])
            if event.get("agent_id", "") == identity:
                continue
            if mentions_only and identity not in event.get("mentions_agents", []):
                continue
            return {"ok": True, "messages": [event]}
    except Exception as e:
        return {"ok": False, "error": f"wait failed: {e}"}
    finally:
        subscription.close()
