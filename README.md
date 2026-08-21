# charter

> Scans an agent configuration and produces a reviewable record of every MCP server, tool,
> scope and credential path — then fails CI when an update quietly expands what the agent can
> reach.

Full product spec: `../../specs/charter.md`. This file tracks build status; the spec is the
source of truth for scope and decisions.

**Scope note (Session 15):** specs/charter.md's own §0 describes a much larger build —
Supabase-backed org inventory, GitHub OAuth, Paddle/Lemon Squeezy billing, a hosted dashboard
(FR-007, its own Slice 5). None of that is in scope here, same boundary ebb and telltale both
shipped within: the OSS CLI + CI Action is the whole product until `PILOT_STATUS >= 3`
(specs/charter.md's own "Validation gate"). Sessions 15-17 build exactly the pattern
CLAUDE_CODE_PLAN.md's Part 4 describes: collector, distinctive engine, renderers + golden
corpus, Action + PR comment + publish-prep.

**Decisions locked in from specs/charter.md §1's checkpoint** (all defaults except DEC-03,
which the user picked explicitly):
- **DEC-01** (config sources): Claude Code's `.mcp.json` and Cursor's `.cursor/mcp.json`, both
  project-root, both meant to be committed — matches DEC-04's "the review is a code review"
  framing. Claude Desktop's `claude_desktop_config.json` lives in a per-machine app-support
  directory, never committed to a repo, so it's out of scope for a "review this in CI" tool.
- **DEC-02** (tool inventories): static parse by default; live `tools/list` enumeration behind
  an explicit `--enumerate` flag, since that means launching third-party code.
- **DEC-03** (classification): rules-only for v1 — no LLM dependency, no API key, no cost. The
  spec's own default is hybrid (rules + a cached LLM assist for unrecognized tools); that part
  is deliberately not built yet, same as every other real-external-dependency decision this
  build has paused on rather than deciding unilaterally. An unrecognized tool is classified
  `unknown` — a real, honest verdict — not guessed at.
- **DEC-04** (diff baseline): a committed `charter.lock` file.
- **DEC-05** (policy language): YAML.
- **DEC-06** (secret detection depth): names and paths only. This is a hard invariant, not
  just a convention — see below.

## Status

**Slice 1 (Session 15): config parse + manifest write + the stable-hash property test.**

```
uv run --package foretop-charter charter scan <path>
```

