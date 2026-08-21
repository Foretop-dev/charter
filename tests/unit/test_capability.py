from charter.capability import CapabilityClass, Severity, classify_tool
from charter.enumerate import Tool


def make_tool(**overrides: object) -> Tool:
    defaults: dict[str, object] = {
        "server_name": "svc",
        "name": "some_tool",
        "description": None,
        "input_schema": None,
    }
    defaults.update(overrides)
    return Tool(**defaults)  # type: ignore[arg-type]


def test_read_file_is_classified_as_read() -> None:
    tool = make_tool(name="read_file", description="Read a file from disk")

    result = classify_tool(tool)

    assert CapabilityClass.READ in result.capabilities
    assert result.severity == Severity.LOW


def test_write_file_is_classified_as_write() -> None:
    tool = make_tool(name="write_file", description="Write a file to disk")

    result = classify_tool(tool)

    assert CapabilityClass.WRITE in result.capabilities
    assert result.severity == Severity.HIGH


def test_delete_is_classified_as_write() -> None:
    # specs/charter.md's own five categories have no separate "destructive write" tier —
    # delete/remove/update all fall under `write`.
    tool = make_tool(name="delete_record", description="Deletes a record from the database")

    result = classify_tool(tool)

    assert CapabilityClass.WRITE in result.capabilities


def test_execute_shell_command_is_code_execution_and_critical() -> None:
    tool = make_tool(name="execute_command", description="Execute a shell command")

    result = classify_tool(tool)

    assert CapabilityClass.CODE_EXECUTION in result.capabilities
    assert result.severity == Severity.CRITICAL


def test_a_token_shaped_schema_property_is_credential_access() -> None:
    tool = make_tool(
        name="connect",
        description="Connect to the service",
        input_schema={"type": "object", "properties": {"api_key": {"type": "string"}}},
    )

    result = classify_tool(tool)

    assert CapabilityClass.CREDENTIAL_ACCESS in result.capabilities
    assert result.severity == Severity.CRITICAL


def test_fetch_url_is_network_egress() -> None:
    tool = make_tool(
        name="fetch_url",
        description="Fetch a URL from the internet",
        input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
    )

    result = classify_tool(tool)

    assert CapabilityClass.NETWORK_EGRESS in result.capabilities
    assert result.severity == Severity.MEDIUM


def test_web_search_matches_both_read_and_network_egress() -> None:
    # A tool can legitimately carry more than one capability — under-reporting this would
    # hide a real one. web_search both returns information (read) and makes an outbound call
    # (network_egress).
    tool = make_tool(name="web_search", description="Search the web for information")

    result = classify_tool(tool)

    assert CapabilityClass.READ in result.capabilities
    assert CapabilityClass.NETWORK_EGRESS in result.capabilities


def test_overall_severity_is_the_maximum_across_matched_capabilities() -> None:
    # Matches both `read` (LOW) and `credential_access` (CRITICAL) via description keywords —
    # the tool's overall severity must be the worst one, not the first or an average.
    tool = make_tool(
        name="get_secret",
        description="Retrieve a stored secret",
        input_schema={"type": "object", "properties": {"secret": {"type": "string"}}},
    )

    result = classify_tool(tool)

    assert result.severity == Severity.CRITICAL


def test_an_unrecognized_tool_is_classified_unknown_with_medium_severity() -> None:
    tool = make_tool(name="frobnicate", description="Does something entirely unrecognizable")

    result = classify_tool(tool)

    assert result.capabilities == frozenset()
    assert result.severity == Severity.MEDIUM


def test_a_tool_with_no_description_and_no_schema_still_classifies_on_name_alone() -> None:
    tool = make_tool(name="write_file", description=None, input_schema=None)

    result = classify_tool(tool)

    assert CapabilityClass.WRITE in result.capabilities


def test_classification_carries_the_tool_and_server_name() -> None:
    tool = make_tool(server_name="airtable", name="write_file")

    result = classify_tool(tool)

    assert result.server_name == "airtable"
    assert result.tool_name == "write_file"


def test_classification_carries_a_real_rule_version() -> None:
    result = classify_tool(make_tool())

    assert result.rule_version >= 1


def test_matching_is_case_insensitive() -> None:
    tool = make_tool(name="WRITE_FILE", description="WRITES A FILE")

    result = classify_tool(tool)

    assert CapabilityClass.WRITE in result.capabilities
