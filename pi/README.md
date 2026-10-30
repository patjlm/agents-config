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

## Corporate CA trust (internal HTTPS endpoints)

pi's Node runtime ships the Mozilla root store only, so internal endpoints
signed by the Red Hat corporate CA fail with
`SELF_SIGNED_CERT_IN_CHAIN` — e.g. the Google Workspace MCP server
(`https://gworkspace.corp.redhat.com/mcp`, in `mcp/servers.jsonc`). curl
and omp are unaffected (system store / native TLS).

pi has no setting for extra CAs: its settings schema exposes only
`httpProxy`, and `pi mcp` shell commands explicitly do not load
extensions, so an in-process `tls.setDefaultCACertificates` extension
cannot cover `pi mcp login`/`list`. The trust is injected per invocation
from a shell drop-in **outside this repo** (the installer never touches
shell rc files — recreate it on a new machine):

```sh
# ~/.bashrc.d/pi.rc
pi() {
  NODE_EXTRA_CA_CERTS=/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem command pi "$@"
}
```

- Scoped to pi processes only; every other Node app keeps the bundled store.
- Interactive bash only (`~/.bashrc` sources `~/.bashrc.d/*`); other launch
  paths (scripts, GUI) need their own wrapper.
- Verified alternative: `NODE_USE_SYSTEM_CA=1` (Node >= 24.6) adds the OS
  store without hardcoding a path — on Fedora `/etc/ssl/cert.pem` symlinks
  to the same bundle. Swap it into the function if the path changes.
- Calling `pi` outside the function (e.g. `env pi …`) still shows
  `fetch failed`.

Verify: `pi mcp list` → `google-workspace: connected, 101 tools`.

## enmaas-anthropic provider quirks

Claude models over EnMaaS's anthropic-compatible endpoint (`api:
anthropic-messages`, baseUrl without `/v1` — the SDK appends
`/v1/messages`). The gateway validates with an older Anthropic schema:

- Message-level `output_config` is rejected (`messages.N.output_config:
  Extra inputs are not permitted`). pi only emits it when
  `compat.supportsMidConvoEffort` is true, which pi's built-in catalog
  sets for the Claude 5.5 models — so overriding the built-in
  `anthropic` provider breaks; a custom provider defaults it to false.
  The template pins `supportsMidConvoEffort: false` explicitly.
- A body-level `betas` field is rejected. pi sends it for
  budget-thinking models (interleaved thinking). All three Claude 5.5
  models are adaptive: they set `forceAdaptiveThinking: true`, which
  skips the interleaved beta. Budget thinking is also rejected directly
  (`thinking.type.enabled` unsupported for haiku-5-5 — use adaptive plus
  top-level `output_config.effort`).
- Accepted and verified live: adaptive thinking plus top-level
  `output_config.effort`, streaming, tool definitions, and the catalog
  token caps (1M context / 128K output on all three 5.5 models).
- Model ids are the plain Anthropic ones; they don't appear in
  `GET /v1/models` but the anthropic endpoint serves them.

## vertex provider quirks

- `@lhl/pi-vertex` 1.1.9 (May 2026, latest) predates pi 1.x and fails to
  register ("baseUrl is required when defining custom models" — issue
  #2, fix proposed but unreleased). `install.py` patches the installed
  copy in `~/.pi/agent/npm/node_modules/@lhl/pi-vertex/` idempotently;
  drop the patch when upstream releases a fix.
- The enmaas-off fallback model is `vertex/claude-opus-4-6`, not the
  pre-repo `claude-haiku-4-5`: vertex rejects adaptive thinking on
  haiku-4-5, and pi's `modelOverrides` cannot reach extension-registered
  models to disable thinking for it (only built-in catalog models).

## Verifying

```sh
pi --list-models enmaas          # enmaas-glm/deepseek/openai/anthropic (+ litellm)
pi -p "Reply with exactly: ok"   # live EnMaaS GLM round-trip
# anthropic provider (one per model family):
pi -p --no-session --provider enmaas-anthropic --model claude-opus-5-5 -- "Reply with exactly: ok"
pi -p --no-session --provider enmaas-anthropic --model claude-sonnet-5-5 -- "Reply with exactly: ok"
pi -p --no-session --provider enmaas-anthropic --model claude-haiku-5-5 -- "Reply with exactly: ok"
```

The smoke test exercises `defaultProvider`/`defaultModel` routing and
the `!cat ~/.secrets/enmaas-key` request-time key read. Note: unlike
omp, a bare apiKey string in pi is a literal — pi env interpolation is
`$NAME`/`${NAME}`, which we don't use (command form only).
