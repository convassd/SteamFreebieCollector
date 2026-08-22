import pytest

from steam_freebie_collector.models import Availability
from steam_freebie_collector.parsing import (
    ParseError,
    canonicalize_thread_url,
    classify_availability,
    is_canonical_command,
    parse_add_license_command,
    parse_detail,
    parse_index,
)


INDEX_URL = "https://keylol.com/t572814-1-1"


def test_current_fixture_selects_only_steam(fixture_text):
    entries = parse_index(fixture_text("current_index.html"), INDEX_URL)
    assert len(entries) == 1
    assert entries[0].thread_id == 1045840
    assert entries[0].title == "《夜勤人》"
    assert entries[0].availability is Availability.CURRENT


def test_index_canonicalizes_deduplicates_and_classifies(fixture_text):
    entries = parse_index(fixture_text("edge_index.html"), INDEX_URL)
    assert [entry.thread_id for entry in entries] == [111, 222, 333]
    assert entries[0].url == "https://keylol.com/t111-1-1"
    assert entries[0].availability is Availability.CURRENT
    assert entries[1].availability is Availability.FUTURE
    assert entries[2].availability is Availability.AMBIGUOUS


def test_missing_required_index_structure_fails_closed(fixture_text):
    with pytest.raises(ParseError):
        parse_index(fixture_text("malformed_index.html"), INDEX_URL)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("【现已可领取】", Availability.CURRENT),
        ("現在可領取", Availability.CURRENT),
        ("今日可限時免費領取", Availability.CURRENT),
        ("现已可领取，但尚未开放", Availability.FUTURE),
        ("限时福利", Availability.AMBIGUOUS),
    ],
)
def test_availability_rules(text, expected):
    assert classify_availability(text) is expected


def test_thread_url_requires_https_and_keylol():
    assert canonicalize_thread_url("/t123-2-9?x=1", INDEX_URL) == (123, "https://keylol.com/t123-1-1")
    assert canonicalize_thread_url("http://keylol.com/t123-1-1", INDEX_URL) is None
    assert canonicalize_thread_url("https://example.com/t123-1-1", INDEX_URL) is None
    assert canonicalize_thread_url("https://keylol.com/forum.php?mod=viewthread&tid=123", INDEX_URL) is None


def test_detail_prefers_authored_code_and_ignores_widget(fixture_text):
    result = parse_detail(fixture_text("current_detail.html"), "https://keylol.com/t1045840-1-1", 1045840)
    assert [item.normalized_command for item in result.licenses] == ["!ALA s/1706211"]
    assert [item.provenance for item in result.licenses] == ["authored_blockcode"]
    assert not result.issues


def test_detail_accepts_only_allowlisted_full_lines_and_deduplicates(fixture_text):
    result = parse_detail(fixture_text("edge_detail.html"), "https://keylol.com/t1-1-1", 1)
    assert [item.normalized_command for item in result.licenses] == ["!ALA a/12", "!ALA s/34", "!ALA a/56"]
    rejected = [issue.raw_value for issue in result.issues if issue.code == "unsupported_command"]
    assert "!addlicense SomeBot s/99" in rejected
    assert "!exit" in rejected
    assert "!ALA s/78; !update" in rejected


def test_detail_accepts_real_world_mixed_typed_and_bare_line(fixture_text):
    result = parse_detail(fixture_text("bare_id_detail.html"), "https://keylol.com/t1047211-1-1", 1047211)
    assert [item.identifier.normalized for item in result.licenses] == ["sub/1613629", "sub/1741253"]
    assert [item.normalized_command for item in result.licenses] == ["!ALA s/1613629", "!ALA s/1741253"]
    assert not [issue for issue in result.issues if issue.code in {"unsupported_command", "missing_supported_command"}]


@pytest.mark.parametrize(
    ("line", "expected_identifiers", "expected_commands"),
    [
        ("!ALA s/1613629, 1741253", ["sub/1613629", "sub/1741253"], ["!ALA s/1613629", "!ALA s/1741253"]),
        ("!ALA 1741253", ["sub/1741253"], ["!ALA s/1741253"]),
        ("!ALA a/123, 456", ["app/123", "sub/456"], ["!ALA a/123", "!ALA s/456"]),
    ],
)
def test_command_parser_accepts_asf_bare_sub_syntax(line, expected_identifiers, expected_commands):
    identifiers = parse_add_license_command(line)
    assert identifiers is not None
    assert [identifier.normalized for identifier in identifiers] == expected_identifiers
    assert [f"!ALA {identifier.asf_token}" for identifier in identifiers] == expected_commands


def test_typed_and_bare_sub_representations_are_semantically_deduplicated():
    identifiers = parse_add_license_command("!ALA s/1741253, 1741253 sub/1741253")
    assert identifiers is not None
    assert [identifier.normalized for identifier in identifiers] == ["sub/1741253"]


