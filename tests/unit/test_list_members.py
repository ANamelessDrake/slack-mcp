import boto3
import pytest
import tools.list_members as lm
from moto import mock_aws
from sharedModules import dynamo


class FakeRelay:
    def __init__(self, members, users=None, fail_on=()):
        self._members = members
        self._users = users or {}
        self._fail_on = set(fail_on)
        self.info_calls = []

    def conversations_members(self, **kwargs):
        return {"members": self._members}

    def users_info(self, user):
        self.info_calls.append(user)
        if user in self._fail_on:
            from slack_sdk.errors import SlackApiError

            raise SlackApiError("nope", {"error": "user_not_found"})
        return {"user": self._users[user]}


@pytest.fixture()
def table(monkeypatch):
    monkeypatch.setenv("MESSAGES_TABLE", "test-messages")
    dynamo.messages_table.cache_clear()
    with mock_aws():
        t = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="test-messages",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield t
    dynamo.messages_table.cache_clear()


def test_resolves_names_and_flags_bots(table, monkeypatch):
    fake = FakeRelay(
        members=["U1", "U2"],
        users={
            "U1": {"real_name": "Susan Redman", "profile": {}},
            "U2": {"real_name": "WILMA", "is_bot": True, "profile": {"display_name": "WILMA"}},
        },
    )
    monkeypatch.setattr(lm, "relay_client", lambda: fake)

    result = lm.list_members("C1")

    assert result["ok"] is True
    assert result["members"] == [
        {"id": "U1", "name": "Susan Redman", "is_bot": False},
        {"id": "U2", "name": "WILMA", "is_bot": True},
    ]


def test_second_call_uses_the_cache(table, monkeypatch):
    fake = FakeRelay(members=["U1"], users={"U1": {"real_name": "Susan Redman", "profile": {}}})
    monkeypatch.setattr(lm, "relay_client", lambda: fake)

    lm.list_members("C1")
    lm.list_members("C1")

    # One Slack lookup per person, not per listing
    assert fake.info_calls == ["U1"]


def test_ingest_cache_row_without_is_bot_is_enriched(table, monkeypatch):
    # Ingest writes name-only rows; a reader that needs is_bot fills it in once
    table.put_item(Item={"PK": "USER#U9", "SK": "META", "name": "Greg Grant"})
    fake = FakeRelay(
        members=["U9"], users={"U9": {"real_name": "Greg Grant", "profile": {}}}
    )
    monkeypatch.setattr(lm, "relay_client", lambda: fake)

    result = lm.list_members("C1")

    assert result["members"] == [{"id": "U9", "name": "Greg Grant", "is_bot": False}]
    assert fake.info_calls == ["U9"]
    assert dynamo.get_cached_user("U9")["is_bot"] is False


def test_unresolvable_member_still_listed(table, monkeypatch):
    fake = FakeRelay(members=["UGHOST"], users={}, fail_on=["UGHOST"])
    monkeypatch.setattr(lm, "relay_client", lambda: fake)

    result = lm.list_members("C1")

    assert result["members"] == [{"id": "UGHOST", "name": "", "is_bot": False}]


def test_slack_error_is_surfaced(table, monkeypatch):
    from slack_sdk.errors import SlackApiError

    class Boom:
        def conversations_members(self, **kwargs):
            raise SlackApiError("boom", {"error": "channel_not_found"})

    monkeypatch.setattr(lm, "relay_client", lambda: Boom())

    assert lm.list_members("C404") == {"ok": False, "error": "channel_not_found"}
