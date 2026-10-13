# AGENTS.md

Personal repo: AI agent configurations + scripts to install them automatically.

## Supported agents

- Claude Code, Codex CLI, OpenCode.
- Pi (or others) may be added later. When adding one: new per-agent folder + installer entry; do not break existing agents' installs.

## Layout

- One folder per agent (`opencode/`, later `claude/`, `codex/`): holds that agent's Jinja2 templates (`.j2`) and other agent-specific files.
- `mcp/servers.json`: canonical shared MCP server list — single source of truth, rendered into every agent config. More shared data dirs may come (e.g. models).
- `install.py`: renders templates → agent config files.

## Install rules (important)

- Two install mechanisms:
  - **Generated configs** (e.g. `~/.config/opencode/opencode.jsonc`): `uv run install.py` renders `.j2` templates. Generated files are never hand-edited — edit the template + shared data, re-run installer.
  - **Symlinks** for 1:1 content (e.g. skills later): `~/.agents` links to repo's shared dir; agent-specific locations link into `~/.agents`.
- Never duplicate config content across agent dirs — shared data lives once in repo (`mcp/`, ...), agent templates only map it to each agent's format.
- Install must be idempotent and safe to re-run: skip unchanged files, back up before overwrite, never write through symlinks, don't clobber unrelated user files.

## Python scripts

- All Python scripts run via `uv` with PEP 723 inline dependency headers (`# /// script`). No venvs, no pip install. Run as `uv run <script>.py`.

## Repo nature

- **Never commit sensitive info** (credentials, tokens, API keys, secrets). Configs may be shared/synced widely — assume anything here is public.
- Config files must not reference sensitive files inside this repo, even if gitignored. Reference them by absolute path outside the repo instead (`~` or `$HOME`, e.g. `~/.secrets/openai.key`).
- Config + scripts only (Python via uv, shell). No build, test, or lint toolchain.
- Verify changes by running `make check` (validate renders without installing), then `make install` (expect `unchanged:` on re-run), and exercise the affected agent.
- Upstream opencode config schema (opencode.ai/config.json) is V1-stale — rejects V2 keys (`permissions`, `providers`). Only syntax-check rendered opencode config; schema-check only formats we own (e.g. `mcp/servers.schema.json`). Revisit if upstream publishes V2 schema.
