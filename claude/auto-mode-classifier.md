# Auto-mode permission classifier via EnMaaS (claude code 2.1.300)

Verified 2026-10-16 with a logging proxy between claude and EnMaaS, plus
banana-model substitution tests for the env vars. Version-specific —
re-verify after claude code upgrades.

## How it works

Two-stage client-side classifier (both stages send a "You are a security
monitor for autonomous AI coding agents" system prompt):

- Stage 1: hardcoded `claude-sonnet-5` (64 max tokens) — model comes from
  `tengu_auto_mode_config` (Statsig config, hardcoded fallback; no server
  config exists via EnMaaS). In 1M-context sessions the `[1m]` variant is
  requested instead.
- Stage 2: the session model (GLM for us, 2112 max tokens).
- Stage-1 failure (404 or 403) falls back to stage 2, so single stage-1
  failures are survivable. Hard "X is temporarily unavailable" errors mean
  both stages failed — stage-2 GLM likely killed by burst load (each Bash
  command fires 2-6 classifier calls, T3 fires them in parallel).

## Env vars with NO effect on the classifier (2.1.300)

- `CLAUDE_CODE_AUTO_MODE_MODEL` — dead; removed from our settings
- `CLAUDE_CODE_BG_CLASSIFIER_MODEL` — dead for permission classification
- `ANTHROPIC_DEFAULT_HAIKU_MODEL` — only routes the haiku alias for
  background tasks; the classifier never consults it

## CLAUDE_CODE_AUTO_MODE_SERVER

- `0` (ours): client-side classifier only. Verified correct.
- `1`: main requests carry `safeguards` / `context_management` /
  `output_config`; EnMaaS/GLM ignores them → claude falls back to the same
  client-side classifier. No benefit on EnMaaS.

## EnMaaS dependency

- Stage 1 requires `claude-sonnet-5` on EnMaaS: intermittent 404s
  (ENMAAS-57 — external Anthropic upstream). `claude-sonnet-5[1m]` is
  always rejected (403 access policy), which is what long 1M-context
  sessions request.
- Until EnMaaS fixes this, auto mode on EnMaaS+GLM only works as well as
  the GLM stage-2 fallback holds up. Prefer non-auto permission modes +
  allow rules (`/fewer-permission-prompts`) meanwhile.
