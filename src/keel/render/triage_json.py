"""Serializes a `keel.triage.TriageResult` for `--format triage-json`.

Its own module, not folded into `keel.render.markdown`/`annotations`, because unlike those two
(genuinely shared table primitives with no product-specific shape) this renders one already-
product-agnostic Pydantic model — the entire function is `model_dump`, kept here so every
product's CLI imports the same one line rather than five copies of `json.dumps(...)`.
"""

import json

from keel.triage import TriageResult


def render_triage_json(result: TriageResult) -> str:
    return json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True)
