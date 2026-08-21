import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from charter.capability import classify_tool
from charter.enumerate import Tool

GOLDEN_DIR = (Path(__file__).resolve().parents[1] / "golden").resolve()
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "golden_manifest.yaml"

# Same floor and the same discipline as apps/ebb/tests/unit/test_golden_corpus.py
# (CLAUDE_CODE_PLAN.md Session 7, reused unchanged for every product since). The floor, not a
# target — never lower it to make this test pass; if the classifier falls short, the taxonomy
# (src/charter/rules/capability_taxonomy.yaml) is what changes.
PRECISION_FLOOR = 0.95
RECALL_FLOOR = 0.85

# (relative_path, capability) — the full shape of "one piece of ground truth" for this
# classifier, the same role ebb's (path, line, matched_text) plays for its own detectors.
MatchKey = tuple[str, str]


def _group_of(relative_path: str) -> str:
    return relative_path.split("/", 1)[0]


def _all_groups() -> list[str]:
    return sorted(p.name for p in GOLDEN_DIR.iterdir() if p.is_dir())


def _load_expected() -> dict[str, Counter[MatchKey]]:
    entries = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or []
    by_group: dict[str, Counter[MatchKey]] = defaultdict(Counter)
    for entry in entries:
        key: MatchKey = (entry["path"], entry["capability"])
        by_group[_group_of(entry["path"])][key] += 1
    return by_group


def _load_tool(fixture_path: Path) -> Tool:
    data: dict[str, Any] = json.loads(fixture_path.read_text(encoding="utf-8"))
    return Tool(
        server_name="golden",
        name=data["name"],
        description=data.get("description"),
        input_schema=data.get("inputSchema"),
    )


def _load_actual() -> dict[str, Counter[MatchKey]]:
    by_group: dict[str, Counter[MatchKey]] = defaultdict(Counter)
    for fixture_path in sorted(GOLDEN_DIR.rglob("tool.json")):
        relative_path = str(fixture_path.relative_to(GOLDEN_DIR))
        classification = classify_tool(_load_tool(fixture_path))
        for capability in classification.capabilities:
            key: MatchKey = (relative_path, capability.value)
            by_group[_group_of(relative_path)][key] += 1
    return by_group


@dataclass(frozen=True)
class GroupScore:
    group: str
    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float | None:
        denominator = self.true_positives + self.false_positives
        return self.true_positives / denominator if denominator else None

    @property
    def recall(self) -> float | None:
        denominator = self.true_positives + self.false_negatives
        return self.true_positives / denominator if denominator else None


def _score(
    expected: dict[str, Counter[MatchKey]], actual: dict[str, Counter[MatchKey]]
) -> list[GroupScore]:
    """Per-group, never pooled into one aggregate: a group with 0/5 correct must not be
    averaged away by five groups running at 5/5 (CLAUDE_CODE_PLAN.md's explicit warning: "an
    average must never hide one broken detector"). Every group directory on disk is scored,
    even one with zero expected entries (golden/unknown/) — a false positive there (a tool
    that should stay unknown but matched a rule) must show up as a real precision failure, not
    be silently skipped for having nothing expected."""
    scores = []
    for group in sorted(set(expected) | set(actual) | set(_all_groups())):
        exp, act = expected.get(group, Counter()), actual.get(group, Counter())
        scores.append(
            GroupScore(
                group=group,
                true_positives=sum((exp & act).values()),
                false_positives=sum((act - exp).values()),
                false_negatives=sum((exp - act).values()),
            )
        )
    return scores


def _format_report(scores: list[GroupScore]) -> str:
    header = f"{'group':<20}{'TP':>4}{'FP':>4}{'FN':>4}{'precision':>12}{'recall':>10}"
    rows = [header, "-" * len(header)]
    for s in scores:
        precision = f"{s.precision:.3f}" if s.precision is not None else "n/a"
        recall = f"{s.recall:.3f}" if s.recall is not None else "n/a"
        rows.append(
            f"{s.group:<20}{s.true_positives:>4}{s.false_positives:>4}{s.false_negatives:>4}"
            f"{precision:>12}{recall:>10}"
        )
    return "\n".join(rows)


def test_every_group_meets_the_accuracy_floor() -> None:
    scores = _score(_load_expected(), _load_actual())
    report = _format_report(scores)
    print("\n" + report)

    violations = [
        f"{s.group}: precision {s.precision:.3f} < floor {PRECISION_FLOOR}"
        for s in scores
        if s.precision is not None and s.precision < PRECISION_FLOOR
    ] + [
        f"{s.group}: recall {s.recall:.3f} < floor {RECALL_FLOOR}"
        for s in scores
        if s.recall is not None and s.recall < RECALL_FLOOR
    ]
    if violations:
        pytest.fail(f"\n{report}\n\nBelow the accuracy floor:\n" + "\n".join(violations))


def test_every_fixture_group_with_expectations_has_a_pure_decoy_case() -> None:
    """A group could pass the floor above by never being tempted — if every fixture in a group
    only contains true positives, an over-eager rule would never be caught (0 FP is trivial
    when nothing false is ever offered). Every group that has at least one expected capability
    must also have at least one fixture contributing zero expected entries — same requirement
    apps/ebb/tests/unit/test_golden_corpus.py enforces. golden/unknown/ is exempt: every one of
    its fixtures already is a decoy by construction (zero expected entries for all of them)."""
    expected = _load_expected()
    fixture_dirs_with_zero_expected: dict[str, set[str]] = defaultdict(set)
    for group_dir in sorted(GOLDEN_DIR.iterdir()):
        if not group_dir.is_dir():
            continue
        expected_paths = {key[0] for key in expected.get(group_dir.name, Counter())}
        for fixture_dir in sorted(group_dir.iterdir()):
            if not fixture_dir.is_dir():
                continue
            has_expected = any(
                p.startswith(f"{group_dir.name}/{fixture_dir.name}/") for p in expected_paths
            )
            if not has_expected:
                fixture_dirs_with_zero_expected[group_dir.name].add(fixture_dir.name)

    missing = [g for g in expected if not fixture_dirs_with_zero_expected.get(g)]
    assert not missing, f"capability groups with no pure-decoy fixture: {missing}"
