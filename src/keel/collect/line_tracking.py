from typing import Any

import yaml

_LINE_KEY = "__line__"


class _LineTrackingLoader(yaml.SafeLoader):
    """Every mapping gets `__line__` (1-indexed) injected alongside its real keys — PyYAML
    discards node position info once a document is constructed, and per-finding evidence needs
    to survive past parse time. `__line__` is not a realistic key collision — YAML mapping keys
    borrowed from real config formats never look like this — so mutating the mapping in place
    is safe."""


def _construct_mapping(loader: _LineTrackingLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    mapping = yaml.SafeLoader.construct_mapping(loader, node, deep=True)
    if isinstance(mapping, dict):
        mapping[_LINE_KEY] = node.start_mark.line + 1
    return mapping


_LineTrackingLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def parse_with_lines(text: str) -> Any:
    """Like yaml.safe_load, except every mapping in the result carries its own starting line
    number under the `__line__` key (see line_of() to read it back out). Works for JSON too:
    well-formed JSON is valid YAML flow-style syntax, so this is the one parser telltale's
    OpenAPI/Grafana-dashboard parsers and charter's MCP client config parsers both use to get
    real evidence_line values out of either format — verified directly (not assumed) that
    well-formed JSON parses identically through this loader as through json.loads, both when
    this lived in telltale (apps/telltale/src/telltale/openapi.py, first against a real OpenAPI
    document, then again against a real 12KB Grafana dashboard) and independently for charter's
    own JSON-only config formats."""
    return yaml.load(text, Loader=_LineTrackingLoader)


def line_of(mapping: object, default: int = 1) -> int:
    """Reads back the `__line__` a mapping was tagged with by parse_with_lines. `default`
    covers a mapping that was constructed by hand (e.g. in a test) rather than parsed."""
    if isinstance(mapping, dict):
        line = mapping.get(_LINE_KEY)
        if isinstance(line, int):
            return line
    return default
