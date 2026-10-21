# AGENTS.md

Personal repo: AI agent configurations + scripts to install them automatically.

## Supported agents

- Claude Code, Codex CLI, OpenCode, oh-my-pi (`omp` — a fork of pi with its own binary and `~/.omp` config dir), and pi itself (`~/.pi/agent`).
- When adding another: new per-agent folder + installer entry; do not break existing agents' installs.

## Layout

- One folder per agent (`opencode/`, `claude/`, `codex/`, `omp/`, `pi/`): holds that agent's Jinja2 templates (`.j2`) and other agent-specific files.
- `agents/`: shared cross-agent content — `agents/AGENTS.md` holds the common guidelines; each agent folder may add `<agent>/AGENTS.md` with agent-specific rules. `install.py` combines both into each agent's global instructions file (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.config/opencode/AGENTS.md`); `~/.agents/AGENTS.md` symlinks to the common file. Keep both files lean — they load into every session's context. Agent-specific rules never go in `agents/AGENTS.md`.
- `mcp/servers.jsonc`: canonical shared MCP server list — single source of truth, rendered into every agent config. More shared data dirs may come (e.g. models).
- `config.jsonc`: repo-wide settings (`enmaas.enabled`, `enmaas.rely_on_glm`), schema-checked against `config.schema.json` and rendered into every agent config — flip + re-run install to switch agents between EnMaaS and their fallbacks (claude: Vertex via gcloud ADC; opencode: openai; codex: default OpenAI auth). `rely_on_glm` additionally routes claude code's cheap auxiliary usage (haiku alias + auto-mode classifier) to GLM — flip it off when GLM is unavailable but EnMaaS still serves the claude models.
- `claude/plugins/`: our own Claude Code marketplace (`patjlm-agents-config`, directory source) — currently holds the `mcp` plugin.
- `install.py`: renders templates → agent config files. Structure: shared primitives, shared data, then one section per agent, listed in `INSTALLERS` — adding an agent = new folder + templates + one section + one list entry. Agent *formats* live in the agent's Jinja templates; agent *behavior* (symlinks, CLI calls, drift checks) in its `install.py` section. One agent failing does not abort the others.

## Install rules (important)

- Two install mechanisms:
  - **Generated configs** (e.g. `~/.config/opencode/opencode.jsonc`, `~/.claude/settings.json`): `uv run install.py` renders `.j2` templates. Generated files are never hand-edited — edit the template + shared data, re-run installer.
  - **Symlinks** for 1:1 content: `~/.agents/AGENTS.md` → repo `agents/AGENTS.md` (the rest of `~/.agents` is managed by skills tooling); `~/.claude` entries link into repo `claude/`.
- Never duplicate config content across agent dirs — shared data lives once in repo (`mcp/`, ...), agent templates only map it to each agent's format.
- Install must be idempotent and safe to re-run: skip unchanged files, back up before overwrite, never write through symlinks, don't clobber unrelated user files.
- Claude specifics:
  - 1:1 files (`statusline-command.sh`, `agents/`, `commands/`, `hooks/`, `skills/`) are symlinked into `~/.claude`; `~/.claude/CLAUDE.md` is generated (common + `claude/AGENTS.md`).
  - `claude/settings.jsonc.j2` (comments + `{{ home }}`/`{{ repo }}` substitution) is rendered to plain JSON and **copied** to `~/.claude/settings.json` — never symlinked, Claude Code itself writes to it (permissions, `/config`); a symlink would let those writes hit the repo. The `unchanged` check compares parsed JSON (insensitive to Claude Code's reformatting); any differing top-level keys are warned about before overwrite (backup kept) — merge such changes into the source to keep them.
  - Plugins: marketplaces (`extraKnownMarketplaces`) and plugins (`enabledPlugins`, `true` = install) both live in `claude/settings.jsonc.j2` — single source of truth; `install.py` applies them via the `claude` CLI (idempotent no-ops when present, skipped with a warning when the CLI is missing). The installer warns when a user-scope plugin is installed on the machine but missing from the repo config.
  - MCP: Claude Code can't read `mcp/servers.jsonc` (opencode format), so `install.py` translates it into `claude/plugins/mcp/.mcp.json` — our own plugin under the `patjlm-agents-config` marketplace. Per-server `"claude"` overrides in `mcp/servers.jsonc` handle differences (e.g. `exclude` for servers provided by other plugins). The rendered `.mcp.json` is gitignored (generated file). Per-server agent overrides: a per-agent key (e.g. `"claude"`) is deep-merged over the base server when rendering for that agent (dicts merge per-key, scalars/lists replace; `"exclude": true` skips the server). Base values use opencode syntax — `{file:PATH}` secrets are read by opencode at runtime; the claude rendering translates `{env:VAR}` to `${VAR}` and rejects `{file:...}` (claude can't read files), so override such values in the `"claude"` block with an env-var ref. Never inline secret contents. Directory-marketplace plugins load in place: edits apply on next session start or `/reload-plugins`.
