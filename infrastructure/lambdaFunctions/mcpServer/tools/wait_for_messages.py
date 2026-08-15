import time

from sharedModules.dynamo import cursor_scope, heartbeat_session, set_cursor
from sharedModules.events import EventSubscription
from sharedModules.identity import current_agent_id

from .check_messages import check_messages

MAX_WAIT_SECONDS = 840  # stays under the Lambda's 900s timeout


def wait_for_messages(
    timeout_seconds: int = 60,
    channel: str = "",
    mentions_only: bool = False,
    from_user: str = "",
) -> dict:
    """Wait for the next new Slack message and return it as soon as it arrives.

    Use this to hold a live conversation: it blocks until someone posts a new
    message, then returns immediately (or returns empty with timed_out true if
    nothing arrives within timeout_seconds, max 840). Call it again to keep
    waiting. `channel` may be one conversation ID, several separated by commas,
    or empty to watch everything. Messages you sent yourself are never
    returned. Set mentions_only to true to wake only for messages that
    @mention you; set from_user to a user ID (from find_user) to wake only for
    that person's messages.

    As with check_messages, each filter keeps its own read position, so waiting
    with a filter never consumes messages for an unfiltered check later.
    """
    identity = current_agent_id()
    timeout = max(5, min(int(timeout_seconds), MAX_WAIT_SECONDS))
    deadline = time.monotonic() + timeout
    heartbeat_session(identity, timeout)

    requested = [c.strip() for c in channel.split(",") if c.strip()]
    watched = set(requested)
    subscription = EventSubscription(
        f"slack/messages/{requested[0]}" if len(requested) == 1 else "slack/messages/*"
    )
    try:
        subscription.connect()

        # Subscribe first, then drain the backlog: a message landing between the
        # two is caught by at least one path, and the cursor dedupes the overlap.
        backlog = check_messages(channel, mentions_only=mentions_only, from_user=from_user)
        if not backlog.get("ok", False):
            return backlog
        if backlog["messages"]:
            return {"ok": True, "messages": backlog["messages"]}

        while True:
            event = subscription.next_event(deadline)
            if event is None:
                return {"ok": True, "messages": [], "timed_out": True}
            # Unwatched channels stay untouched: their messages remain new for
            # whoever does watch them, so no cursor movement here.
            if watched and event.get("channel") not in watched:
                continue
            # Advance the cursor for this filter view only, matching the backlog
            # drain above, so a filtered wait never consumes another view's mail.
            scope = cursor_scope(event["channel"], mentions_only, from_user)
            set_cursor(identity, scope, event["ts"])
            if event.get("agent_id", "") == identity:
                continue
            if mentions_only and identity not in event.get("mentions_agents", []):
                continue
            if from_user and event.get("user", "") != from_user:
                continue
            return {"ok": True, "messages": [event]}
    except Exception as e:
        return {"ok": False, "error": f"wait failed: {e}"}
    finally:
        subscription.close()
