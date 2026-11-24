# /// script
# requires-python = ">=3.10"
# dependencies = ["agent-client-protocol>=1.0.0"]
# ///
"""Drive chai_acp.py over ACP stdio the way T3 Code does.

    uv run --script drive.py ask "your question"
    uv run --script drive.py followup "a question"   # then a context-dependent follow-up
    uv run --script drive.py cancel                  # prompt, then Stop after 12s
"""

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.schema import TextContentBlock
from acp.interfaces import Client

SCRIPT = str(Path(__file__).with_name("chai_acp.py"))
MODE = sys.argv[1] if len(sys.argv) > 1 else "ask"
QUESTION = (
    sys.argv[2]
    if len(sys.argv) > 2
    else "Name the two Slack channels most used for GCP HCP CI failures. Answer in one line."
)

start = time.time()


def _field(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def log(msg: str) -> None:
    print(f"[{time.time() - start:6.1f}s] {msg}", flush=True)


class Driver(Client):
    async def request_permission(self, session_id, tool_call, options, **kwargs: Any):
        log("UNEXPECTED permission request")
        return {"outcome": {"outcome": "cancelled"}}

    async def session_update(self, session_id, update, **kwargs: Any) -> None:
        kind = update.get("sessionUpdate") if isinstance(update, dict) else update.session_update
        content = update.get("content") if isinstance(update, dict) else getattr(update, "content", None)
        text = ""
        if isinstance(content, dict):
            text = content.get("text") or ""
        elif content is not None:
            text = getattr(content, "text", "") or ""
        title = ""
        if isinstance(update, dict):
            title = update.get("title") or ""
        elif hasattr(update, "title"):
            title = update.title or ""
        if kind == "session_info_update":
            log(f"UPDATE {kind} title={title!r}")
        else:
            log(f"UPDATE {kind} {text[:160]!r}{'...' if len(text) > 160 else ''}")


async def main() -> None:
    wrapped = (
        "<t3_code_instructions>\nYou are running inside T3 Code.\n</t3_code_instructions>\n\n"
        f"<user_request>\n{QUESTION}\n</user_request>"
    )
    async with spawn_agent_process(
        Driver(),
        "uv",
        "run",
        "--script",
        SCRIPT,
        env=dict(os.environ),
        transport_kwargs={"stderr": None},
    ) as (conn, _proc):
        init = await conn.initialize(protocol_version=PROTOCOL_VERSION)
        log(f"initialize -> v{init.protocol_version} caps={init.agent_capabilities}")
        session = await conn.new_session(
            cwd="/home/patmarti/dev/patjlm/agents-config",
            mcp_servers=[],
        )
        log(f"new_session -> {session.session_id}")
        for option in session.config_options or []:
            names = [(_field(o, "value"), _field(o, "name")) for o in _field(option, "options") or []]
            log(
                f"  config option {_field(option, 'id')} category={_field(option, 'category')}"
                f" current={_field(option, 'current_value', 'currentValue')} choices={names}"
            )
        if MODE == "cancel":
            task = asyncio.create_task(
                conn.prompt(session_id=session.session_id, prompt=[TextContentBlock(text=wrapped)])
            )
            await asyncio.sleep(12)
            log("sending session/cancel")
            t0 = time.time()
            await conn.cancel(session_id=session.session_id)
            result = await task
            log(f"prompt -> {result.stop_reason} (cancel settled in {time.time() - t0:.1f}s)")
            return
        await conn.set_config_option(
            config_id="model", session_id=session.session_id, value="chai-auto"
        )
        log("set_config_option(model=chai-auto) accepted")
        result = await conn.prompt(session_id=session.session_id, prompt=[TextContentBlock(text=wrapped)])
        log(f"prompt -> stop_reason={result.stop_reason}")
        if MODE == "followup":
            follow = TextContentBlock(text="And which of those two is the alert channel? One line.")
            result = await conn.prompt(session_id=session.session_id, prompt=[follow])
            log(f"followup -> stop_reason={result.stop_reason}")


asyncio.run(main())
