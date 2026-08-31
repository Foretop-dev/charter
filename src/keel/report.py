import json
import os
from collections.abc import Iterable
from pathlib import Path

import httpx
from keel.finding import Finding
from keel.run import detect_run
from keel.triage import TriageResult

DEFAULT_HUB_URL = "https://app.foretop.dev"

# R11: reproduced live that a 3,782-finding --report payload (~3 MiB) hit ReadTimeout at the
# old flat 30s while Hub's own Cloud Run request budget (300s) had room to spare — the client
# gave up mid-upload/response-wait, not because Hub was actually too slow (measured server-side
# ingest of that same payload at ~2s). 120s is comfortable margin under Hub's 300s ceiling even
# on a slow connection, without waiting indefinitely on a genuinely stuck request.
DEFAULT_REPORT_TIMEOUT_SECONDS = 120.0


class ReportError(Exception):
    """Raised when --report was explicitly requested but cannot actually be sent — a missing
    token, or a non-2xx response from the hub. Never swallowed inside this module: the flag was
    an explicit ask, so failing loudly is the "never silently degrade" rule applied in the
    direction of not silently skipping a requested action, not just not silently succeeding."""


def build_payload(
    *,
    product: str,
    findings: Iterable[Finding],
    repo_root: Path,
    triage: TriageResult | None = None,
) -> dict[str, object]:
    """`keel.run.detect_run`, not a second repo-detection mechanism — its own docstring says
    "not embedded in `--format json`", which this respects: `--report` is a separate channel
    entirely, never mixed into a product's own machine-readable stdout.

    `triage` is additive: a caller that never passes it (every caller before this parameter
    existed) gets `"triage": None` in the payload, the same value hub's `IngestRun.triage`
    already defaults to when the key is entirely absent — the two are equivalent on the
    consuming end. Present-and-computed vs. absent-and-never-computed is a distinction only
    this module's own callers make (see each product's `cli.py`), not the wire shape."""
    run = detect_run(repo_root)
    return {
        "product": product,
        "project": run.repo,
        "commit_sha": run.commit,
        "branch": run.branch,
        "started_at": run.detected_at.isoformat(),
        "status": "ok",
        "findings": [f.model_dump(mode="json") for f in findings],
        "triage": triage.model_dump(mode="json") if triage is not None else None,
    }


def send_report(
    payload: dict[str, object], *, hub_url: str, token: str, client: httpx.Client
) -> httpx.Response:
    return client.post(
        f"{hub_url.rstrip('/')}/v1/runs",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def maybe_report(
    *,
    enabled: bool,
    product: str,
    path: Path,
    findings: Iterable[Finding],
    triage: TriageResult | None = None,
    client: httpx.Client | None = None,
) -> None:
    """The one thing every CLI's `--report` option calls. Always prints the exact payload
    before sending, regardless of outcome — the "never upload your source" promise is a
    transparency promise, not just a metadata-only promise: the user sees precisely what leaves
    the machine, every time, not just on request."""
    if not enabled:
        return

    payload = build_payload(product=product, findings=findings, repo_root=path, triage=triage)
    print(json.dumps(payload, indent=2, sort_keys=True))

    token = os.environ.get("FORETOP_TOKEN")
    if not token:
        raise ReportError(
            "--report requires FORETOP_TOKEN (a hub-issued API token) to be set — refusing to "
            "silently skip an explicitly requested send."
        )
    hub_url = os.environ.get("FORETOP_HUB_URL", DEFAULT_HUB_URL)

    owns_client = client is None
    client = client or httpx.Client(timeout=DEFAULT_REPORT_TIMEOUT_SECONDS)
    try:
        response = send_report(payload, hub_url=hub_url, token=token, client=client)
    finally:
        if owns_client:
            client.close()

    if response.status_code >= 400:
        raise ReportError(f"hub rejected the report: {response.status_code} {response.text}")
    print(f"reported to {hub_url}: {response.json()}")
