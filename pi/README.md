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

- Managed install in `~/.pi/agent/install`: one release directory per
  version, `install/current-version` selects the active one, and the
  launcher `~/.pi/agent/bin/pi` is symlinked from `~/.local/bin/pi`.
  Active release here: **1.4.0**. Self-update with `pi update`; the
  official installer pins dependencies via npm-shrinkwrap and can migrate
  a legacy npm-global install (`curl -fsSL https://pi.dev/install.sh | sh`).
  Node 22.19+ (this machine runs v26 via mise; the installer's own
  `~/.local/share/pi-node` directory is absent).
- The pre-repo install was npm-global `@earendil-works/pi-coding-agent`
  0.84.3 (the older `@mariozechner/pi-coding-agent` name is deprecated).
  Everything in this repo targets 1.x: built-in MCP (`mcp.json`), model
  `compat`/`samplingParams`, `--skill`/`--exclude-tools`, and the harness
  launcher. 0.84.x predates built-in MCP and ignores those model keys.

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

## Harnesses

Task-scoped pi configs (currently Slack), described in `harnesses/<name>/`
and rendered to `~/.pi/harnesses/<name>/` by `install.py` (rules in the repo
[AGENTS.md](../AGENTS.md)). `PI_CODING_AGENT_DIR` selects the whole config
dir (pi 1.x), so settings, MCP servers, models, instructions and sessions are
all per-harness.

One setup, two entry points:

```sh
~/.pi/harnesses/slack/pi -p "summarize #team"   # one-off
```

- T3 Code: Settings → Providers → pi → add an instance (`pi-slack`, display
  name "Pi (Slack)") with **binary path** `~/.pi/harnesses/slack/pi`. T3
  accepts any executable there and the instance gets its own model-picker
  entry; a project override can enable it for one project only.
- The launcher locates its own directory, sets `PI_CODING_AGENT_DIR` and
  `NODE_EXTRA_CA_CERTS` (T3 does not run the `~/.bashrc.d/pi.rc` function),
  and passes the flags (`--no-skills --exclude-tools …`).

Slack prerequisites, outside the repo and never committed:

| file | mapped to |
|------|-----------|
| `~/.secrets/slack-xoxc` | `SLACK_XOXC_TOKEN` |
| `~/.secrets/slack-xoxd` | `SLACK_XOXD_TOKEN` |

They are read at MCP connect time (`{file:…}` → `!cat`), so neither a CLI nor
a T3 launch needs exported variables. Missing files fail softly: the value is
empty and Slack answers `invalid_auth`.

The harness owns the `slack-mcp` definition (`harnesses/slack/harness.jsonc`)
instead of referencing `mcp/servers.jsonc`, so no regular agent renders it.

`SLACK_MCP_WORKSPACE` is not read by the server (the image's script and the
upstream docs agree); the harness only uses the workspace domain for
permalinks, written into its `AGENTS.md`.

`LOGS_CHANNEL_ID` is left unset: the current image (2026-10-26) writes audit
lines to stderr instead of posting a log message per tool call. The previous
image (2026-02-03) required the variable and posted to a channel, which is
why the harness used to pin it to `""`; that is gone now.

`SLACK_MCP_READ_ONLY` is a launcher passthrough (`env: null` in
`harness.jsonc`): set it to `1` in the caller's environment and the server
does not register write tools. It cannot be set per T3 thread (T3 has no
per-thread env), but `SLACK_MCP_READ_ONLY=1 ~/.pi/harnesses/slack/pi …` works.
Do not put it in the MCP `environment` as `{env:…}`: pi renders that to
`!printenv`, which fails and stops the server when the variable is unset.

Images: the Slack server has no attachment tool, so `slack.py image <channel>
<ts> [--index N]` downloads the attachment to `/tmp/slack-image-*` with the
xoxc/xoxd tokens; the agent `read`s that path, pi sends images as attachments,
and the DeepSeek EnMaaS model accepts them.

### Helper and approvals

`harnesses/slack/slack.py` (symlinked into the config dir; stdlib only, run
through `uv` via its PEP 723 shebang — no pip install) is the read-only
lookup the prompt tells the agent to use: `whoami`, `channel`, `image`,
`search`, `mentions`, `activity`, `history`, `replies`, printing
`ISO | channel (id) | @user | text | permalink` plus `[files: …]`.
`mentions` searches `<@user-id>` (name search misses the `@Patrick Martin`
form, and GitHub reminders count); `activity` lists threads the user is in,
with who spoke last and whether it needs a reply. It exists because the MCP
returns compact strings, `whoami` gives no user id, and this Enterprise Grid
token rejects `users.conversations` and `chat.getPermalink`
(`enterprise_is_restricted`) — channel names come from `conversations.info`
or an `in:#name` search instead. Identity (`patmarti`, `U029NSDRB62`, the
workspace URL) is in `AGENTS.md`, so no discovery round. Unread DMs are the
MCP's `get_unread_direct_messages` (browser-session only, works here).

Approvals are T3's by default, but the harness owns the policy: T3 injects an
extension whose `tool_call` hook prompts for every MCP/`bash`/`codemode` call
unless the runtime mode is `full-access` (pi itself never asks; its settings
have no permission keys). The launcher sets
`T3_PI_RUNTIME_MODE=full-access` (via `env` in `harness.jsonc`) to silence
that blanket behaviour, and `guard.ts` decides what actually needs approval
(below). T3 still shows the thread's own mode; remove the `env` entry to
restore T3's policy instead.

### Guard

`harnesses/slack/guard.ts` is linked into the config dir and loaded with
`--extension`. It is the boundary behind the full-access override:

- `bash`: only `~/.pi/harnesses/slack/slack.py`, with no shell chaining.
- `read`: only `/tmp/slack-image-*`.
- `edit` / `write`: blocked.
- Slack MCP tools: `readOnlyHint` tools pass; write tools need approval.
- `codemode` is allowed — it is a QuickJS sandbox with no fs/net, and every
  nested tool call hits the same `tool_call` hook and is checked.
- `tool_search` passes; anything else needs approval. With no UI (print/JSON
  modes) such calls are blocked instead, except Slack MCP write tools when
  `SLACK_HARNESS_ALLOW_WRITES=1` is set.
