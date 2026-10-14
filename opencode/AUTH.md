# OpenCode auth store

OpenCode keeps provider credentials in two places, both outside this repo:
nothing here renders, installs, or symlinks them. Entries are created
interactively and never committed (this repo must stay secret-free; see root
AGENTS.md).

## 1. SQLite credential DB — `~/.local/share/opencode/opencode.db` (live store, V2)

- Written by `opencode auth login` / `/connect`; read at every startup.
- `opencode auth logout <integration>` removes an entry; `opencode auth list`
  shows them. Don't SQL-edit by hand.
- Legacy `~/.local/share/opencode/auth.json` was a V1 store, one-time-imported
  into the DB by migration `20260805200742_import_legacy_credentials`. Deleted
  2026-10-14; do not recreate it.

Current entries (as of 2026-10-14):

| Entry | Value | Effect |
|---|---|---|
| `google-vertex-anthropic` | dummy API key (`none`) | Kept deliberately: satisfies the auth requirement for `google-vertex/claude-*`. Real auth is gcloud ADC; the dummy key is never a working credential. Verified working. |
| `mcp_...` | OAuth | Atlassian MCP (remote server auth, not a model provider). |

## 2. Config-file keys — `opencode.jsonc` `providers.<id>.settings.apiKey`

Providers whose key lives in a secret file, referenced via `{file:...}`
substitution (config-only feature; the credential DB does not support it):

- `openai` → `~/.secrets/openai-api-key`
- `google` → `~/.secrets/gemini-api-key` — overrides the built-in Google
  provider; catalog models (`google/*`) stay, auth comes from the file.
  The DB credential for `google` was removed 2026-10-14.
- `enmaas-*` → `~/.secrets/enmaas-key` (routed via api.enmaas.devshift.net,
  billed to EnMaaS, not GCP).

## Distinct Google model sources (disambiguation)

- `google/*` — built-in provider, auth via `~/.secrets/gemini-api-key`
  (config block). Billing: the GCP project the AI Studio key belongs to.
- `google-vertex/*` — provider block in `opencode.jsonc` (project
  `itpc-ca-0ae3c7024b`, location `global`). Gemini/partner models via gcloud
  ADC (active account `patmarti@redhat.com`); Claude models via ADC plus the
  dummy DB credential above. Billing: `itpc-ca-0ae3c7024b`. Note:
  `gemini-4-argon` blocked by org policy `constraints/vertexai.allowedModels`
  as of 2026-10-14; `gemini-3.8-flash` works.
- `enmaas-google/gemini-3.8-flash` — EnMaaS proxy, billed to EnMaaS.

Revisit the tables when credentials change (`opencode auth login`/`logout`).