def test_direct_literal_widget_is_used_when_authored_command_is_missing(fixture_text):
    result = parse_detail(fixture_text("missing_detail.html"), "https://keylol.com/t3-1-1", 3)
    assert [item.normalized_command for item in result.licenses] == ["!ALA a/123"]
    assert [item.provenance for item in result.licenses] == ["widget_copy_fallback"]
    assert result.licenses[0].raw_command == "widget_copy_fallback app/123"
    assert "setCopy" not in result.licenses[0].raw_command
    assert not result.issues


def test_concatenated_href_widget_is_used_as_fallback(fixture_text):
    result = parse_detail(fixture_text("widget_concatenated_detail.html"), "https://keylol.com/t4-1-1", 4)
    assert [item.normalized_command for item in result.licenses] == ["!ALA a/606150"]
    assert [item.provenance for item in result.licenses] == ["widget_copy_fallback"]
    assert not result.issues


def test_missing_authored_command_and_widget_is_flagged(fixture_text):
    result = parse_detail(fixture_text("no_supported_detail.html"), "https://keylol.com/t5-1-1", 5)
    assert not result.licenses
    assert [issue.code for issue in result.issues] == ["missing_supported_command"]


def _detail_with_anchor(anchor: str, *, authored: str = "", outside: bool = False) -> str:
    first_post_content = f'<div class="blockcode"><ol><li>{authored}</li></ol></div>' if authored else "<p>No command.</p>"
    if not outside:
        first_post_content += anchor
        anchor = ""
    return (
        '<!doctype html><html><body><table>'
        f'<tr><td id="postmessage_10">{first_post_content}</td></tr>'
        f'<tr><td id="postmessage_11">{anchor}</td></tr>'
        "</table></body></html>"
    )


@pytest.mark.parametrize(
    "anchor",
    [
        '<a href="#asf123" onclick="setCopy(\'!addlicense asf a/456\')">复制ASF代码</a>',
        '<a href="#asf0" onclick="setCopy(\'!addlicense asf a/0\')">复制ASF代码</a>',
        '<a href="#asf-1" onclick="setCopy(\'!addlicense asf a/-1\')">复制ASF代码</a>',
        '<a href="#asfabc" onclick="setCopy(\'!addlicense asf a/123\')">复制ASF代码</a>',
        '<a href="#asf123" onclick="setCopy(\'!exit\')">复制ASF代码</a>',
        '<a href="#asf123" onclick="setCopy(\'!addlicense asf a/123; !exit\')">复制ASF代码</a>',
        '<a href="#asf123" onclick="setCopy(\'!addlicense asf a/123\'">复制ASF代码</a>',
        '<a href="https://example.com/#asf123" onclick="setCopy(\'!addlicense asf a/123\')">复制ASF代码</a>',
    ],
)
def test_malformed_or_unrelated_widgets_are_rejected(anchor):
    result = parse_detail(_detail_with_anchor(anchor), "https://keylol.com/t10-1-1", 10)
    assert not result.licenses
    assert [issue.code for issue in result.issues] == ["missing_supported_command"]


def test_widget_like_element_outside_original_post_is_ignored():
    anchor = '<a href="#asf123" onclick="setCopy(\'!addlicense asf a/123\')">复制ASF代码</a>'
    result = parse_detail(_detail_with_anchor(anchor, outside=True), "https://keylol.com/t10-1-1", 10)
    assert not result.licenses
    assert [issue.code for issue in result.issues] == ["missing_supported_command"]


def test_traditional_widget_label_is_accepted():
    anchor = '<a href="#asf123" onclick="setCopy(\'!addlicense asf a/123\')">複製ASF代碼</a>'
    result = parse_detail(_detail_with_anchor(anchor), "https://keylol.com/t10-1-1", 10)
    assert [item.normalized_command for item in result.licenses] == ["!ALA a/123"]


def test_widget_fallback_deduplicates_and_preserves_authored_issues():
    anchors = (
        '<a href="#asf123" onclick="setCopy(\'!addlicense asf a/123\')">复制ASF代码</a>'
        '<a href="#asf123" onclick="javascript:setCopy(\'!addlicense asf a/\'+this.href.split(\'#asf\')[1]);return false;">复制ASF代码</a>'
    )
    result = parse_detail(_detail_with_anchor(anchors, authored="!exit"), "https://keylol.com/t10-1-1", 10)
    assert [item.normalized_command for item in result.licenses] == ["!ALA a/123"]
    assert [issue.code for issue in result.issues] == ["unsupported_command"]


@pytest.mark.parametrize(
    "line",
    [
        "!ALA s/1; !exit",
        "!addlicense OtherBot s/1",
        "!update",
        "!ALA s/0",
        "!ALA s/-1",
        "!ALA s/1 extra",
        "!ALA s/1, invalid",
        "!ALA 123, --option",
        "!ALA a/123, 456; !exit",
    ],
)
def test_command_parser_rejects_unsafe_or_ambiguous_lines(line):
    assert parse_add_license_command(line) is None


def test_canonical_command_guard():
    assert is_canonical_command("!ALA a/123")
    assert is_canonical_command("!ALA s/456")
    assert not is_canonical_command("!ALA app/123")
    assert not is_canonical_command("!EXIT")
    assert not is_canonical_command("!ALA s/1 s/2")
