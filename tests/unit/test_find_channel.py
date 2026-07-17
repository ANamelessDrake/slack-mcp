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