- Codex specifics:
  - `codex/config.toml.j2` renders to `~/.codex/config.toml` — drift-aware like claude settings (`codex mcp add` writes there); `~/.codex/AGENTS.md` is generated (common + `codex/AGENTS.md`); enmaas model profiles render as `~/.codex/<name>.config.toml` (`codex --profile <name>`).
  - MCP: stdio servers translate natively (`cwd` is a codex key); `{env:VAR}` env entries become `env_vars` forwarded from codex's environment (renamed vars get a bash wrapper); http servers need a `"codex": {"bearer_token_env_var": ...}` override — codex can't read `{file:...}` secrets either.
  - EnMaaS: codex only supports the responses wire API (`/v1/responses`); the GLM default model assumes EnMaaS serves it over responses (the gpt-* profiles use the same endpoint). API key: read from the secret file via the provider `auth` command (`cat` + absolute path — codex has no file references or env expansion in config; MCP secrets are env-only via `env_vars`/`bearer_token_env_var`). `features.fast_mode = false` while enmaas is enabled — EnMaaS rejects `service_tier=priority` ("Fast").
  - `/model` picker: codex's catalog doesn't know GLM and `model_catalog_json` *replaces* it, so with enmaas enabled `install.py` generates `~/.codex/model-catalog.json` (GLM + the gpt-* profile models, no Fast tiers) from the catalog bundled in the codex binary (`codex debug models --bundled`; skipped with a warning if codex is missing). The GLM entry keeps only codex's required fields — copying GPT-specific capability flags (`supports_search_tool`, freeform `apply_patch`, …) breaks GLM's vLLM backend. If a codex upgrade changes the schema, re-run install; if generation fails the picker just loses GLM.
  - Reasoning effort: GLM runs at `high` (set in `config.toml`, and the only level its catalog entry offers — codex doesn't apply catalog defaults on its own). Profile files layer over `config.toml`, so each gpt profile pins its model's own default effort (`CODEX_PROFILES` in `install.py`) instead of inheriting GLM's.
- omp specifics:
  - `omp/models.yml.j2` renders to `~/.omp/agent/models.yml` — EnMaaS providers under their own IDs (built-in `openai`/`anthropic` stay unchanged). The API key is read from the secret file at request time via the command form (`!cat` — quoted, a leading `!` would otherwise parse as a YAML tag); no env vars. `mcp/servers.jsonc` translates to `~/.omp/agent/mcp.json`: `{file:PATH}` → `!cat PATH`, `{env:VAR}` → `!printenv VAR` (omp runs `!` values as shell commands at connect time; `Bearer {file:...}`-style headers become `!echo "Bearer $(cat ...)"`).
  - `~/.omp/agent/AGENTS.md` is generated (common guidelines) — without it omp falls back to reading `~/.claude/CLAUDE.md` (foreign-config discovery).
  - `~/.omp/agent/config.yml` is omp-owned (`/settings`, `omp config set` write there) — never rendered wholesale; `install.py` only sets `modelRoles.default` surgically (EnMaaS GLM when enabled, the `openai-key` fallback provider when disabled — same key file and default model as opencode's fallback), preserving omp's own settings.
- pi specifics:
  - `pi/models.json.j2` renders to `~/.pi/agent/models.json` — EnMaaS providers (same set as omp) plus the litellm local proxy carried over from the pre-repo setup; apiKey via `!cat` at request time (unlike omp, a bare apiKey value in pi is a literal, not an env-var name). `mcp/servers.jsonc` translates to `~/.pi/agent/mcp.json` via the shared pi-family `!command` translation (needs pi 1.x — built-in MCP).
  - `pi/settings.json.j2` renders to `~/.pi/agent/settings.json`, drift-aware like claude settings (pi writes theme, packages, changelog version there). `defaultProvider`/`defaultModel` switch with `enmaas.enabled`; the disabled fallback is the vertex provider (pi-vertex extension, gcloud ADC) with `claude-opus-4-6` — haiku-4-5 rejects adaptive thinking on vertex, and `modelOverrides` cannot reach extension-registered models to disable it.
  - `@lhl/pi-vertex` 1.1.9 (latest, May 2026) predates pi 1.x — `install.py` patches the installed copy in `~/.pi/agent/npm/` with the fix proposed in its issue #2 (unreleased upstream); drop the patch when a fixed version ships.
  - `pi/extensions/` and `pi/settings/` (pi-vertex) hold the addons gathered from the pre-repo setup, symlinked 1:1 into `~/.pi/agent/` (individually, so runtime-written `*.config.json` files next to the extensions stay unmanaged). Extension backups go under `~/.pi/agent/backups/` — a `.bak` inside `extensions/` would be discovered as an extension.
  - The installer never touches `~/.pi/agent/{auth.json,sessions,run-history.jsonl,models-store.json,bin,npm}` — pi-owned/user data.

## Python scripts

- All Python scripts run via `uv` with PEP 723 inline dependency headers (`# /// script`). No venvs, no pip install. Run as `uv run <script>.py`.

## Repo nature

- **Never commit sensitive info** (credentials, tokens, API keys, secrets). Configs may be shared/synced widely — assume anything here is public.
- Config files must not reference sensitive files inside this repo, even if gitignored. Reference them by absolute path outside the repo instead (`~` or `$HOME`, e.g. `~/.secrets/openai.key`).
- Config + scripts only (Python via uv, shell). No build, test, or lint toolchain.
- Verify changes by running `make check` (validate renders without installing), then `make install` (expect `unchanged:` on re-run), and exercise the affected agent.
- Upstream opencode config schema (opencode.ai/config.json) is V1-stale — rejects V2 keys (`permissions`, `providers`). Only syntax-check rendered opencode config; schema-check only formats we own (e.g. `mcp/servers.schema.json`). Revisit if upstream publishes V2 schema.
