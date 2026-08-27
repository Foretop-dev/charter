from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator


class ProductCode(StrEnum):
    """The five products, per SUITE_ARCHITECTURE.md §1.

    This enum listed all five from day one while living in apps/ebb, with a note saying the
    Finding schema was "shared design (not shared code yet)". Session 26 made it shared code:
    the whole module moved here verbatim, because every paid feature on the pricing page
    (org-wide inventory, one weekly digest across every check, history and trend, dated
    evidence exports) is a query *across* products, and that is impossible until all five
    write rows of the same shape. apps/ebb/src/ebb/finding.py is now a re-export shim."""

    EBB = "ebb"
    TELLTALE = "telltale"
    LADING = "lading"
    CHARTER = "charter"
    UNDERTOW = "undertow"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Confidence(StrEnum):
    CERTAIN = "certain"
    PROBABLE = "probable"
    AMBIGUOUS = "ambiguous"


class Verdict(StrEnum):
    """What THIS run concluded. `UNKNOWN` is a first-class member, not the absence of one —
    SUITE_ARCHITECTURE.md §3: a collector failure or an unresolvable subject is `unknown`,
    never `clear`. A tool that fails quiet is worse than no tool."""

    CLEAR = "clear"
    DRIFT = "drift"
    BREAK = "break"
    UNKNOWN = "unknown"


class FindingState(StrEnum):
    """Lifecycle vs. a baseline from a previous run. Separate axis from Verdict — a finding can
    be `unknown` and `persisting` at the same time. No baseline mechanism exists yet for ebb's
    stateless local CLI (that's the hosted tier, Slice 4 / much later); every finding from a
    single `ebb scan` invocation is `NEW` for now — see build_findings.py."""

    NEW = "new"
    PERSISTING = "persisting"
    RESOLVED = "resolved"
    ACCEPTED = "accepted"


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_uri: str
    locator: str | None = None
    excerpt: str | None = None
    retrieved_at: datetime
    content_hash: str

    @field_validator("source_uri")
    @classmethod
    def _source_uri_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_uri must not be blank")
        return value


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: str
    product: ProductCode
    rule_id: str
    subject: str
    severity: Severity
    confidence: Confidence
    title: str
    detail_md: str
    evidence: list[Evidence]
    owner: str | None = None
    deadline: date | None = None
    verdict: Verdict
    state: FindingState
    first_seen_run: str
    rule_version: str
    engine_version: str

    @field_validator("evidence")
    @classmethod
    def _evidence_never_empty(cls, value: list[Evidence]) -> list[Evidence]:
        if not value:
            raise ValueError(
                "evidence must never be empty — no evidence, no finding (SUITE_ARCHITECTURE.md §3)"
            )
        return value
