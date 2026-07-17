import tools.canvas_create as cc
import tools.canvas_edit as ce
import tools.read_canvas as rc
from sharedModules import canvas

CANVAS_HTML = (
    '<div class="quip-canvas-content">'
    '<h1 id="s1">Status</h1>'
    '<p id="s2" class="line">Build: pending</p>'
    '<h1 id="s3">Notes</h1>'
    '<p id="s4" class="line">none yet</p>'
    "</div>"
)


def test_parse_canvas_extracts_sections_and_markdown():
    data = canvas.parse_canvas(CANVAS_HTML)
    assert data["sections"] == [
        {"section_id": "s1", "type": "h1", "text": "Status"},
        {"section_id": "s2", "type": "p", "text": "Build: pending"},
        {"section_id": "s3", "type": "h1", "text": "Notes"},
        {"section_id": "s4", "type": "p", "text": "none yet"},
    ]
    assert data["markdown"] == "# Status\n\nBuild: pending\n\n# Notes\n\nnone yet"


ORDERED_HTML = (
    '<div class="quip-canvas-content">'
    '<h2 id="h">Steps</h2>'
    "<div data-section-style='6' class=\"list-numbering-restart-at\">"
    "<ul id='u1'>"
    "<li id='o1' value='1'><span id='o1'>first</span><br/></li>"
    "<li id='o2'><span id='o2'>second</span><br/></li>"
    "<li id='o3'><span id='o3'>third</span><br/></li>"
    "</ul></div></div>"
)

NESTED_HTML = (
    '<div class="quip-canvas-content">'
    "<div data-section-style='5' class=\"list-numbering-restart-at\">"
    "<ul id='u1'>"
    "<li id='a'><span id='a'>alpha</span><br/></li>"
    "<li id='b'><span id='b'>beta</span><br/></li>"
    "<ul>"
    "<li id='n1'><span id='n1'>nested one</span><br/></li>"
    "<li id='n2'><span id='n2'>nested two</span><br/></li>"
    "</ul></ul></div></div>"
)


def test_parse_ordered_list_numbers_items():
    data = canvas.parse_canvas(ORDERED_HTML)
    assert data["markdown"] == "## Steps\n\n1. first\n2. second\n3. third"
    assert [s["section_id"] for s in data["sections"]] == ["h", "o1", "o2", "o3"]


def test_parse_nested_bulleted_list_indents():
    data = canvas.parse_canvas(NESTED_HTML)
    assert data["markdown"] == "- alpha\n- beta\n  - nested one\n  - nested two"


def test_two_adjacent_lists_stay_separate():
    # An ordered list immediately followed by a bulleted list: distinct sections
    html = (
        '<div class="quip-canvas-content">'
        "<div data-section-style='6'><ul id='u1'>"
        "<li id='o1'><span id='o1'>step one</span></li></ul></div>"
        "<div data-section-style='5'><ul id='u2'>"
        "<li id='b1'><span id='b1'>bullet one</span></li></ul></div></div>"
    )
    data = canvas.parse_canvas(html)
    assert data["markdown"] == "1. step one\n\n- bullet one"


def test_parse_handles_lists_and_quotes():
    html = (
        '<div class="quip-canvas-content">'
        "<h2 id=\"a\">Todo</h2>"
        "<div data-section-style='5'><ul id='u'>"
        "<li id=\"b\"><span id='b'>first</span></li>"
        "<li id=\"c\"><span id='c'>second</span></li></ul></div>"
        '<blockquote id="d">note</blockquote></div>'
    )
    data = canvas.parse_canvas(html)
    assert data["markdown"] == "## Todo\n\n- first\n- second\n\n> note"


def test_read_canvas_surfaces_sections(monkeypatch):
    monkeypatch.setattr(rc, "channel_canvas_id", lambda ch: "FCANVAS")
    monkeypatch.setattr(rc, "fetch_canvas", lambda ch, cid: canvas.parse_canvas(CANVAS_HTML))

    result = rc.read_canvas("C1")

    assert result["ok"] is True
    assert result["canvas_id"] == "FCANVAS"
    assert [s["section_id"] for s in result["sections"]] == ["s1", "s2", "s3", "s4"]


