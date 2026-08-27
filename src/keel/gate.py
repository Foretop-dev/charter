"""H6/H8: server-side baselines and suppressions, fetched and enforced by each product's own
CLI — not just displayed by hub's local, file-based viewer (`hub.baseline`/`hub.suppressions`,
unrelated to this module and unaffected by it).

Named `gate`, not `policy` — lading already owns a `--policy` flag and a `lading.policy`
module for a different, licence-obligation-specific concept; reusing the name here would
collide in both meaning and CLI surface.

A **baseline** is a named, org-wide set of identities to treat as pre-existing, never a new
failure. A **suppression** is a single identity, with an owner/reason/expiry and an optional
`revoked_at` — an explicit, accountable, time-bounded exception, not a permanent grandfather
clause. Both are `Finding.identity` strings; every product's own `compute_identity` already
prefixes its output with the product name (`"ebb|..."`, `"charter|..."`, ...), so one flat,
org-wide set is safe — no product can ever collide with another's identity.
"""

import os
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from keel.report import DEFAULT_HUB_URL
from pydantic import BaseModel, ConfigDict, Field


class Baseline(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    identities: list[str] = Field(default_factory=list)


class Suppression(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: str
    owner: str
    reason: str
    expiry: date
    revoked_at: datetime | None = None

    def is_active(self, *, today: date) -> bool:
        return self.revoked_at is None and today <= self.expiry


class GateResponse(BaseModel):
    """The whole wire shape `GET /v1/gate` returns — org-wide, not scoped to one product."""

    model_config = ConfigDict(frozen=True)

    baseline: Baseline | None = None
    suppressions: list[Suppression] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GateResult:
    """Per-identity why, not just a bare excluded set — a suppression carries its own
    owner/reason/expiry that a caller (hosted display routes chief among them) needs to show,
    not just a yes/no. Baseline and suppression are reported independently when both apply to
    the same identity: neither hides the other, matching hub's own local, file-based viewer,
    which already shows `in_baseline` and `suppression` side by side rather than picking a
    winner (hub/web.py's `finding_detail` route)."""

    baseline_hits: frozenset[str]
    suppression_hits: dict[str, Suppression]

    @property
    def excluded(self) -> frozenset[str]:
        return self.baseline_hits | self.suppression_hits.keys()


def gate_identities(candidates: set[str], gate: GateResponse, *, today: date) -> GateResult:
    """Pure, total: which of `candidates` are covered by the baseline or an active (non-expired,
    non-revoked) suppression, and why. `candidates` need not all be real gate-triggering
    identities — hosted display routes call this with every identity on a page, a CLI calls it
    with only the identities that would otherwise cause exit 1."""
    baseline_identities = frozenset(gate.baseline.identities) if gate.baseline else frozenset()
    baseline_hits = frozenset(candidates) & baseline_identities

    suppression_hits: dict[str, Suppression] = {}
    for suppression in gate.suppressions:
        if suppression.identity in candidates and suppression.is_active(today=today):
            suppression_hits[suppression.identity] = suppression

    return GateResult(baseline_hits=baseline_hits, suppression_hits=suppression_hits)


def apply_gate(candidates: set[str], gate: GateResponse, *, today: date) -> frozenset[str]:
    """The direct answer a CLI's own exit-code decision needs: which candidates are *not*
    excluded by the baseline or an active suppression, and therefore still real. Exit 1 only
    if this is non-empty."""
    result = gate_identities(candidates, gate, today=today)
    return frozenset(candidates) - result.excluded


def summarize(result: GateResult) -> str:
    """One line, printed alongside a CLI's normal output whenever --gate was used — the same
    transparency `maybe_report` already gives the outbound side (`keel.report`'s own "always
    prints the exact payload" rule), applied to the inbound side: a reader must be able to see
    what was excluded and why, not just a lower exit code."""
    if not result.excluded:
        return "gate: nothing excluded"
    baseline_count = len(result.baseline_hits)
    suppression_count = len(result.suppression_hits)
    parts = []
    if baseline_count:
        parts.append(f"{baseline_count} baselined")
    if suppression_count:
        parts.append(f"{suppression_count} suppressed")
    return f"gate: {len(result.excluded)} finding(s) excluded ({', '.join(parts)})"


class GateFetchError(Exception):
    """Raised when --gate was explicitly requested but the fetch cannot actually happen — a
    missing token, or a non-2xx response from the hub. Never swallowed: the flag was an explicit
    ask, so failing loudly is the same "never silently skip a requested action" rule
    `keel.report.ReportError` already applies to the outbound side."""


def fetch_gate(*, hub_url: str, token: str, client: httpx.Client) -> GateResponse:
    response = client.get(
        f"{hub_url.rstrip('/')}/v1/gate", headers={"Authorization": f"Bearer {token}"}
    )
    if response.status_code >= 400:
        raise GateFetchError(f"hub rejected the gate fetch: {response.status_code} {response.text}")
    return GateResponse.model_validate(response.json())


def maybe_fetch_gate(*, enabled: bool, client: httpx.Client | None = None) -> GateResponse | None:
    """The one thing every CLI's `--gate` option calls. Disabled (the default) is a true
    no-op — no network call, no token lookup — the same offline-by-default guarantee
    `keel.report.maybe_report` already gives `--report` (MARKETING_BRIEF.md's own promise:
    "runs offline, no account")."""
    if not enabled:
        return None

    token = os.environ.get("FORETOP_TOKEN")
    if not token:
        raise GateFetchError(
            "--gate requires FORETOP_TOKEN (a hub-issued API token) to be set — refusing to "
            "silently skip an explicitly requested fetch."
        )
    hub_url = os.environ.get("FORETOP_HUB_URL", DEFAULT_HUB_URL)

    owns_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        return fetch_gate(hub_url=hub_url, token=token, client=client)
    finally:
        if owns_client:
            client.close()
