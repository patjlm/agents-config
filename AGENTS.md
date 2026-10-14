# AGENTS.md

Personal repo: AI agent configurations + scripts to install them automatically.

## Supported agents

- Claude Code, Codex CLI, OpenCode.
- Pi (or others) may be added later. When adding one: new per-agent folder + installer entry; do not break existing agents' installs.

## Layout

- One folder per agent (`opencode/`, `claude/`, `codex/`): holds that agent's Jinja2 templates (`.j2`) and other agent-specific files.
- `agents/`: shared cross-agent content — `agents/AGENTS.md` is the global instructions file (`claude/CLAUDE.md` links to it; `~/.agents/AGENTS.md` is symlinked here at install).
- `mcp/servers.jsonc`: canonical shared MCP server list — single source of truth, rendered into every agent config. More shared data dirs may come (e.g. models).
- `claude/plugins/`: our own Claude Code marketplace (`patjlm-agents-config`, directory source) — currently holds the `mcp` plugin.
- `install.py`: renders templates → agent config files.

## Install rules (important)

- Two install mechanisms:
  - **Generated configs** (e.g. `~/.config/opencode/opencode.jsonc`): `uv run install.py` renders `.j2` templates. Generated files are never hand-edited — edit the template + shared data, re-run installer.
  - **Symlinks** for 1:1 content: `~/.agents/AGENTS.md` → repo `agents/AGENTS.md` (the rest of `~/.agents` is managed by skills tooling); `~/.claude` entries link into repo `claude/`.
- Never duplicate config content across agent dirs — shared data lives once in repo (`mcp/`, ...), agent templates only map it to each agent's format.
- Install must be idempotent and safe to re-run: skip unchanged files, back up before overwrite, never write through symlinks, don't clobber unrelated user files.
- Claude specifics:
  - 1:1 files (`CLAUDE.md`, `statusline-command.sh`, `agents/`, `commands/`, `hooks/`, `skills/`) are symlinked into `~/.claude`.
  - `claude/settings.jsonc` (comments allowed) is rendered to plain JSON and **copied** to `~/.claude/settings.json` — never symlinked, Claude Code itself writes to it (permissions, `/config`); a symlink would let those writes hit the repo. The `unchanged` check compares parsed JSON (insensitive to Claude Code's reformatting); any differing top-level keys are warned about before overwrite (backup kept) — merge such changes into the source to keep them.
  - Plugins: marketplaces (`extraKnownMarketplaces`) and plugins (`enabledPlugins`, `true` = install) both live in `claude/settings.json` — single source of truth; `install.py` applies them via the `claude` CLI (idempotent no-ops when present). The installer warns when a user-scope plugin is installed on the machine but missing from the repo config.
  - MCP: Claude Code can't read `mcp/servers.jsonc` (opencode format), so `install.py` translates it into `claude/plugins/mcp/.mcp.json` — our own plugin under the `patjlm-agents-config` marketplace. Per-server `"claude"` overrides in `mcp/servers.jsonc` handle differences (e.g. `exclude` for servers provided by other plugins). The rendered `.mcp.json` is gitignored (generated file). Per-server agent overrides: a per-agent key (e.g. `"claude"`) is deep-merged over the base server when rendering for that agent (dicts merge per-key, scalars/lists replace; `"exclude": true` skips the server). Base values use opencode syntax — `{file:PATH}` secrets are read by opencode at runtime; the claude rendering translates `{env:VAR}` to `${VAR}` and rejects `{file:...}` (claude can't read files), so override such values in the `"claude"` block with an env-var ref. Never inline secret contents. Directory-marketplace plugins load in place: edits apply on next session start or `/reload-plugins`.

## Python scripts

- All Python scripts run via `uv` with PEP 723 inline dependency headers (`# /// script`). No venvs, no pip install. Run as `uv run <script>.py`.

## Repo nature

- **Never commit sensitive info** (credentials, tokens, API keys, secrets). Configs may be shared/synced widely — assume anything here is public.
- Config files must not reference sensitive files inside this repo, even if gitignored. Reference them by absolute path outside the repo instead (`~` or `$HOME`, e.g. `~/.secrets/openai.key`).
- Config + scripts only (Python via uv, shell). No build, test, or lint toolchain.
- Verify changes by running `make check` (validate renders without installing), then `make install` (expect `unchanged:` on re-run), and exercise the affected agent.
- Upstream opencode config schema (opencode.ai/config.json) is V1-stale — rejects V2 keys (`permissions`, `providers`). Only syntax-check rendered opencode config; schema-check only formats we own (e.g. `mcp/servers.schema.json`). Revisit if upstream publishes V2 schema.
