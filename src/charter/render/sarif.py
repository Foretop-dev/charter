from charter import __version__ as ENGINE_VERSION  # noqa: N812
from charter.capability import CapabilityClass, Severity, classify_tool
from charter.enumerate import EnumerationResult
from charter.models import Server, ServerSet

SCHEMA_URI = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
)

# SARIF 2.1.0 has 3 real result levels (note/warning/error, plus "none" for suppressed results —
# definitions.result.properties.level.enum, verified against the real schema same as ebb's own
# sarif.py). Charter's Severity has 4. HIGH and CRITICAL both collapse to "error" — mirrors
# ebb's own collapse of its top severities, and specs/charter.md's own accuracy strategy: "a
# misclassified-as-dangerous tool is annoying, the reverse is a breach."
_SEVERITY_TO_SARIF_LEVEL: dict[Severity, str] = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
}

_UNKNOWN_RULE_ID = "unknown"

# Short, human descriptions for each rule id — specs/charter.md §2.2's five categories plus
# DEC-03's honest "unknown" verdict. Kept here rather than pulled from capability_taxonomy.yaml
# because these describe the *capability*, not a specific matching rule within it.
_RULE_DESCRIPTIONS: dict[str, str] = {
    CapabilityClass.READ.value: "Tool can read or retrieve information.",
    CapabilityClass.WRITE.value: "Tool can create, modify, or delete data.",
    CapabilityClass.NETWORK_EGRESS.value: "Tool can make outbound network requests.",
    CapabilityClass.CODE_EXECUTION.value: "Tool can execute code or shell commands.",
    CapabilityClass.CREDENTIAL_ACCESS.value: "Tool can access credentials or secrets.",
    _UNKNOWN_RULE_ID: "Tool did not match any known capability pattern (DEC-03: a real, "
    "honest verdict, not guessed at).",
}


def render_sarif(
    servers: ServerSet, enumeration: dict[Server, EnumerationResult]
) -> dict[str, object]:
    """Returns the SARIF log as a dict (apps/ebb/src/ebb/render/sarif.py's same convention —
    tests validate structure directly, json.dumps is the caller's job). One result per (server,
    tool, matched capability): a tool matching more than one capability produces multiple
    results rather than one merged one, same "don't under-report" stance capability.py's own
    docstring takes for `Classification.capabilities`. A tool's location is its *server's*
    config source_file/source_line — enumerated tools have no line of their own, they come from
    a live tools/list call, not static text. Only servers `enumeration` actually covers produce
    results; a server that was never --enumerate'd contributes none (not "0 findings" — no
    evidence was ever gathered, the same distinction charter.lock's `tools: null` makes).
    """
    rules = [
        {"id": rule_id, "name": rule_id, "shortDescription": {"text": description}}
        for rule_id, description in sorted(_RULE_DESCRIPTIONS.items())
    ]

    results: list[dict[str, object]] = []
    for server in sorted(servers.servers, key=lambda s: (s.name, str(s.source_file))):
        result = enumeration.get(server)
        if result is None or result.error is not None:
            continue
        for tool in sorted(result.tools, key=lambda t: t.name):
            classification = classify_tool(tool)
            rule_ids = sorted(c.value for c in classification.capabilities) or [_UNKNOWN_RULE_ID]
            location = {
                "physicalLocation": {
                    "artifactLocation": {"uri": str(server.source_file)},
                    "region": {"startLine": server.source_line},
                }
            }
            for rule_id in rule_ids:
                results.append(
                    {
                        "ruleId": rule_id,
                        "level": _SEVERITY_TO_SARIF_LEVEL[classification.severity],
                        "message": {
                            "text": f"{server.name}: tool {tool.name!r} ({rule_id}, "
                            f"{classification.severity.value})"
                        },
                        "locations": [location],
                    }
                )

    return {
        "$schema": SCHEMA_URI,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "charter", "version": ENGINE_VERSION, "rules": rules}},
                "results": results,
            }
        ],
    }
