# agents-config

Personal AI agent configurations (Claude Code, Codex CLI, OpenCode, oh-my-pi) —
templates and shared data in the repo, rendered/linked into each agent's home
config by `install.py`.

## Quick start

```sh
make check     # validate renders without installing
make install   # idempotent; re-run after any change
```

Everything else — layout, install rules, per-agent specifics, repo conventions —
is documented in [AGENTS.md](AGENTS.md).
