from charter.drift import Drift, DriftKind
from charter.render.annotations import render_annotations


def test_no_drift_renders_nothing() -> None:
    assert render_annotations(()) == ""


def test_a_new_server_annotation() -> None:
    drift = (
        Drift(
            server_name="evil-svc",
            source_file=".mcp.json",
            source_line=5,
            tool_name=None,
            kind=DriftKind.NEW_SERVER,
            before_severity=None,
            after_severity=None,
        ),
    )

    output = render_annotations(drift)

    assert output.startswith("::error file=.mcp.json,line=5,title=")
    assert "evil-svc" in output
    assert output.endswith("\n")


def test_a_new_tool_annotation_includes_the_severity() -> None:
    drift = (
        Drift(
            server_name="svc",
            source_file=".mcp.json",
            source_line=3,
            tool_name="write_file",
            kind=DriftKind.NEW_TOOL,
            before_severity=None,
            after_severity="high",
        ),
    )

    output = render_annotations(drift)

    assert "write_file" in output
    assert "high" in output


def test_a_severity_increase_annotation_shows_before_and_after() -> None:
    drift = (
        Drift(
            server_name="svc",
            source_file=".mcp.json",
            source_line=3,
            tool_name="t",
            kind=DriftKind.SEVERITY_INCREASED,
            before_severity="low",
            after_severity="critical",
        ),
    )

    output = render_annotations(drift)

    assert "low" in output
    assert "critical" in output


def test_a_comma_in_the_source_file_is_escaped_in_the_property_list() -> None:
    # file= is a property, not the message body — a literal "," would otherwise be
    # misread as a property separator by GitHub's own workflow-command parser.
    drift = (
        Drift(
            server_name="svc",
            source_file="weird,path/.mcp.json",
            source_line=1,
            tool_name=None,
            kind=DriftKind.NEW_SERVER,
            before_severity=None,
            after_severity=None,
        ),
    )

    output = render_annotations(drift)

    assert "file=weird%2Cpath/.mcp.json" in output


def test_multiple_drifts_produce_multiple_lines() -> None:
    drift = (
        Drift("a", ".mcp.json", 1, None, DriftKind.NEW_SERVER, None, None),
        Drift("b", ".mcp.json", 2, "t", DriftKind.NEW_TOOL, None, "low"),
    )

    output = render_annotations(drift)

    assert len(output.strip().splitlines()) == 2
