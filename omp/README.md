# omp (oh-my-pi)

Fork of pi with its own binary and `~/.omp/agent/` config dir. What the
installer manages vs. what omp owns is documented in the repo
[AGENTS.md](../AGENTS.md) (omp specifics) — this file holds operational
notes: how to install, verify, and what is proven vs. untested.

## Install (mise)

```sh
mise use -g github:can1357/oh-my-pi
```

Use the `github:` backend, **not** `ubi:` — ubi is deprecated and picks
the `omp-linux-musl-x64` release asset, which cannot execute on glibc
Fedora ("required file not found": its ELF interpreter is
`/lib/ld-musl-x86_64.so.1`). The `github:` backend picks the glibc
`omp-linux-x64` asset, whose binary is named `omp` (the musl one unpacks
as `oh-my-pi`). omp releases daily; `minimum_release_age` in mise usefully
lags that.

## Verifying the install

```sh
omp models list                 # enmaas-glm + enmaas-deepseek + enmaas-openai (+ openai-key) providers
omp config list | grep -E "modelRoles|advisor"   # default: enmaas-deepseek/rits/deepseek-ai/DeepSeek-V4.1-Flash-1017,
                                  # advisor: enmaas-glm/rits/zai-org/glm-5-3, advisor.enabled: true
omp -p "Reply with exactly: ok"     # live EnMaaS DeepSeek round-trip
```

The smoke test exercises the full chain: `modelRoles.default` routing,
the `!cat ~/.secrets/enmaas-key` request-time key read, and the model
entries in `omp/models.yml.j2`.

## Advisor mode

omp's advisor runtime pairs a second model (the `advisor` model role)
that passively reviews each turn and injects notes. `install.py` wires
DeepSeek as the main model with GLM as the advisor (`advisor.enabled:
true`) — both free on EnMaaS, so the extra per-turn review costs
latency only. Toggle per session with `--advisor`/`--no-advisor` or
`/advisor` in the TUI. When `enmaas.enabled=false` the advisor role is
dropped (the single openai-key fallback would only review itself).

## Proven vs. untested

- **Proven live**: GLM 5.3 via `enmaas-glm` (completions API,
  `supportsDeveloperRole: false`, zeroed penalties — colleague-proven
  flags, verified with a real completion). DeepSeek V4.1 Flash via
  `enmaas-deepseek` (completions API — developer role, penalties, image
  input, and function tools all verified directly against the API; no
  compat flags needed, unlike GLM).
- **Untested**: the `enmaas-openai` gpt models (luna/sol/astra over
  `openai-responses`) and the `openai-key` fallback provider (direct
  OpenAI, used only when `enmaas.enabled=false`). Entries were
  constructed from our opencode/codex limits, not from a working config.

## MCP notes

- Secrets use omp's `!command` form, resolved at connect time:
  `{file:PATH}` → `!cat PATH`, `{env:VAR}` → `!printenv VAR`,
  `Bearer {file:...}` headers → `!echo "Bearer $(cat ...)"`. In YAML
  (`models.yml`) such values must be quoted — a leading `!` parses as a
  YAML tag otherwise.
- `atlassian` needs an OAuth login (`omp login`) or an
  `"omp": {"exclude": true}` override in `mcp/servers.jsonc` — without
  auth it fails with HTTP 401. (claude/codex both exclude it.)
- `slack-mcp` env values come from `!printenv`, so the `SLACK_MCP_*`
  variables must exist in the shell that launches omp.
- `omp` reads no pi (`~/.pi`) config; without `~/.omp/agent/AGENTS.md`
  it falls back to `~/.claude/CLAUDE.md` — the installer generates the
  omp one to avoid that.
