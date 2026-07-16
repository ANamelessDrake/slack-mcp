import boto3
import pytest
import tools.download_file as df
import tools.read_file as rf
from mcp.server.fastmcp import Image
from moto import mock_aws
from sharedModules import dynamo, files, identity

PNG = b"\x89PNG\r\n\x1a\n" + b"fake image bytes"


@pytest.fixture()
def table(monkeypatch):
    monkeypatch.setenv("MESSAGES_TABLE", "test-messages")
    monkeypatch.setenv("MAX_FILE_DOWNLOAD_MB", "10")
    monkeypatch.setenv("RELAY_BOT_TOKEN", "xoxb-test")
    dynamo.messages_table.cache_clear()
    files._relay_token.cache_clear()
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
    files._relay_token.cache_clear()


def _seed_file(table, file_id="F1", name="shot.png", mimetype="image/png", size=24):
    table.put_item(
        Item={
            "PK": f"FILE#{file_id}",
            "SK": "META",
            "file_id": file_id,
            "name": name,
            "mimetype": mimetype,
            "size": size,
            "url_private": f"https://files.slack.com/{file_id}",
            "channel": "C1",
            "ts": "1.0",
        }
    )


def test_image_returns_viewable_content(table, monkeypatch):
    _seed_file(table)
    monkeypatch.setattr(rf, "fetch_bytes", lambda record: PNG)

    result = rf.read_file("F1")

    assert isinstance(result, Image)
    assert result.data == PNG
    assert result._format == "png"


def test_text_file_returns_text(table, monkeypatch):
    _seed_file(table, file_id="F2", name="log.txt", mimetype="text/plain", size=11)
    monkeypatch.setattr(rf, "fetch_bytes", lambda record: b"hello world")

    result = rf.read_file("F2")

    assert result["ok"] is True
    assert result["content"] == "hello world"
    assert result["truncated"] is False


def test_long_text_is_truncated(table, monkeypatch):
    _seed_file(table, file_id="F3", name="big.log", mimetype="text/plain", size=99999)
    monkeypatch.setattr(rf, "fetch_bytes", lambda record: b"x" * 60000)

    result = rf.read_file("F3")

    assert result["truncated"] is True
    assert len(result["content"]) == rf.MAX_TEXT_CHARS


def test_binary_points_at_download(table):
    _seed_file(table, file_id="F4", name="report.pdf", mimetype="application/pdf")

    result = rf.read_file("F4")

    assert result["ok"] is False
    assert "download_file" in result["error"]


def test_unknown_file_is_refused(table):
    result = rf.read_file("F404")
    assert result["ok"] is False
    assert "not in this system's message history" in result["error"]


def test_size_cap_enforced_before_fetch(table, monkeypatch):
    monkeypatch.setenv("MAX_FILE_DOWNLOAD_MB", "1")
    _seed_file(table, file_id="F5", name="huge.txt", mimetype="text/plain", size=5 * 1048576)

    def must_not_fetch(url, timeout=0):
        raise AssertionError("fetch attempted despite oversized metadata")

    monkeypatch.setattr(files.urllib.request, "urlopen", must_not_fetch)

    result = rf.read_file("F5")
    assert result["ok"] is False
    assert "over the 1 MB limit" in result["error"]


def test_size_cap_catches_understated_metadata(table, monkeypatch):
    monkeypatch.setenv("MAX_FILE_DOWNLOAD_MB", "1")
    _seed_file(table, file_id="F6", name="liar.txt", mimetype="text/plain", size=10)

    class FakeResp:
        def read(self, n):
            return b"x" * n  # more than the cap, whatever is asked for

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(files.urllib.request, "urlopen", lambda req, timeout=0: FakeResp())

    with pytest.raises(files.FileTooLarge):
        files.fetch_bytes(files.get_file_record("F6"))


def test_download_file_builds_absolute_url_and_curl(table):
    _seed_file(table, file_id="F7", name="deck.pdf", mimetype="application/pdf", size=2048)
    identity.set_base_url("https://example.lambda-url.us-east-1.on.aws/")

    result = df.download_file("F7")

    assert result["ok"] is True
    assert result["download_url"] == "https://example.lambda-url.us-east-1.on.aws/files/F7"
    assert "Authorization: Bearer $SLACK_MCP_TOKEN" in result["curl_command"]
    assert result["over_size_limit"] is False
    identity.set_base_url("")


def test_download_file_flags_oversize(table, monkeypatch):
    monkeypatch.setenv("MAX_FILE_DOWNLOAD_MB", "1")
    _seed_file(table, file_id="F8", name="big.zip", mimetype="application/zip", size=9 * 1048576)

    assert df.download_file("F8")["over_size_limit"] is True
