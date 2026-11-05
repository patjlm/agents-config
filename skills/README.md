# Shared skills

Install and provenance rules: see [`../AGENTS.md`](../AGENTS.md) and
[`SOURCES.md`](SOURCES.md).

## Caveman-derived skills

Default reply style is the static `# Communication style` section in
[`../agents/AGENTS.md`](../agents/AGENTS.md). The caveman plugin is disabled, so
these skills are optional and only run when invoked. All of them are prompt-only
or self-contained; none needs the plugin, a hook, or the `caveman` CLI.

| Skill | What it does |
|---|---|
| `caveman` | Terse reply mode with levels (`lite`, `full`, `ultra`, `wenyan*`). Vendored copy predates upstream v4 naming (`caveman`/`ultracave`/`megacave`) |
| `caveman-commit` | Conventional-commit message compressed to intent |
| `caveman-review` | Diff review, one line per finding |
| `caveman-compress` | Compress a memory file; Python scripts ship in the skill dir |
| `caveman-help` | Quick reference card |
| `caveman-explore` | Read-only explorer, `model: haiku`, citations only |
| `investigate-first`, `lean-build`, `migration`, `safe-refactor`, `surgical-patch`, `verify-and-stop` | Workflow skills; unrelated to the caveman runtime |

These are candidates for removal. Drop one when it is unused, when its upstream
diverges from the vendored copy, or when the runtime it assumes is gone. Delete
the directory and its `SOURCES.md` row in the same change; `install.py` prunes
the stale symlinks.

## Dropped 2026-11-05

Removed with the caveman plugin (`caveman@caveman` disabled in
`claude/settings.jsonc.j2`):

- `cavecrew`: delegated to plugin subagents that no longer exist.
- `caveman-stats`: driven by the plugin's mode-tracker hook.
- `caveman-discover`, `caveman-evidence-review`, `caveman-learn`,
  `caveman-manage`, `caveman-optimize`, `caveman-setup`: need the Caveman Cloud
  `caveman` CLI, which is not installed.
