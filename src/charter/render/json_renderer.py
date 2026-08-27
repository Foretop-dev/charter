import json

from keel.finding import Finding


def render_json(findings: list[Finding]) -> str:
    """Identical shape to apps/ebb/src/ebb/render/json_renderer.py and
    apps/telltale/src/telltale/render/json_renderer.py, down to the argument order of
    json.dumps — three products now emit one envelope, so the hosted hub has one shape to
    ingest rather than five. Named json_renderer.py rather than json.py for the same reason
    ebb's and telltale's are: a module named `json` inside the package would shadow the
    stdlib import on the line above it.
    """
    return json.dumps([f.model_dump(mode="json") for f in findings], indent=2, sort_keys=True)
