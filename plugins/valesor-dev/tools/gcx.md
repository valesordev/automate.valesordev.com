---
tool: gcx
kind: cli
domain: Grafana Cloud
check: gcx --version
catalog: gcx commands -o json --flat
read_verbs: [list, list-*, get, get-*, query, query-*, search, status, outline, describe-table, series, labels, label-names, label-values, metrics, metadata, data-range, timeline, schema, stats, diff, span, profile, map, collections, baseline, export, check, wait, current-context, commands, help, help-tree, version]
always_ask: [gcx api]
instead_of:
  commands: [mimirtool, cortextool, logcli, tempo-cli, profilecli, promtool]
  bash:
    - '\b(curl|wget|http|https|xh)\b.*\.grafana\.net'
  url:
    - '^https?://[^/]+\.grafana\.net'
credentials: gcx's own config (contexts from `gcx login`), never the repo
---
# gcx: Grafana Cloud

Use `gcx` for everything in Grafana Cloud: dashboards, datasources, alerting, SLOs, IRM, Synthetic
Monitoring, k6, Fleet, and queries against the stack's Prometheus/Mimir, Loki, Tempo and Pyroscope.
Don't reach the stack another way: no `curl` against `*.grafana.net`, no `mimirtool`, `logcli`,
`tempo-cli` or `profilecli`, no browser. If gcx can't do something, say so to the owner rather than
working around it.

## Before you start

- `gcx config current-context` names the stack you're about to touch. Say which one in your first
  reply that uses it.
- `gcx help-tree` prints the command tree compactly. `gcx <group> --help` explains a group.
- `gcx agent skills list` lists gcx's own task guides (`gcx agent skills get <name>` reads one).
  Prefer them to guessing flags.
- gcx detects an agent session and answers in JSON (`--agent`). A large answer is written to a
  spill file whose path it prints: read that file, don't rerun with bigger limits.

## Permissions

Reads (`list`, `get`, `query`, `search`, `status`, …) are allowed. Everything else asks: creating,
updating, deleting, `pull`/`push`, `gcx assistant prompt`, `gcx config view`, and `gcx api` (raw
HTTP, whatever the method). A coordinator lane can run only the reads.

Global flags go after the command path (`gcx dashboards list --context prod`), or the rule for that
command doesn't match and you'll be asked.

## Credentials

gcx keeps them in its own config (`gcx config path`), set up by the owner with `gcx login`. Never
print, copy or commit them. `gcx config view` and `--insecure-log-http-payload` can show secrets:
don't run them. If a command fails with an auth error, tell the owner; don't log in yourself.
