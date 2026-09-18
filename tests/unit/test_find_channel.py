import tools.find_channel as fc


class FakeRelay:
    def __init__(self, pages):
        self._pages = pages
        self._i = 0

    def conversations_list(self, **kwargs):
        page = self._pages[self._i]
        self._i += 1
        return page


def _page(channels, cursor=""):
    return {"channels": channels, "response_metadata": {"next_cursor": cursor}}


def test_matches_substring_case_insensitive(monkeypatch):
    relay = FakeRelay(
        [
            _page(
                [
                    {"id": "C1", "name": "paymentproducts", "is_member": True, "is_private": True},
                    {"id": "C2", "name": "general", "is_member": False},
                    {"id": "C3", "name": "payments-ops", "is_member": True},
                ]
            )
        ]
    )
    monkeypatch.setattr(fc, "relay_client", lambda: relay)

    result = fc.find_channel("Payment")

    assert result["ok"] is True
    assert [c["id"] for c in result["channels"]] == ["C1", "C3"]
    assert result["channels"][0]["is_private"] is True


def test_exact_match_sorts_first(monkeypatch):
    relay = FakeRelay(
        [
            _page(
                [
                    {"id": "C1", "name": "general-chat", "is_member": True},
                    {"id": "C2", "name": "general", "is_member": True},
                ]
            )
        ]
    )
    monkeypatch.setattr(fc, "relay_client", lambda: relay)

    result = fc.find_channel("#general")  # leading # tolerated

    assert [c["id"] for c in result["channels"]] == ["C2", "C1"]


def test_paginates_until_enough(monkeypatch):
    relay = FakeRelay(
        [
            _page([{"id": "C1", "name": "team-a", "is_member": True}], cursor="next"),
            _page([{"id": "C2", "name": "team-b", "is_member": True}]),
        ]
    )
    monkeypatch.setattr(fc, "relay_client", lambda: relay)

    result = fc.find_channel("team")

    assert sorted(c["id"] for c in result["channels"]) == ["C1", "C2"]


def test_empty_name_rejected():
    assert fc.find_channel("   ")["ok"] is False


def test_finds_group_dm_by_name_and_marks_it(monkeypatch):
    monkeypatch.setattr(fc, "relay_client", lambda: FakeRelay([_page([])]))
    monkeypatch.setattr(fc, "current_agent_id", lambda: "wilma")
    monkeypatch.setattr(
        fc,
        "list_dm_conversations",
        lambda _: [
            {"id": "C0GRP", "is_group": True, "name": "JustWILMA2", "user": ""},
            {"id": "D1", "is_group": False, "name": "", "user": "U1"},
        ],
    )
    registered = []
    monkeypatch.setattr(fc, "register_channel", lambda ch, t="": registered.append((ch, t)))

    result = fc.find_channel("justwilma")

    assert result["ok"] is True
    match = next(c for c in result["channels"] if c["id"] == "C0GRP")
    assert match["name"] == "JustWILMA2"
    assert match["is_group_chat"] is True
    # Marked mpim so a later check_messages pulls it
    assert ("C0GRP", "mpim") in registered


def test_group_dm_lookup_failure_never_breaks_channel_search(monkeypatch):
    monkeypatch.setattr(
        fc, "relay_client", lambda: FakeRelay([_page([{"id": "C1", "name": "general"}])])
    )
    monkeypatch.setattr(fc, "current_agent_id", lambda: "wilma")

    def boom(_):
        raise RuntimeError("no agent token")

    monkeypatch.setattr(fc, "list_dm_conversations", boom)

    result = fc.find_channel("general")

    assert result["ok"] is True
    assert [c["id"] for c in result["channels"]] == ["C1"]
