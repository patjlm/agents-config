# Auto-mode permission classifier via EnMaaS (claude code 2.1.318)

Verified 2026-11-06 by reading claude code 2.1.318 (bundled JS) and with
controlled `-p` runs in auto mode; the 2.1.300 behaviour was observed on
2026-10-16 with a logging proxy. Version-specific — re-verify after claude
code upgrades.

## How it works

Client-side classifier (both stages send a "You are a security monitor for
autonomous AI coding agents" system prompt):

- Stage 1: classifier model, 64 max tokens, verdict only (block / no block).
- Stage 2: only when stage 1 blocks — **the same classifier model**, 8192
  max tokens, severity 0-100 + category. Not the session model.
- A call that would be allowed in acceptEdits mode is not classified at all
  (`Skipping auto mode classifier for Bash: would be allowed in acceptEdits
  mode`), so cost per command is below the old "2-6 calls" estimate.
- Budgets: stage 1 60s, +10s per 50k classifier tokens above 50k, capped at
  120s; 120s total per call; outer retries 2s → 10s.

## Which model stage 1 uses

Resolution order, first match wins:

1. GrowthBook `tengu_auto_mode_config.model` / `.modelByMainModel` — never
   present via EnMaaS.
2. `CLAUDE_CODE_AUTO_MODE_MODEL` — honored (2.1.312+). Dropped with a
   warning when the model is unavailable or policy-denied, or is
   `claude-sonnet-5-5`, `claude-opus-5-5`, `claude-opus-5`.
3. `ANTHROPIC_DEFAULT_SONNET_MODEL` — refused (one warning, then the default
   below) when it resolves to `claude-sonnet-5-5` or `claude-opus-5-5`;
   dropped when unavailable or policy-denied.
4. Default `claude-sonnet-5`, with `[1m]` appended whenever the session
   model is a 1M model.
5. Otherwise the session model.

Our config pins step 2 to `rits/deepseek-ai/DeepSeek-V4.1-Flash-1017[1m]`
while EnMaaS is on, so stage 1 requests that id; with the line commented out
the stage 1 request is `claude-sonnet-5[1m]` (session model `glm-5-3[1m]`,
`[1m]` re-appended).

Demotion: one stage-1 failure on the sonnet-5 default (any error kind except
`http_401`) sets `externalSonnet5Probe=demoted`, and the rest of the process
classifies with the session model. A model pinned through `env` or server
config gets no such fallback — its failure makes auto mode deny
(`fail closed`).

## Measured 2026-11-06 (EnMaaS, auto mode, classifier model per stage)

| setting | stage 1 | stage 2 | latency s1 / s2 |
| --- | --- | --- | --- |
| sonnet-5 default | `claude-sonnet-5[1m]` | `claude-sonnet-5[1m]` | 1.34s / 3.98s |
| `ANTHROPIC_DEFAULT_SONNET_MODEL=rits/zai-org/glm-5-3` | `...glm-5-3[1m]` | `...glm-5-3[1m]` | 15.9s / 8.7s |
| `CLAUDE_CODE_AUTO_MODE_MODEL=rits/zai-org/glm-5-3` | `...glm-5-3` | `...glm-5-3` | 15.6s / — |
| `CLAUDE_CODE_AUTO_MODE_MODEL=...DeepSeek-V4.1-Flash-1017` | `...-1017` | `...-1017` | 1.15s-2.79s (median 1.3s) / 5.15s |
| `CLAUDE_CODE_AUTO_MODE_MODEL=...DeepSeek-V4.1-Flash-1017[1m]` | `...-1017[1m]` | — | 1.18s |
| `CLAUDE_CODE_AUTO_MODE_MODEL=banana-model` | `banana-model` → error | — | 0.7s, denied |

Six DeepSeek allow-path runs all produced a clean stage-1 verdict (no extra
stage 2); the block-path run took 1.92s + 5.15s = 7.1s per decision, versus
5.5s on sonnet-5. `[claude-code:unrecognized_model] … query_source=auto_mode`
is printed to stderr for any id claude's catalog doesn't know: harmless.

The `banana-model` run is why the old "env var has no effect" note was
wrong: an unusable model is accepted into the request, fails, and auto mode
denies. Validate substitution tests against the request line
(`model=` in the debug log), never against the outcome.

## Knob guidance

- Don't set `ANTHROPIC_DEFAULT_SONNET_MODEL` for the classifier. It also
  repoints the sonnet alias (`--model sonnet`, subagent `model: sonnet`,
  the `/model` Sonnet entry), re-appends `[1m]` anyway, costs ~10x latency
  on GLM, and loses the demotion fallback.
- Pointing it at `claude-sonnet-5-5` is harmless for auto mode: the
  classifier refuses that model and keeps `claude-sonnet-5`.
- To keep classifier calls off the paid quota, pin the classifier with
  `CLAUDE_CODE_AUTO_MODE_MODEL`. This is the narrow knob: classifier only,
  no alias side effects, and it beats the sonnet-5 default on nothing but
  cost.
- Pin `rits/deepseek-ai/DeepSeek-V4.1-Flash-1017[1m]` (current setting):
  ~1.3s stage 1, i.e. sonnet-5 speed on the free model. GLM works but is
  12x slower (15.6-27.7s), so it is the fallback, not the pick.
- Use the `[1m]` form. A pinned id is passed verbatim — the automatic `[1m]`
  rewrite only applies to the sonnet-5 default — and the classifier prompt
  carries the whole transcript, so the base id risks an over-window error
  late in long sessions.
- Pinned means no demotion: an unavailable pinned model denies every checked
  action until it is back. Comment the line out to return to the billed
  sonnet-5 default, which degrades to the session model instead.
- Only inside the EnMaaS config branch. With `enmaas.enabled=false` (Vertex)
  the DeepSeek id doesn't exist, and a pinned classifier would fail closed.
- Unverified: whether `modelSettings.<model>.effortLevel` reaches the
  classifier request. The classifier request sets no effort field itself;
  our `modelSettings.claude-sonnet-5` entry governs main-loop requests.

## Env vars with NO effect on the classifier (2.1.318)

- `ANTHROPIC_DEFAULT_HAIKU_MODEL` — routes the haiku alias only (background
  tasks, session-title generation: `query_source=generate_session_title` in
  the debug log). `aux_model` in `config.jsonc` is exactly this.
- `CLAUDE_CODE_BG_CLASSIFIER_MODEL` — no effect on permission classification.

## CLAUDE_CODE_AUTO_MODE_SERVER

- `0` (ours): client-side classifier only. Verified 2026-11-06.
- `1`: verified on 2.1.318 against EnMaaS — requests carry the `safeguards`
  field, EnMaaS returns no verdict, and claude logs `[server-classifier] the
  platform gave no classification for this request (server_no_result); …
  auto mode classifies locally for the rest of this session`. Net result is
  the same client-side classifier, plus a round-trip and a user-facing
  notice. No benefit.
- Anthropic documents `0` for gateways that can't pass `safeguards` /
  `safeguard_results` through, and it keeps that notice away
  (code.claude.com/docs/en/auto-mode-classifier-billing). The server path
  would be *unbilled* if EnMaaS implemented the pass-through — the fix that
  would make auto mode free without pinning. The docs call the variable
  temporary: it may be removed in a later release.

## Cost

Classifier calls go wherever `ANTHROPIC_BASE_URL` points, and they cost the
same as any other call to that model. **On EnMaaS only GLM and DeepSeek are
free; Claude (and GPT/Gemini) draw on the user's quota exactly like the
Vertex path does.** So the default setup spends quota: `claude-sonnet-5`
classifies every command that auto mode does not skip, and each stage-1
prompt carries the ~138k-char monitor system prompt plus transcript (~35k
tokens, prompt caching requested).

Consequences:

- Hit the Claude quota and every classifier call fails; auto mode degrades
  to the session model (GLM, free) for the rest of the process via the
  demotion path, ~10x slower but working.
- With `enmaas.enabled=false` (Vertex) the same calls bill Vertex instead —
  and a session on `claude-sonnet-5-5` still classifies on
  `claude-sonnet-5`, since the 5.5 models are excluded.
- Pinning the classifier to a free EnMaaS model with
  `CLAUDE_CODE_AUTO_MODE_MODEL` is the quota-free option, and DeepSeek makes
  it latency-free too (~1.3s per decision).
- Claude 2.1.318 shows a notice when the server-side review isn't reaching
  the session: classifier requests "are billed as before" and the session is
  "not eligible" for the no-charge path. That is our case with EnMaaS.

## EnMaaS dependency

- 2026-10-16: every stage-1 call on `claude-sonnet-5[1m]` returned
  `http_404` (ENMAAS-57, external Anthropic upstream), so each process fell
  back to GLM on its first classifier call.
- 2026-11-06: `claude-sonnet-5[1m]` answers (1.3s-4.0s per call). The 404s
  may come back; the demotion path keeps auto mode working if they do.
- GLM as classifier works but is slow (15.9s stage 1, 8.7s stage 2), inside
  the 60s/120s budgets in every run we captured.
- If classifier stability matters for a session, prefer non-auto permission
  modes + allow rules (`/fewer-permission-prompts`).
