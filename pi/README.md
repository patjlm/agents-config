# pi

Minimal coding agent (omp is its fork — separate binary, `~/.omp` config
dir, no config sharing). What the installer manages is documented in the
repo [AGENTS.md](../AGENTS.md) (pi specifics); this file holds
operational notes.

## Gathered from the pre-repo setup

The repo absorbed the previous hand-maintained `~/.pi/agent`:

- `models.json`: the litellm local-proxy provider (key at
  `~/.secrets/litellm-key`, like every other secret — the old setup read
  it through a `~/.pi/agent/.litellm-key` symlink into
  `~/.config/opencode`, which had been dangling since that file
  disappeared).
- `extensions/`: handoff, herdr-agent-state, question, questionnaire,
  status-line, todo (+ `.config.example.json` files and docs), linked
  1:1. The runtime-written `*.config.json` files next to them (e.g.
  `todo.config.json`) stay unmanaged.
- `settings/pi-vertex.json`: pi-vertex extension settings (GCP
  project/location), linked 1:1.
- `settings.json`: default model/thinking, packages
  (`npm:@lhl/pi-vertex`, `npm:pi-subagents`), theme — now rendered from
  `pi/settings.json.j2`, drift-aware.

Untouched by the installer: `auth.json`, `sessions/`, `bin/`, `npm/`
(package installs), `models-store.json`, `run-history.jsonl`.

## Versions

- Installed binary was npm-global `@earendil-works/pi-coding-agent`
  0.84.3 (old line); upstream is 1.x. The old name
  `@mariozechner/pi-coding-agent` is deprecated.
- Recommended install: the official installer pins dependencies via
  npm-shrinkwrap and self-updates with `pi update`:
  `curl -fsSL https://pi.dev/install.sh | sh` (it also offers to migrate
  a legacy npm-global install). Plain npm works but does not pin
  transitive deps. Node 22.19+ required.
- `mcp.json` (rendered from `mcp/servers.jsonc`, shared pi-family
  translation) needs pi 1.x — 0.84.x predates built-in MCP and ignores
  it.
- On 0.84.x the `compat`/`samplingParams` keys in models.json are
  ignored (GLM still worked in a live smoke test); 1.x applies them.

## Verifying

```sh
pi --list-models enmaas          # enmaas-glm + enmaas-openai (+ litellm)
pi -p "Reply with exactly: ok"   # live EnMaaS GLM round-trip
```

The smoke test exercises `defaultProvider`/`defaultModel` routing and
the `!cat ~/.secrets/enmaas-key` request-time key read. Note: unlike
omp, a bare apiKey string in pi is a literal — pi env interpolation is
`$NAME`/`${NAME}`, which we don't use (command form only).
