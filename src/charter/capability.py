import re
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from typing import Any

import yaml

from charter.enumerate import Tool
from charter.models import Server


class CapabilityClass(StrEnum):
    """specs/charter.md §2.2: "Each tool is classified: read / write / network egress / code
    execution / credential access." Exactly these five — not a place to add a sixth without a
    spec change."""

    READ = "read"
    WRITE = "write"
    NETWORK_EGRESS = "network_egress"
    CODE_EXECUTION = "code_execution"
    CREDENTIAL_ACCESS = "credential_access"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_SEVERITY_ORDER = (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)

# specs/charter.md's own accuracy strategy: "Capability classification errs toward higher
# severity; a misclassified-as-dangerous tool is annoying, the reverse is a breach." Credential
# access and code execution are the two categories where a false negative would be a real
# security failure, not an annoyance — both CRITICAL. Write is HIGH (can destroy or corrupt
# data, but is at least visible/auditable after the fact, unlike a leaked credential). Network
# egress is MEDIUM (data can leave the system, real but usually less immediately destructive).
# Read is LOW (information disclosure risk exists but is bounded).
_CAPABILITY_SEVERITY: dict[CapabilityClass, Severity] = {
    CapabilityClass.CREDENTIAL_ACCESS: Severity.CRITICAL,
    CapabilityClass.CODE_EXECUTION: Severity.CRITICAL,
    CapabilityClass.WRITE: Severity.HIGH,
    CapabilityClass.NETWORK_EGRESS: Severity.MEDIUM,
    CapabilityClass.READ: Severity.LOW,
}


def severity_for_capability(capability: CapabilityClass) -> Severity:
    """Public accessor for one capability's own fixed severity — added for findings.py
    (Session 27), which needs per-capability precision (a tool matching both READ and
    CREDENTIAL_ACCESS becomes two Finding rows, one LOW and one CRITICAL) rather than
    classify_tool's own tool-level max. Reads the same table classify_tool uses internally,
    kept module-private, so the two never drift apart."""
    return _CAPABILITY_SEVERITY[capability]


# DEC-03: "An unrecognized tool is classified unknown — a real, honest verdict, not guessed
# at." Not LOW: an unrecognized tool could be anything, including something genuinely
# dangerous, and calling it "safe" because no rule happened to match would be exactly the false
# `present`-adjacent overclaim this whole suite's design principle warns against. Not CRITICAL
# either — that would make every unrecognized tool as alarming as a confirmed credential leak,
# which isn't honest in the other direction. MEDIUM is the deliberate middle.
_UNKNOWN_SEVERITY = Severity.MEDIUM


@dataclass(frozen=True, slots=True)
class Classification:
    server_name: str
    tool_name: str
    capabilities: frozenset[CapabilityClass]
    severity: Severity
    rule_version: int


@dataclass(frozen=True, slots=True)
class _Rule:
    capability: CapabilityClass
    name_patterns: tuple[str, ...]
    description_keywords: tuple[str, ...]
    schema_property_names: tuple[str, ...]


def _load_rules() -> tuple[int, tuple[_Rule, ...]]:
    text = resources.files("charter").joinpath("rules", "capability_taxonomy.yaml").read_text()
    data = yaml.safe_load(text)
    version = data.get("version", 1)
    rules = tuple(
        _Rule(
            capability=CapabilityClass(entry["capability"]),
            name_patterns=tuple(entry.get("name_patterns", [])),
            description_keywords=tuple(entry.get("description_keywords", [])),
            schema_property_names=tuple(entry.get("schema_property_names", [])),
        )
        for entry in data.get("rules", [])
    )
    return version, rules


_RULE_VERSION, _RULES = _load_rules()


def _schema_property_names(schema: dict[str, Any] | None) -> frozenset[str]:
    if not schema:
        return frozenset()
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return frozenset()
    return frozenset(k.lower() for k in properties if isinstance(k, str))


def _rule_matches(rule: _Rule, name: str, description: str, schema_props: frozenset[str]) -> bool:
    if any(pattern in name for pattern in rule.name_patterns):
        return True
    if description and any(keyword in description for keyword in rule.description_keywords):
        return True
    return bool(schema_props) and any(prop in schema_props for prop in rule.schema_property_names)


def classify_tool(tool: Tool) -> Classification:
    """Pure: Tool -> Classification. No I/O, no network — every rule in the loaded taxonomy is
    checked independently (not first-match-wins), so a tool can carry more than one capability.
    `capabilities` is empty exactly when DEC-03's "unknown" applies: no rule matched anything
    about this tool's name, description, or input schema property names."""
    name = tool.name.lower()
    description = (tool.description or "").lower()
    schema_props = _schema_property_names(tool.input_schema)

    matched = frozenset(
        rule.capability for rule in _RULES if _rule_matches(rule, name, description, schema_props)
    )

    severity = (
        max((_CAPABILITY_SEVERITY[c] for c in matched), key=_SEVERITY_ORDER.index)
        if matched
        else _UNKNOWN_SEVERITY
    )

    return Classification(
        server_name=tool.server_name,
        tool_name=tool.name,
        capabilities=matched,
        severity=severity,
        rule_version=_RULE_VERSION,
    )


# ---------------------------------------------------------------- static config signal (R18)

# Names that make an environment variable a credential rather than ordinary configuration.
# Deliberately the same vocabulary the credential_access rule in rules/capability_taxonomy.yaml
# already uses for tool names and schema properties, so one idea has one definition.
_CREDENTIAL_NAME_PARTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "auth",
)

# A URI carrying userinfo with a password: scheme://user:secret@host. This is the shape a
# committed database DSN takes, and it is the reason this check exists — a connection string in
# a positional argument is a credential sitting in the repository.
_CREDENTIAL_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://[^/\s:@]+:[^/\s@]+@")


def declared_credential_env_vars(server: Server) -> tuple[str, ...]:
    """Environment variable *names* the config wires into this server that read as credentials.

    Names only — `Server.env_var_names` never holds a value (DEC-06), so there is nothing here
    to leak. A server declaring `GITHUB_PERSONAL_ACCESS_TOKEN` is declaring credential access in
    the committed file, which is a static fact rather than an inference about what it might do.
    """
    return tuple(
        name
        for name in server.env_var_names
        if any(part in name.lower() for part in _CREDENTIAL_NAME_PARTS)
    )


def has_credential_bearing_argument(server: Server) -> bool:
    """Whether any launch argument embeds a credential, without revealing which or what.

    Returns a bool on purpose. `Server.args` is held in memory only so real enumeration can
    build a launch vector, and lock schema v4 records `arg_count` rather than argument text
    precisely so a password in a positional argument cannot reach charter.lock, rendered output
    or hosted reporting (DEC-06). Reporting *that* one exists is the finding; reproducing it
    would be the leak this design exists to prevent.
    """
    return any(_CREDENTIAL_URI_RE.match(arg) for arg in server.args)
