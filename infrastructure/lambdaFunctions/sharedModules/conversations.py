"""Conversation-ID handling: validation, user-to-DM resolution, DM enumeration.

A read tool that accepts an unusable `channel` and returns an empty list is
indistinguishable, from inside a polling loop, from a healthy poll of a quiet
conversation: both are {"ok": true, "messages": []}. Only one of those is
recoverable, and the caller cannot tell which it got, so a monitoring loop built
on a wrong target reports healthy forever. Every read path therefore resolves its
channel argument through here and fails loudly when it cannot use it.

DM enumeration lives here too. Slack's conversations.list omits DMs unless asked
for them by type, and the message registry only knows DMs a message has already
arrived in, which is circular exactly when the problem is a message you missed.
"""

from functools import lru_cache

from slack_sdk.errors import SlackApiError

from sharedModules.slack import agent_client

# Conversations that can be read: public channel, private group, direct message.
CONVERSATION_PREFIXES = ("C", "D", "G")
# People, which resolve to their DM conversation rather than being read directly.
USER_PREFIXES = ("U", "W")

PAGE_LIMIT = 200


class ChannelArgumentError(ValueError):
    """An unusable channel argument. The message names the valid forms."""


def _looks_like_id(value: str, prefixes: tuple[str, ...]) -> bool:
    return len(value) >= 2 and value[0] in prefixes and value[1:].isalnum()


def is_conversation_id(value: str) -> bool:
    """True for channel (C), private group (G), and DM (D) IDs."""
    return _looks_like_id(value, CONVERSATION_PREFIXES)


def is_user_id(value: str) -> bool:
    """True for user (U) and enterprise-user (W) IDs."""
    return _looks_like_id(value, USER_PREFIXES)


@lru_cache(maxsize=256)
def dm_channel_for_user(agent_id: str, user_id: str) -> str:
    """The DM conversation between this agent and one user, opening it if needed.

    Cached for the container's lifetime because the DM ID for a given pair never
    changes. conversations.open is idempotent and notifies nobody, so calling it
    from a read path is safe.
    """
    return agent_client(agent_id).conversations_open(users=user_id)["channel"]["id"]


def resolve_conversation(channel: str, agent_id: str) -> str:
    """Map a caller's `channel` argument onto a conversation ID that can be read.

    Conversation IDs pass through. A user ID resolves to that person's DM, which
    is what find_user's `id` already implies for send_message. Anything else
    raises rather than quietly reading a conversation that does not exist.
    """
    value = channel.strip()
    if not value:
        raise ChannelArgumentError("channel must not be empty")
    if is_conversation_id(value):
        return value
    if is_user_id(value):
        return dm_channel_for_user(agent_id, value)
    raise ChannelArgumentError(
        f"{value!r} is not a conversation ID. Pass a channel ID (C0123456789, from "
        "list_channels or find_channel), a DM ID (D0123456789, from list_dms), or a "
        "user ID (U0123456789, from find_user) to read that person's DM."
    )


def list_dm_conversations(agent_id: str) -> list[dict]:
    """Every direct and group DM this agent's Slack app is part of.

    Each entry is {id, user, is_group}. A one-to-one DM carries the other
    person's `user` with is_group False; a group DM (multi-person DM, id starting
    with G) carries user "" with is_group True, and its members are fetched
    separately via mpim_member_ids only when a caller needs them, so a plain DM
    lookup stays a single API call. Uses the agent's own token: these are the
    agent's conversations, and the relay app is not a member of them (and, for a
    group DM, cannot be added after the group is created).

    Listing DMs needs im:read, and listing group DMs needs mpim:read. If mpim:read
    is absent the group DMs are omitted rather than failing the whole listing; if
    im:read is also absent the missing_scope propagates for the caller to report.
    """
    client = agent_client(agent_id)
    try:
        return _list_dm_and_group(client, "im,mpim")
    except SlackApiError as e:
        if e.response.get("error") == "missing_scope":
            return _list_dm_and_group(client, "im")
        raise


def _list_dm_and_group(client, types: str) -> list[dict]:
    conversations: list[dict] = []
    cursor = None
    while True:
        kwargs = {"types": types, "exclude_archived": True, "limit": PAGE_LIMIT}
        if cursor:
            kwargs["cursor"] = cursor
        resp = client.conversations_list(**kwargs)
        for conv in resp["channels"]:
            if conv.get("is_user_deleted"):
                continue
            if conv.get("is_mpim"):
                # Channel-backed group DMs carry a real name (e.g. "JustWILMA2");
                # surface it so the group is findable by name, not only members.
                conversations.append(
                    {"id": conv["id"], "user": "", "is_group": True, "name": conv.get("name", "")}
                )
            else:
                conversations.append(
                    {"id": conv["id"], "user": conv.get("user", ""), "is_group": False, "name": ""}
                )
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    return conversations


def mpim_member_ids(agent_id: str, channel: str) -> list[str]:
    """The user ids in a group DM, via the agent's token (the agent is a member).

    A separate call because conversations.list does not return mpim members, and
    a plain DM listing should not pay for it. Includes the agent's own bot user.
    """
    client = agent_client(agent_id)
    members: list[str] = []
    cursor = None
    while True:
        kwargs = {"channel": channel, "limit": PAGE_LIMIT}
        if cursor:
            kwargs["cursor"] = cursor
        resp = client.conversations_members(**kwargs)
        members.extend(resp.get("members", []))
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    return members


def user_dm_index(agent_id: str) -> dict[str, str]:
    """user ID -> existing DM ID. Opens nothing, so it is safe to call on lookups.

    Group DMs carry no single user, so they fall out of this one-to-one index.
    """
    return {c["user"]: c["id"] for c in list_dm_conversations(agent_id) if c["user"]}