Checks a small, fixed set of committed, project-scoped config paths under `path` —
`.mcp.json` (Claude Code) and `.cursor/mcp.json` (Cursor), both confirmed live against each
client's own docs this session, not a full repo walk (`src/charter/collect.py`; same reasoning
telltale's own OpenAPI-spec autodetection uses for a fixed candidate list). Parses whichever
exist into `Server` records (`src/charter/models.py`) and writes a canonical, deterministic
`charter.lock` (`src/charter/lockfile.py`) — the committed diff baseline DEC-04 calls for:
"it makes the review a code review, which is the whole product." No policy engine or differ
yet (that's Slice 3) — for now, git's own diff on the committed lock file *is* the review
mechanism, the same way a `package-lock.json` works.

**Canonical serialization** (`src/charter/canonical.py`) — specs/charter.md §6: "sorted keys,
no floats, LF endings, trailing newline. Test byte-equality across two runs." Recursive
`sort_keys=True` makes dict-insertion-order irrelevant; a value that would serialize unstably
(a float) is rejected outright rather than trusted to `repr()` consistently; the lock file is
written with `Path.write_bytes`, never a text-mode handle, so Windows can't silently turn `\n`
into `\r\n`. Proven with a genuine Hypothesis property test
(`tests/unit/test_stable_hash_property.py`), not just a fixed fixture run twice: generates
~100 arbitrary (valid-shaped) `.mcp.json` documents per run and asserts the rendered manifest
is byte-identical across two scans of the same unchanged file.

**DEC-06 as a hard invariant, not a convention**: `Server` has no field that could carry a
credential's *value* — `env_var_names`/`header_names` are exactly that, names only (evidence
that a server references a credential, e.g. `AIRTABLE_API_KEY`), never what's assigned to
them. This is structural (there's nowhere on the dataclass to put a value even by accident),
not just tested — though it's tested too, at the parser, lockfile, and CLI layers, each
asserting a planted fake secret value never appears in output. `args` is the one field
captured verbatim rather than name-only, a deliberate choice made after a real fixture
surfaced why it matters — see `Server`'s own docstring in `models.py` for the full reasoning
(short version: a positional CLI argument can itself be a connection string with a literal
password in it, and DEC-06's own default is "never attempt to detect actual secret values" —
which applies to `args` exactly as much as it applies to `env`/`headers`; hiding `args` would
also hide the launch command a reviewer needs to actually understand what a server does).

**Two client config formats, confirmed live, not from memory**: Claude Code's `.mcp.json`
(`code.claude.com/docs/en/mcp`) requires an explicit `type` on any `url`-having entry — Claude
Code itself treats a bare `url` with no `type` as a configuration error and skips that server;
`src/charter/parsers/claude_code.py` mirrors that exact real behavior rather than guessing a
transport Claude Code itself wouldn't have used. Cursor's `.cursor/mcp.json`
(`cursor.com/docs/context/mcp`) has no `type` field at all — presence of `command` vs. `url`
is what distinguishes stdio from remote, and Cursor's own docs don't expose a separate SSE/
WebSocket distinction the way Claude Code's `type` does, so every Cursor remote entry is
recorded as `http`, a documented limitation of what the format itself reveals. `envFile` and
`auth` (real fields in Cursor's format) aren't read yet — left for a later slice rather than
half-modeled now.

**`keel.collect.line_tracking`**: charter's two parsers get real `evidence_line` values from
the exact same line-tracking YAML/JSON loader telltale's own parsers already used — extracted
from `apps/telltale/src/telltale/yaml_lines.py` into `packages/keel` this session, since
charter's need for it was genuinely identical, not merely similar (see
`packages/keel/README.md`'s own Session 15 note for the full extraction story).

**Verified for real** against a fixture covering all three interesting cases at once — a
stdio server with a real credential env var, a remote HTTP server with a real credential
header, and a `url`-with-no-`type` misconfigured entry — before any of this shipped: ran
`charter scan` against it, confirmed the misconfigured entry was correctly skipped, confirmed
the terminal output and `charter.lock` both showed `AIRTABLE_API_KEY`/`Authorization` as
*names* and never the planted fake secret *values*, and confirmed a Cursor-format server in
`.cursor/mcp.json` with a connection-string-in-args was captured (which is what surfaced the
`args`-verbatim design note above).

**Slice 2, part 1 (Session 15): live enumeration.**

```
uv run --package foretop-charter charter scan <path> --enumerate
```

DEC-02: "launching third-party servers is a security decision the user must make consciously"
— static parsing (Slice 1) never launches anything; `--enumerate` does, so it's opt-in and
never the default. Stdio servers only for now — remote (http/sse/ws) enumeration would mean
building an OAuth-capable HTTP+SSE MCP client, real scope creep beyond what this slice needs;
a documented gap, not a silent one.

`src/charter/enumerate.py` is a real MCP client speaking the classic
`initialize` → `notifications/initialized` → `tools/list` handshake over newline-delimited
JSON-RPC on the child's stdin/stdout — built from `modelcontextprotocol.io`'s own lifecycle
and stdio-transport spec pages, fetched live this session, not from memory. Deliberately pinned
to protocol version `2025-06-18` rather than the `2026-07-28` draft's newer
`server/discover`-based "modern" era: essentially no real-world server speaks the new one yet,
and specs/charter.md §11 already names protocol churn as an ongoing risk to revisit, not solve
preemptively.

**A hard timeout, and a real one** — a background thread runs the whole exchange, an overall
`queue.Queue.get(timeout=...)` bounds it, and shutdown always follows the spec's own sequence
(close stdin → wait → `SIGTERM` → `SIGKILL`). Verified for real, not asserted: a dedicated
`--sleep-forever` mode in the test fixture below proves a hung server is actually killed within
the timeout, not left running — `ps aux` was checked by hand after the first run to confirm no
orphaned process was left behind before this shipped.

**`${VAR}`/`${VAR:-default}` expansion** — Claude Code's own documented syntax
(`code.claude.com/docs/en/mcp`, fetched live). A real server needs its real `command`/`args`/
`env` values to actually launch, which `Server` itself never carries (DEC-06). `_resolve_launch`
re-reads the original config entry for exactly the one server being enumerated and resolves
values only as local variables inside `enumerate.py`'s own call stack, passed straight into
`subprocess.Popen`'s `env=` — never assigned to any field, logged, or serialized anywhere else.
Confirmed by a dedicated test that plants a fake secret value and asserts it never appears in
`repr()` of the result.

**`tests/fixtures/toy_mcp_server.py`**: a real, minimal MCP server, hand-written from the same
spec pages `enumerate.py` was built against — no MCP SDK, no charter code — so a passing test
against it is evidence the wire protocol handling actually works against a real subprocess
exchanging real messages, not just that two halves of a mock agree with each other. Supports
`--fail-tools-list`, `--crash-on-init`, `--sleep-forever` flags to exercise every failure mode
`enumerate_stdio_server` has to degrade through.

**Lock file schema v2**: adds `tools`/`enumeration_error` per server, three states mirroring
telltale's own three-state coverage discipline — `tools: null` means not attempted (a plain
scan, or this server isn't stdio), `tools: []` means attempted and the server genuinely
advertised none, and a real tools array means it succeeded. Never conflating "we didn't check"
with "we checked and found nothing" is the same principle either way.

Verified end-to-end for real: `charter scan --enumerate` against a config pointing at the toy
server, confirming the terminal output and `charter.lock` both show the two real discovered
tools (`read_file`, `write_file`) with their real input schemas.

**Slice 2, part 2 (Session 15): the rules-only capability classifier.**

`src/charter/capability.py` classifies every tool live enumeration discovers into
specs/charter.md §2.2's exact five categories (`read`/`write`/`network_egress`/
`code_execution`/`credential_access`) — DEC-03's rules-only choice, no LLM. A tool can match
more than one: `web_search` is both `read` (returns information) and `network_egress` (makes
an outbound call) — under-reporting either would hide something real. Overall severity is the
*maximum* across every capability a tool matched (`credential_access`/`code_execution` →
CRITICAL, `write` → HIGH, `network_egress` → MEDIUM, `read` → LOW), per specs/charter.md's own
accuracy strategy: "errs toward higher severity; a misclassified-as-dangerous tool is
annoying, the reverse is a breach." An unrecognized tool matches nothing and gets `unknown`
(`capabilities: []`, severity MEDIUM) — a real, honest verdict, not guessed at, and not
silently treated as safe either.

Rules live in one versioned YAML file with a changelog
(`src/charter/rules/capability_taxonomy.yaml`, specs/charter.md §6's explicit requirement),
matched by substring on the tool's lowercased name/description and by lowercased input-schema
property-name membership — deliberately simple, the same "good enough, not semantically
perfect" scope call telltale's own PromQL/OTTL condition matching already makes. Patterns were
derived from real tool-naming conventions seen across the official filesystem/GitHub/Slack/
Postgres MCP servers and the `tools/list` spec's own examples, not invented from nothing. A
real accuracy floor (golden corpus, precision/recall) is Slice 3's job, not this file's.

Lock file schema v3 adds `capabilities`/`severity`/`rule_version` per tool — capabilities are
sorted before serializing, not left in `frozenset` iteration order, which Python does not
guarantee stable across separate process runs (string-hash randomization);
`test_capabilities_are_sorted_for_byte_stability` guards this directly.

**A real bug, found before this shipped, not after**: the terminal renderer's Tools column
started annotating each tool name with its severity (`read_file [low]`), and a smoke test
against a real enumerated server showed the `[low]`/`[high]` suffix silently vanishing —
Rich's `Table` parses plain cell strings as its own markup language, and `[low]` isn't a
recognized style tag, so Rich absorbed it instead of erroring. Worse: a tool name or
enumeration-error string containing real markup syntax (`[/bold red]`) didn't just render
oddly, it **crashed** the renderer outright (`rich.errors.MarkupError`) — confirmed by
deliberately reverting the fix and re-running the test suite before shipping, not just
reasoned about. Tool names and error messages come verbatim from a live, untrusted third-party
server's own response (DEC-02's whole point), so every dynamic value reaching Rich now goes
through `rich.markup.escape` — server-config strings and, especially, anything sourced from a
live server's own output. Regression tests plant literal `[...]` sequences in a tool name, a
server name, and an enumeration error to prove none of them can vanish or crash rendering
again.

Verified end-to-end for real: `charter scan --enumerate` against the toy server, confirming
`read_file [low], write_file [high]` (or the equivalent ANSI-colored form) actually appears in
both the terminal table and matches `charter.lock`'s own `capabilities`/`severity` fields.

**Slice 3 (Session 16): markdown/SARIF renderers, the capability classifier's golden corpus,
and the CI accuracy gate.**

**Scope calls, same style as Session 15's:** no differ yet — `specs/charter.md`'s FR-006
"capability diff" PR comment needs one, and Session 15's own note already deferred that ("git's
own diff on the committed `charter.lock` is the review mechanism" until a real one exists);
building it belongs with the Action work in Session 17, where it has an actual consumer. So
`render_markdown`/`render_sarif` (`src/charter/render/markdown.py`,
`src/charter/render/sarif.py`) show the **current scan snapshot** — the same content
`render_terminal` already shows, reformatted — not a before/after. No JSON renderer either:
spec §6 lists `markdown | sarif | json`, but `charter.lock` (schema v3) already *is* the
canonical JSON artifact; a second one would just duplicate it. `charter scan --format
{table,markdown,sarif}` selects the display format (default `table`, unchanged); `charter.lock`
is still always written regardless.

**SARIF locations point at the server's config source**, not the tool — `server.source_file`/
`server.source_line`, since a live-enumerated tool has no line of its own to point at (DEC-02:
it comes from a `tools/list` call, not static text). Severity collapses charter's four levels
into SARIF's three real ones the same way ebb's own `render/sarif.py` does:
`LOW→note, MEDIUM→warning, HIGH→error, CRITICAL→error`. A tool matching more than one
capability produces one SARIF result per capability, same "don't under-report" stance
`capability.py`'s own docstring takes for `Classification.capabilities`. `--format sarif`
without `--enumerate` is not an error — it prints the full rule catalog with zero results,
since there's genuinely nothing to report yet.

**The golden corpus** (`tests/golden/<capability>/<NN_name>/tool.json`,
`tests/golden_manifest.yaml`, `tests/unit/test_golden_corpus.py`) scores the rules-only
classifier per capability group — never pooled into one aggregate, same "an average must never
hide one broken group" discipline `apps/ebb/tests/unit/test_golden_corpus.py` established —
against the same `PRECISION_FLOOR = 0.95` / `RECALL_FLOOR = 0.85` every other product in this
suite uses. 30 fixtures across six groups (the five `CapabilityClass` values plus `unknown`),
grounded in real tool definitions from the actual official filesystem/GitHub/Slack/Puppeteer/
Brave-Search MCP servers the taxonomy's own changelog already cites — not invented vocabulary.
Every group with an expected capability carries at least one pure-decoy fixture (a tool that
shares surface vocabulary with a real match without containing the actual matched pattern —
e.g. `readiness_probe`, which contains "read" but not the `read_`/`_read` substrings the rules
actually check for), enforced by its own test so a rule that over-matches can't hide behind
fixtures that only ever offer true positives. `golden/unknown/` is exempt from that
requirement: every one of its fixtures is already a negative case by construction.

**A real precision bug, found by the corpus before it shipped, not after**: `get_pull_request`
(a real GitHub MCP tool) was classified `network_egress` in addition to the correct `read` —
network_egress's bare `"request"` name pattern matched the substring "request" inside
"pull_**request**", nothing to do with an actual outbound call. The same bug would have hit
`create_merge_request`, `approve_service_request`, or any other tool whose name merely contains
the English word "request". Fixed in `capability_taxonomy.yaml` v2 (changelog entry in the file
itself): bare `"request"` replaced with the specific compound forms a real HTTP-client-shaped
tool actually uses — `http_request`, `api_request`, `make_request`, `send_request`,
`web_request`. Confirmed by re-running the full corpus before and after: 0.800 precision on
`read` before the fix, 1.000 after, every other group unaffected.

Verified end-to-end for real: `charter scan --enumerate --format markdown` and `--format sarif`
against the toy server, confirming real tool names/capabilities/severities appear in both, the
`--format sarif` SARIF log validates as well-formed JSON with the expected rule catalog, and
neither renderer leaks a planted fake secret value from a config fixture that has one (only the
existing `AIRTABLE_API_KEY` *name*, never its value — DEC-06, same check Slice 1 and 2 each
ran). `make accuracy-charter` reports 1.000 precision / 1.000 recall on every scored group.

**Slice 4 (Session 17): the capability-drift gate, the composite Action, PR comment, and
publish-prep.**

**The gap this session closes:** unlike ebb (`--fail-on`) and telltale (regression-vs-merge-
base), charter's `scan` had no failure condition at all through Session 16 — a naive Action
would only ever comment, never fail a PR, undercutting the product's own pitch ("fails CI when
an update quietly expands what the agent can reach"). `charter scan --base <ref>` now exits 1
when a new server appears, or an existing tool's capability severity increases, since the
merge base — never an absolute threshold, mirroring telltale's own regression-only philosophy
exactly (`src/charter/drift.py`).

**The baseline is the committed `charter.lock`, not a live re-scan** — DEC-04's own framing
("`charter.lock` *is* the review mechanism") drives this directly: `charter/git_ref.py`'s
`show_file_at_ref` reads the lock file's content at the merge base via `git show`, never
launching whatever third-party MCP server versions existed at an old commit the way telltale's
worktree-based `--base` has to (it has no single lockfile-shaped artifact to diff against).
`toplevel`/`merge_base`/`GitError` moved to `packages/keel/src/keel/git_ref.py` this session —
pure git plumbing telltale's own `git_ref.py` already had, extracted because charter needed
the identical thing (same "genuinely identical, not merely similar" bar Session 15 used to
extract `keel.collect.line_tracking`); telltale now re-imports them, keeping only its
telltale-specific `checkout_ref`.

**A real, honest limitation, not a silent one**: tool-level drift (`NEW_TOOL`/
`SEVERITY_INCREASED`) is only computed when *both* the current scan and the baseline commit
were `--enumerate`'d — a server that wasn't enumerated on either side has nothing honest to
compare (not "no tools", not "no drift", just unknown), the same null/empty/populated
discipline `lockfile.py`'s own `tools: null` already encodes. `NEW_SERVER` drift has no such
limitation — a server's mere presence in the config is real evidence regardless of
enumeration.

**`apps/charter/action.yml`** (subpath, not the repo root — ebb already claimed that):
`enumerate` defaults to `"false"` (DEC-02 extended to the Action layer — launching third-party
servers in a shared CI runner is still a conscious opt-in, not a default; the documented
consequence is that only `NEW_SERVER` drift fires out of the box), `base` defaults to the PR's
base SHA (the gate itself *does* default on, unlike `enumerate` — matching telltale's own
`base` default). Steps mirror `apps/telltale/action.yml` exactly: scan with `--format
annotations` → render `--format markdown` for the sticky PR comment → upsert by marker
(`apps/charter/scripts/upsert_pr_comment.sh`, a byte-identical copy of the generic root-level
script) → fail on the captured exit code.

**Annotations, not a SARIF-upload step, for inline PR feedback** — verified live against
GitHub's own code-scanning docs before deciding: `github/codeql-action/upload-sarif` needs
`security-events: write` and, on private repos, GitHub Advanced Security/Code Security enabled.
Workflow-command annotations (`src/charter/render/annotations.py`, same
`::error file=...,line=...::` mechanism and escaping helpers ebb's and telltale's own
`render/annotations.py` use, duplicated on purpose) need neither — zero extra permissions, works
on every plan — so that's the composite Action's default. SARIF (`render/sarif.py`) stays
available for anyone who wants to wire their own upload step; it was not extended with a drift
section this session (still a snapshot renderer, Session 16's own call) since annotations
already cover the narrative-diff job.

**Publish-prep, matching `docs/adr/0002-ebb-public-mirror.md` and telltale's own Session 16
boundary exactly**: `.github/workflows/charter-release.yml` (tag prefix `charter-v*`),
`scripts/mirror-workflows/charter-{ci,release}.yml`, and `scripts/sync-charter-mirror.sh` were
added, but the script was **not run against a real repo**. `apps/charter/LICENSE` (Apache-2.0,
"the foretop authors", byte-identical to ebb's/telltale's own) is new; `license-files =
["LICENSE"]` added to `pyproject.toml` and verified for real by building the wheel and
confirming `LICENSE` lands in `dist-info/licenses/` — telltale's own Session 16 found this
missing the hard way, so it was checked directly this time rather than assumed. The
`foretop-charter`/`charter` dual script entries telltale's Session 16 had to add were already
both present in charter's `pyproject.toml` from Slice 1 — nothing to fix there.

**Still the user's job, not this session's** (`NEXT_STEPS.md` Track A, same split every
predecessor used): creating the `nclsmitchell/charter` repo, actually running
`sync-charter-mirror.sh`, creating the `foretop-charter` PyPI project, configuring its trusted
publisher (owner `nclsmitchell`, repository `charter`, workflow `release.yml`, environment
`pypi`), cutting a `charter-v0.1.0` release, and Marketplace submission. The `foretop-keel`
workspace-only dependency problem `packages/keel/README.md` used to document as unresolved for
this mirror (and ebb's/telltale's) is fixed as of a later session — see that file's own
"Packaging note" — `scripts/sync-charter-mirror.sh` now vendors the exact `keel` submodules
charter imports (`git_ref.py`, `collect/line_tracking.py`) into the mirror at sync time.

Verified end-to-end for real: a real git repo fixture (commit a `charter.lock` with one
server, branch, add a second server) drives `charter scan --base <branch>` to exit 1 with a
`NEW_SERVER` finding visible in both `--format annotations` and `--format markdown`; an
unrelated change on the same branch exits 0; an unknown `--base` ref exits 2. `make check`
passes across `packages/keel`, `apps/telltale`, and `apps/charter` together, confirming the
`keel.git_ref` extraction didn't regress telltale.
