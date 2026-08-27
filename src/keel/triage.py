"""The shared triage shape every product's own `<product>/triage.py` produces.

This is the fix for "1,571 findings" / "4,922 findings" — totals from different checks summed
as though they shared a common unit. Each product already emits `keel.finding.Finding` (a flat,
one-row-per-occurrence record); this module is a second, additive output built *from* those
Findings (plus whatever richer pre-Finding context a product needs — see each product's own
`triage.py`), grouping repeated occurrences of the same root cause into an `IssueGroup` and
assigning it one of four explainable lanes.

**Does not touch `keel.finding.Finding` at all.** That schema is relied on by every existing
consumer — `apps/hub`'s ingest path (`keel.report.maybe_report`) chief among them — and changing
it is on CLAUDE.md's "never without asking" list. `--format json/table/markdown/sarif/
annotations` and the flat Finding list stay exactly as they are; `TriageResult` is a new,
separate `--format triage-json` output, never a replacement.

**No opaque severity score.** `IssueGroup.lane` is always derived from explainable fields the
`reason` string names directly (verified deadline, deadline proximity, usage/context role,
confidence, source scope, baseline change, policy outcome — whichever apply to that product) —
never a numeric score a reader has to trust blindly.

**The "totals disagree" guard is structural, not a convention.** `TriageResult.issue_groups`,
`.actions`, `.reviews`, `.inventory` and `.actionable_occurrences` are `@computed_field`
properties derived from `.groups` — there is no way to construct a `TriageResult` where a stored
total disagrees with what its own group list says, because no such field exists to disagree.
"""

import hashlib
from datetime import date, datetime
from enum import StrEnum

from keel.finding import ProductCode
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class Lane(StrEnum):
    """Exactly four. The Results Explorer's fifth view, "All evidence", is a flat view over raw
    observations, not a lane an IssueGroup belongs to — adding a fifth member here would silently
    break every product's own lane-derivation logic, which is written against this exact set."""

    ACT_NOW = "act_now"
    PLAN = "plan"
    REVIEW = "review"
    INVENTORY = "inventory"


def make_fingerprint(*parts: str) -> str:
    """One hashing convention for every product's `IssueGroup.fingerprint` — the same
    sha256-and-truncate shape `ebb.build_findings.compute_identity` already uses for
    `Finding.identity`, extracted here so five products share it instead of each reinventing it.

    Order-sensitive by design: callers must pass parts in a fixed, documented order (e.g.
    product, then model, then provider) — swapping two parts must produce a different
    fingerprint, since `(model, provider)` and `(provider, model)` are not the same key."""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


class OccurrenceRef(BaseModel):
    """One raw evidence pointer folded into an `IssueGroup`. Deliberately not a full `Evidence`/
    `Finding` re-embed — the group's own `label`/`reason` already carry what's common across
    every occurrence; this carries only what's specific to *this* one, enough to resolve back to
    the `Finding` it came from and to render an evidence link."""

    model_config = ConfigDict(frozen=True)

    finding_identity: str
    source_uri: str
    locator: str | None = None
    excerpt: str | None = None
    context: str | None = None
    """Product-defined, optional: an ebb occurrence's usage_role, a telltale cell's error_mode,
    etc. Free text on purpose — different products classify occurrences along different axes,
    and this module does not need to know what any of them mean to carry one."""

    @field_validator("source_uri")
    @classmethod
    def _source_uri_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_uri must not be blank")
        return value


class IssueGroup(BaseModel):
    """Deduplicated occurrences of the same root cause, in one lane, with the evidence for every
    occurrence reachable by expanding it — never a hand-picked representative that hides the
    rest."""

    model_config = ConfigDict(frozen=True)

    fingerprint: str
    product: ProductCode
    label: str
    lane: Lane
    reason: str
    """Shown beside the status, e.g. "Plan · retires in 60 days · executable model profile" —
    the explainable-fields requirement made literal: whatever field decided the lane, name it
    here."""
    occurrence_count: int
    context_count: int
    """Distinct contexts among this group's occurrences — e.g. distinct usage_roles for ebb,
    distinct error_modes for telltale. A product with no context axis reports 1."""
    occurrences: list[OccurrenceRef]
    deadline: date | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    """Product-specific display fields (ebb's replacement_id, undertow's region, telltale's
    operation_id) — kept out of the shared schema itself so no product's display needs ever
    require a change here."""

    @field_validator("occurrences")
    @classmethod
    def _occurrences_never_empty(cls, value: list[OccurrenceRef]) -> list[OccurrenceRef]:
        if not value:
            raise ValueError(
                "occurrences must never be empty — an issue group with no evidence is not "
                "a group, same invariant as Finding.evidence (SUITE_ARCHITECTURE.md §3)"
            )
        return value


class TriageResult(BaseModel):
    """One product's whole triaged output for one run. `product`-scoped and never combined with
    another product's `TriageResult` into a single summed number — the suite-level view is "N
    checks require action or review, followed by each check's own domain-specific outcome"
    (brief's own words), not one cross-product integer."""

    model_config = ConfigDict(frozen=True)

    product: ProductCode
    groups: list[IssueGroup]
    observations: int
    """Tier-3: every raw piece of evidence detected, whether or not it ended up in a group —
    the "912 references scanned" number. Independent of len(groups)/occurrence sums, since a
    product may choose not to group every observation (e.g. a clear/inventory row folded away)."""
    generated_at: datetime | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    """Whole-result metadata that isn't about any one group — e.g. telltale's repo-wide "no
    telemetry configuration found anywhere" notice, which must render once, not duplicated
    onto every operation row. Same free-form, product-defined shape as IssueGroup.attributes,
    for the same reason: the shared schema shouldn't need to know what any product's own notice
    keys mean."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def issue_groups(self) -> int:
        return len(self.groups)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def actions(self) -> int:
        return sum(1 for g in self.groups if g.lane in (Lane.ACT_NOW, Lane.PLAN))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reviews(self) -> int:
        return sum(1 for g in self.groups if g.lane is Lane.REVIEW)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def inventory(self) -> int:
        return sum(1 for g in self.groups if g.lane is Lane.INVENTORY)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def actionable_occurrences(self) -> int:
        """Tier-2: the "across N occurrences" half of "3 model migrations across 7 executable
        references" — summed only over act_now/plan groups, never the full occurrence count."""
        return sum(g.occurrence_count for g in self.groups if g.lane in (Lane.ACT_NOW, Lane.PLAN))
