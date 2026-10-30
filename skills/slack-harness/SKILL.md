---
name: slack-harness
description: Read or write Slack through the dedicated Slack agent — searches, summaries, mentions, channel/thread reads, posting, reactions, DMs, and images. Invoke it from T3 Code as a dedicated thread or subagent, or from any shell through ~/.pi/harnesses/slack/pi.
---

# Slack via the Slack harness

Slack access lives in one place: the dedicated `pi_slack` harness
(`~/.pi/harnesses/slack/`). Other agents have no Slack MCP, so delegate
instead of trying to reach Slack yourself.

## Pick a path

- **T3 Code** (the `t3_thread_launch` / `delegate_task` tools exist): use a
  dedicated thread or a subagent with provider instance `pi_slack` and model
  `default`.
- **Any shell / outside T3**: run the harness binary directly. No T3, no
  approvals:

```sh
~/.pi/harnesses/slack/pi -p --no-session "<prompt>"
```

Prefix the command with `SLACK_MCP_READ_ONLY=1` when the task is read-only:
the harness then does not even load the write tools. In this path write tools
also need `SLACK_HARNESS_ALLOW_WRITES=1` (there is no approval UI); prefer the
T3 path when a write should be approved.

For a long or multiline prompt:

```sh
~/.pi/harnesses/slack/pi -p --no-session "$(cat <<'EOF'
...
EOF
)"
```

## T3 Code

Dedicated thread — best when you want to inspect or continue the work:

1. `t3_thread_launch` with
   `modelSelection: {"instanceId": "pi_slack", "model": "default"}`,
   `scratch: true` unless the work belongs to a project, and the prompt below.
2. `t3_thread_wait`, then `t3_thread_read`; relay the answer and the thread
   link.
3. `t3_thread_interrupt` if it is still running when you no longer need it.

Subagent — best when you only need the answer:

```
delegate_task({
  role: "research",
  title: "Slack: <task>",
  target: { providerInstanceId: "pi_slack", model: "default" },
  task: "<prompt>"
})
```

If `orchestrator_capabilities` does not list `pi_slack`, use the shell path.
Do not create provider instances or threads on another configuration.

## Prompt to send

Give it the task, the time window, the output shape, and the write policy.
The harness handles its own Slack setup and identity:

```
<task: summarize / search / find mentions / read thread / post / react ...>
Since: <UTC timestamp, e.g. 2026-10-29T12:00:00Z, or "last 24h">.
Report: <channel, UTC time, one line each, permalinks; end with what needs a reply>.
Read-only: do not post, react, or send DMs.
```

For writes, be exact and explicit — never let it guess a channel or
recipient; ask the user first if they did not name one:

```
Post exactly: "<text>"
To: <#channel-name or channel id>   (thread_ts: <ts> if it is a reply)
Do not post anywhere else.
```

## What it can do

- Read: search, channel history, threads, mentions, reactions, channel/user
  lookup, unread DMs, and images (it downloads Slack files and reads them
  with a vision model).
- Write: post message, thread reply, DM/group DM, reaction, channel
  create/rename/invite, usergroup edits.

## Boundaries

- One request per invocation. The harness is single-purpose: do not ask it
  to edit files or do project work.
- Slack is the only data source. Never put tokens or secrets in the prompt.
- If the harness reports an error, relay its message as-is, including any
  fix it suggests. Do not guess at auth, setup, or data problems.
- It runs a guard: shell commands only for its own helper, file reads only for
  its image downloads. Ask for Slack work, not shell or file work, and do not
  try to work around a refusal.
- If `~/.pi/harnesses/slack/pi` is missing, the harness is not installed;
  run the agents-config repo's `make install` first.
- Each open T3 Slack thread or subagent keeps one `slack-mcp` container
  alive (T3 keeps the provider session warm). It goes away when the thread
  is closed or T3 restarts; the CLI path leaves no container behind.
