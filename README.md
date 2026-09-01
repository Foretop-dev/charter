# charter

> Make changes to an agent's MCP access visible and reviewable in code review.

`charter` reads committed MCP client configuration, records declared servers and credential
references in a deterministic lock file, and can gate pull requests when access expands.

## Install and run

```console
uvx foretop-charter scan .
```

The default scan reads `.mcp.json` and `.cursor/mcp.json`, writes `charter.lock`, and does not
launch a server or make a network request. Commit the lock file so later changes are visible in
normal review. Run `uvx foretop-charter scan --help` for every option.

## What it checks

- Declared MCP servers, transports, commands, argument count, and endpoints. Schema v4
  structurally excludes argument text because an argument can contain a credential.
- Names of credential-referencing environment variables and headers, never their values.
- Stable lock-file changes for new or modified servers.
- Tool capabilities returned by local stdio servers when enumeration is explicitly enabled.

Unrecognized tools are classified as `unknown`; Charter does not guess their capabilities.

## Output and CI gating

`--format` supports `table`, `markdown`, `json`, `sarif`, `annotations`, and `triage-json`.
Add `--base origin/main` to exit `1` when a server or capability expands relative to the merge
base. Without `--enumerate`, the comparison detects server-level drift only. Exit code `2`
means the scan itself failed.

## GitHub Action

```yaml
- uses: foretop-dev/charter@v0.4.0
  with:
    base: ${{ github.event.pull_request.base.sha }}
    enumerate: "false"
```

The Action emits inline annotations and can maintain one summary comment on pull requests.
Grant `pull-requests: write` when comments are enabled and make the base revision available to
the checkout. Set `base` to an empty string for report-only use. When enumeration is enabled,
the Action installs Bubblewrap and loads Ubuntu's restricted Bubblewrap AppArmor profile on
ephemeral GitHub-hosted Linux runners. It never changes packages or AppArmor policy on a
self-hosted runner: operators must provide a working Bubblewrap boundary there, or the Action
fails closed before scanning.

## Privacy

Static scanning is local. The lock records credential-reference names and argument count
without retaining argument text; argument values are never hashed, rendered, or reported.
Passing `--enumerate` is a materially different Linux-only operation. Charter launches each
configured local stdio server inside a Bubblewrap sandbox with a read-only repository, an
isolated home and temporary directory, a sanitized environment, dropped capabilities, and no
network. Configured credential values are not passed to the server. Charter exits `2` instead
of launching anything when this boundary cannot be established. `--report` and `--gate` are
explicit hosted-mode options and never send config bodies.

## Current limitations

- Only project-level Claude Code and Cursor MCP configuration files are scanned.
- Live enumeration requires Linux and Bubblewrap. Remote transports are recorded but not
  contacted, and stdio servers that require credentials or network access remain `unknown`.
- Ubuntu 24.04 self-hosted runners must allow Bubblewrap's restricted user namespace (for
  example with the distribution's `bwrap-userns-restrict` AppArmor profile).
- Capability-level drift requires comparable enumeration data on both revisions.

## License

Apache-2.0. See [LICENSE](LICENSE).

Questions and bug reports are welcome in
[GitHub Issues](https://github.com/foretop-dev/charter/issues).
