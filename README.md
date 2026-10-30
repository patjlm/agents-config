# agents-config

Personal AI agent configurations (Claude Code, Codex CLI, OpenCode, oh-my-pi, pi) —
templates and shared data in the repo, rendered/linked into each agent's home
config by `install.py`.

## Quick start

```sh
make check     # validate renders without installing
make plan      # same, plus a diff of every file that would change
make install   # idempotent; re-run after any change
make check AGENT=pi   # single agent/section, same for plan/install
                      # (claude/codex/opencode/omp/pi/harnesses)
```

Everything else — layout, install rules, per-agent specifics, repo conventions —
is documented in [AGENTS.md](AGENTS.md).
