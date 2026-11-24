# chai-acp

A tiny ACP agent that fronts the Chai Bot MCP persona (`ask_persona`), so T3 Code can run
Chai Bot as its own provider with threads, projects and history.

One file, `chai_acp.py`, run by `uv` with inline dependencies. No build, no install step.

## How Chai Bot answers

`ask_persona` is one MCP `tools/call` whose response is an SSE stream held open for the
whole research run: progress notifications arrive first, the answer arrives as the
JSON-RPC response that closes the stream. Measured against the `gcp_hcp_engineering`
persona:

| time | event |
|---|---|
| 0.6 s | `tools/call` → 200 `text/event-stream` |
| 6 s, 40 s, 47 s, 51 s | `notifications/progress` |
| 54 s | the answer, then the stream closes |

Simple questions take ~1 minute; a real multi-source question took 7 minutes. There is no
job id and no polling, so the agent:

- keeps HTTP read timeout off and caps a question with `CHAI_ACP_TIMEOUT_SECONDS`;
- reuses one MCP session (`mcp-session-id`) for its whole lifetime, re-initializing once if
  the server rejects it;
- emits `agent_thought_chunk` for every progress note **and** every
  `CHAI_ACP_HEARTBEAT_SECONDS` of silence, because the server can stay quiet for 30 s+;
- cancels by aborting the HTTP stream — the research runs inside that request, so a
  connection drop is the only cancel. A Stop in T3 settles immediately.

Chai Bot is stateless: each turn is one question. The last two exchanges of the thread ride
along as context so follow-ups make sense.

## Add it to T3 Code

Settings → Providers → **Add provider** → **Show more** → **Local ACP command**:

| field | value |
|---|---|
| Command | `uv` |
| Arguments | `run`, `--script`, `/home/<you>/dev/patjlm/agents-config/chai-acp/chai_acp.py` |
| Display name | Chai Bot (GCP HCP) |

The agent advertises one model through a `session/new` select option with
`category: "model"`: `chai-auto`, shown as **Chai-Auto**. An ACP agent that reports no
models is labelled `Default` in T3's picker, which is why the option exists; Chai Bot picks
its own research strategy, so there is nothing else to choose. Switching the option
re-selects the same model and never reaches the bot.

The script is also executable with a `uv` shebang, so the script path alone works as the
command instead of `uv run --script`.

Environment overrides on the instance:

| variable | default |
|---|---|
| `CHAI_MCP_URL` | the `gcp_hcp_engineering` persona endpoint |
| `CHAI_MCP_TOKEN_FILE` | `~/.secrets/chai-bot-gcp-hcp-engineering-token` |
| `CHAI_MCP_TOKEN` | overrides the file |
| `CHAI_ACP_TIMEOUT_SECONDS` | `900` |
| `CHAI_ACP_HEARTBEAT_SECONDS` | `15` |
| `CHAI_ACP_DEBUG` | unset; `1` traces to stderr |

Per-thread permission modes do nothing here: Chai Bot has no file or shell tools, so the
agent never asks for approval and never touches the workspace. Checkpoints cannot rewind
its conversation.

## Test it without T3

Any ACP client works. `drive.py` next to it is the one used for the checks below:

```bash
uv run --script drive.py ask "Which Slack channel carries GCP HCP CI failures? One line."
uv run --script drive.py followup "And which of those is the alert channel?"   # history rides along
uv run --script drive.py cancel                                                # Stop after 12 s
```

Direct stdio check:

```bash
{ printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{},"clientInfo":{"name":"t","version":"0"}}}\n'; sleep 2; } \
  | uv run --script chai_acp.py
# {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1}}
```

A real turn, with tracing:

```bash
CHAI_ACP_DEBUG=1 uv run --script chai_acp.py   # driven by an ACP client on stdin/stdout
```