def test_read_canvas_when_none_exists(monkeypatch):
    monkeypatch.setattr(rc, "channel_canvas_id", lambda ch: None)
    result = rc.read_canvas("C1")
    assert result["ok"] is False
    assert "no canvas yet" in result["error"]


def test_create_refuses_when_canvas_exists(monkeypatch):
    monkeypatch.setattr(cc, "channel_canvas_id", lambda ch: "FEXISTS")
    created = []
    monkeypatch.setattr(cc, "create_channel_canvas", lambda ch, md: created.append(md))

    result = cc.canvas_create("C1", "# hi")

    assert result["ok"] is False
    assert result["canvas_id"] == "FEXISTS"
    assert created == []  # never attempted a duplicate


def test_create_when_absent(monkeypatch):
    monkeypatch.setattr(cc, "channel_canvas_id", lambda ch: None)
    monkeypatch.setattr(cc, "create_channel_canvas", lambda ch, md: "FNEW")

    result = cc.canvas_create("C1", "# Status\npending")

    assert result == {"ok": True, "canvas_id": "FNEW"}


def _capture_edit(monkeypatch):
    monkeypatch.setattr(ce, "channel_canvas_id", lambda ch: "FCANVAS")
    changes = {}
    monkeypatch.setattr(ce, "apply_changes", lambda cid, ch: changes.setdefault("v", (cid, ch)))
    return changes


def test_append_builds_insert_at_end(monkeypatch):
    changes = _capture_edit(monkeypatch)
    result = ce.canvas_edit("C1", "append", markdown="new line")
    assert result["ok"] is True
    cid, ch = changes["v"]
    assert cid == "FCANVAS"
    assert ch == [
        {
            "operation": "insert_at_end",
            "document_content": {"type": "markdown", "markdown": "new line"},
        }
    ]


def test_replace_by_find_text_resolves_section(monkeypatch):
    changes = _capture_edit(monkeypatch)
    monkeypatch.setattr(ce, "find_section_id", lambda ch, cid, text: "s2")

    result = ce.canvas_edit("C1", "replace", markdown="Build: done", find_text="Build:")

    assert result["ok"] is True
    _, ch = changes["v"]
    assert ch == [
        {
            "operation": "replace",
            "section_id": "s2",
            "document_content": {"type": "markdown", "markdown": "Build: done"},
        }
    ]


def test_delete_needs_no_document_content(monkeypatch):
    changes = _capture_edit(monkeypatch)
    result = ce.canvas_edit("C1", "delete", section_id="s3")
    assert result["ok"] is True
    _, ch = changes["v"]
    assert ch == [{"operation": "delete", "section_id": "s3"}]


def test_positional_op_without_target_is_rejected(monkeypatch):
    _capture_edit(monkeypatch)
    result = ce.canvas_edit("C1", "replace", markdown="x")
    assert result["ok"] is False
    assert "section_id or find_text" in result["error"]


def test_unknown_operation_is_rejected(monkeypatch):
    _capture_edit(monkeypatch)
    result = ce.canvas_edit("C1", "obliterate", markdown="x")
    assert result["ok"] is False
    assert "operation must be one of" in result["error"]


def test_edit_without_canvas_is_rejected(monkeypatch):
    monkeypatch.setattr(ce, "channel_canvas_id", lambda ch: None)
    result = ce.canvas_edit("C1", "append", markdown="x")
    assert result["ok"] is False
    assert "no canvas yet" in result["error"]


def test_find_section_id_matches_substring(monkeypatch):
    monkeypatch.setattr(canvas, "fetch_canvas", lambda ch, cid: canvas.parse_canvas(CANVAS_HTML))
    assert canvas.find_section_id("C1", "FCANVAS", "none yet") == "s4"
    assert canvas.find_section_id("C1", "FCANVAS", "nonexistent") == ""
