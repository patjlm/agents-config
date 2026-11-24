#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "agent-client-protocol>=1.0.0",
#     "httpx>=0.27",
#     "pyyaml>=6",
# ]
# ///
"""ACP agent that fronts Chai Bot (`ask_persona`) so T3 Code can run it as a provider.

Chai Bot answers one question per MCP `tools/call`, streamed over SSE on a single
long-lived HTTP request: progress notifications arrive first, the answer arrives as
the JSON-RPC response that closes the stream. There is no job id and no polling, so a
turn holds that request open for as long as the research takes (minutes).

Protocol notes:
  * stdout carries ACP frames only; all diagnostics go to stderr.
  * T3 wraps the first prompt of a session in <t3_code_instructions>/<user_request>;
    only the user request is forwarded to Chai Bot.

Configuration (environment):
  CHAI_ACP_PERSONAS_FILE    personas.yaml to use, default the file next to this script
  CHAI_MCP_URL              endpoint template, default /personas/{persona}/mcp on the default host
  CHAI_MCP_TOKEN_FILE       bearer token file, default ~/.secrets/chai-mcp-token
  CHAI_MCP_TOKEN            bearer token, overrides the file
  CHAI_ACP_TIMEOUT_SECONDS  overall cap per question, default 2700 (45 minutes)
  CHAI_ACP_HEARTBEAT_SECONDS seconds between liveness notes, default 15
  CHAI_ACP_DEBUG            set to 1 for stderr tracing
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from acp import Agent, InitializeResponse, NewSessionResponse, PromptResponse, run_agent
from acp.interfaces import Client
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ClientCapabilities,
    Implementation,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionInfoUpdate,
    SetSessionConfigOptionResponse,
    TextContentBlock,
)

DEFAULT_URL = (
    "https://ship-help-mcp-chai-bot--internal-slack.apps.prod-scale-spoke1-dc-rdu3"
    ".itup.redhat.com/personas/{persona}/mcp"
)
DEFAULT_TOKEN_FILE = "~/.secrets/chai-mcp-token"

# One token covers every persona; only the URL path selects it. The list lives in
# personas.yaml because the service exposes no catalog (unknown ids 404).
PERSONAS_FILE = Path(__file__).with_name("personas.yaml")


@dataclass(frozen=True)
class Persona:
    """One picker choice: the id is the MCP path segment."""

    id: str
    label: str
    description: str | None = None


FALLBACK_PERSONAS: list[Persona] = [Persona("gcp_hcp_engineering", "GCP HCP Engineering")]

# T3 Code labels an unreported model "Default"; one named choice avoids that.
MODEL_OPTION_ID = "model"
MODEL_ID = "chai-auto"
PERSONA_OPTION_ID = "persona"

TIMEOUT_SECONDS = float(os.environ.get("CHAI_ACP_TIMEOUT_SECONDS", "2700"))
HEARTBEAT_SECONDS = float(os.environ.get("CHAI_ACP_HEARTBEAT_SECONDS", "15"))
DEBUG = os.environ.get("CHAI_ACP_DEBUG") == "1"

# T3 prepends its own instructions on the first prompt of a session.
_USER_REQUEST = re.compile(r"<user_request>(.*?)</user_request>", re.S)


def log(message: str) -> None:
    if DEBUG:
        print(f"chai-acp: {message}", file=sys.stderr, flush=True)


_WARNED: set[str] = set()


def warn(message: str) -> None:
    """Configuration problems must show up without CHAI_ACP_DEBUG, once each."""
    if message in _WARNED:
        return
    _WARNED.add(message)
    print(f"chai-acp: {message}", file=sys.stderr, flush=True)


def elapsed(since: float) -> str:
    total = int(time.monotonic() - since)
    return f"{total // 60}m{total % 60:02d}s" if total >= 60 else f"{total}s"


class ChaiError(Exception):
    """Chai Bot call failed in a way the user should see."""


class SessionExpired(ChaiError):
    """The MCP session id was rejected; the caller retries once with a fresh one."""


def read_token() -> str:
    token = os.environ.get("CHAI_MCP_TOKEN")
    if token:
        return token.strip()
    path = Path(os.environ.get("CHAI_MCP_TOKEN_FILE", DEFAULT_TOKEN_FILE)).expanduser()
    try:
        return path.read_text().strip()
    except OSError as error:
        raise ChaiError(f"Cannot read Chai Bot token at {path}: {error}") from error


def persona_file() -> Path:
    override = os.environ.get("CHAI_ACP_PERSONAS_FILE", "").strip()
    return Path(override).expanduser() if override else PERSONAS_FILE


def load_personas() -> tuple[list[Persona], str]:
    """Read personas.yaml: the personas and the id a new session starts on.

    Re-read on every `session/new`, so editing the file needs no restart.
    """
    path = persona_file()
    fallback = (list(FALLBACK_PERSONAS), FALLBACK_PERSONAS[0].id)
    try:
        document = yaml.safe_load(path.read_text()) or {}
    except OSError as error:
        warn(f"cannot read {path} ({error}); using {FALLBACK_PERSONAS[0].id} only")
        return fallback
    except yaml.YAMLError as error:
        warn(f"{path} is not valid YAML ({error}); using {FALLBACK_PERSONAS[0].id} only")
        return fallback

    entries: list[Persona] = []
    for item in (document.get("personas") or []) if isinstance(document, dict) else []:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            continue
        persona_id = str(item.get("id") or "").strip()
        if not persona_id:
            warn(f"skipping a persona without an id in {path}")
            continue
        if any(existing.id == persona_id for existing in entries):
            continue
        entries.append(
            Persona(
                id=persona_id,
                label=str(item.get("label") or "").strip() or persona_id,
                description=str(item.get("description") or "").strip() or None,
            )
        )
    if not entries:
        warn(f"no personas in {path}; using {FALLBACK_PERSONAS[0].id} only")
        return fallback

    requested = str((document.get("default") or "")).strip()
    ids = {entry.id for entry in entries}
    default = requested if requested in ids else entries[0].id
    if requested and default != requested:
        warn(f"default persona {requested!r} is not in {path}; starting on {default}")
    return entries, default


def persona_url(persona_id: str) -> str:
    template = os.environ.get("CHAI_MCP_URL", DEFAULT_URL)
    return template.format(persona=persona_id) if "{persona}" in template else template


def model_option() -> SessionConfigOptionSelect:
    return SessionConfigOptionSelect(
        id=MODEL_OPTION_ID,
        name="Model",
        category="model",
        current_value=MODEL_ID,
        options=[
            SessionConfigSelectOption(
                value=MODEL_ID,
                name="Chai-Auto",
                description="The Chai Bot persona picks its own research strategy.",
            )
        ],
    )


def persona_option(current: str, entries: list[Persona]) -> SessionConfigOptionSelect:
    return SessionConfigOptionSelect(
        id=PERSONA_OPTION_ID,
        name="Persona",
        current_value=current,
        options=[
            SessionConfigSelectOption(
                value=persona.id,
                name=persona.label,
                **({"description": persona.description} if persona.description else {}),
            )
            for persona in entries
        ],
    )


class ChaiMcp:
    """Minimal MCP client: streamable HTTP, `progressToken` progress, one session at a time."""

    def __init__(self, persona: str) -> None:
        self.persona = persona
        self._url = persona_url(persona)
        self._token = read_token()
        log(f"mcp endpoint {self._url}")
        self._session_id: str | None = None
        self._ids = itertools.count(1)
        # No read timeout: the answer arrives minutes later on the same stream.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(None, connect=30.0),
            limits=httpx.Limits(max_keepalive_connections=4),
        )
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    async def _ensure_session(self) -> None:
        async with self._lock:
            if self._session_id:
                return
            response = await self._client.post(
                self._url,
                headers=self._headers(),
                json={
                    "jsonrpc": "2.0",
                    "id": next(self._ids),
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "chai-acp", "version": "1.0.0"},
                    },
                },
                timeout=httpx.Timeout(60.0, connect=30.0),
            )
            if response.status_code == 404:
                raise ChaiError(
                    f"Unknown persona {self.persona!r}: {self._url} answers 404. "
                    "Add the id to personas.yaml, or fix the persona path in CHAI_MCP_URL."
                )
            if response.status_code >= 400:
                raise ChaiError(
                    f"MCP initialize failed: HTTP {response.status_code} {response.text[:300]}"
                )
            self._session_id = response.headers.get("mcp-session-id")
            log(f"mcp session {self._session_id}")
            if self._session_id:
                # Best effort: servers that require it answer 202, others ignore it.
                with contextlib.suppress(httpx.HTTPError):
                    await self._client.post(
                        self._url,
                        headers=self._headers(),
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                        timeout=httpx.Timeout(30.0, connect=30.0),
                    )

    async def ask(self, question: str, on_progress) -> str:
        """Run one `ask_persona` call and return its answer text."""
        try:
            await self._ensure_session()
            try:
                return await self._call(question, on_progress)
            except SessionExpired:
                log("session rejected, re-initializing once")
                self._session_id = None
                await self._ensure_session()
                return await self._call(question, on_progress)
        except ChaiError:
            raise
        except httpx.HTTPError as error:
            # Transport failures must reach the thread as text, not as an ACP internal error.
            raise ChaiError(f"Chai Bot unreachable: {error}") from error

    async def _call(self, question: str, on_progress) -> str:
        request_id = next(self._ids)
        progress_token = uuid.uuid4().hex
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": "ask_persona",
                "arguments": {"question": question},
                "_meta": {"progressToken": progress_token},
            },
        }
        log(f"tools/call id={request_id} chars={len(question)}")
        async with self._client.stream(
            "POST", self._url, headers=self._headers(), json=payload
        ) as response:
            if response.status_code == 404 and self._session_id:
                raise SessionExpired("MCP session expired")
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                raise ChaiError(f"Chai Bot HTTP {response.status_code}: {body[:300]}")
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" not in content_type:
                body = (await response.aread()).decode("utf-8", "replace")
                try:
                    return _result_text(json.loads(body))
                except (ValueError, KeyError) as error:
                    raise ChaiError(f"Unreadable Chai Bot response: {error}") from error
            return await self._read_stream(response, request_id, on_progress)

    async def _read_stream(self, response, request_id: int, on_progress) -> str:
        pending: list[str] = []
        async for line in response.aiter_lines():
            if line.startswith(":"):
                continue  # SSE comment / keepalive ping
            if line == "":
                if not pending:
                    continue
                frame = "\n".join(pending)
                pending.clear()
                message = _parse_frame(frame)
                if message is None:
                    continue
                if message.get("id") == request_id:
                    return _result_text(message)
                if message.get("method") == "notifications/progress":
                    params = message.get("params") or {}
                    await on_progress(params.get("message"), params.get("progress"))
                continue
            if line.startswith("data:"):
                pending.append(line[5:].lstrip())
        raise ChaiError("Chai Bot closed the stream before answering")


def _parse_frame(frame: str) -> dict[str, Any] | None:
    try:
        message = json.loads(frame)
    except ValueError:
        log(f"unparsed frame {frame[:200]}")
        return None
    return message if isinstance(message, dict) else None


def _result_text(message: dict[str, Any]) -> str:
    if "error" in message:
        error = message["error"] or {}
        raise ChaiError(f"Chai Bot error: {error.get('message', error)}")
    result = message.get("result") or {}
    parts = [
        block.get("text", "")
        for block in result.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        structured = result.get("structuredContent") or {}
        text = str(structured.get("result") or "").strip()
    if not text:
        raise ChaiError("Chai Bot returned no text")
    if result.get("isError"):
        raise ChaiError(text)
    return text


def prompt_text(prompt: list[Any]) -> str:
    parts: list[str] = []
    for block in prompt:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text)
    raw = "\n".join(parts).strip()
    match = _USER_REQUEST.search(raw)
    return (match.group(1) if match else raw).strip()


@dataclass
class Session:
    cwd: str
    persona: str
    history: list[tuple[str, str]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    titled: bool = False


class ChaiAgent(Agent):
    """One ACP session per T3 thread; Chai Bot is stateless, so prior turns ride along."""

    _conn: Client

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._clients: dict[str, ChaiMcp] = {}

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    def chai(self, persona: str) -> ChaiMcp:
        """One MCP session per persona, kept warm for the process lifetime."""
        client = self._clients.get(persona)
        if client is None:
            client = self._clients[persona] = ChaiMcp(persona)
        return client

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        log(f"initialize v{protocol_version} client={client_info}")
        return InitializeResponse(protocol_version=protocol_version)

    async def new_session(self, cwd: str | None = None, **kwargs: Any) -> NewSessionResponse:
        session_id = uuid.uuid4().hex
        entries, default = load_personas()
        session = Session(cwd=cwd or os.getcwd(), persona=default)
        # T3 injects its own MCP servers here; Chai Bot has no tools to hand them.
        self._sessions[session_id] = session
        log(f"new session {session_id} cwd={cwd} persona={session.persona}")
        return NewSessionResponse(
            session_id=session_id,
            config_options=[model_option(), persona_option(session.persona, entries)],
        )

    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **kwargs: Any
    ) -> SetSessionConfigOptionResponse | None:
        session = self._sessions.get(session_id)
        entries, default = load_personas()
        # Changing the model is a no-op: one model exists and it is what this agent runs.
        if config_id == PERSONA_OPTION_ID and session is not None and isinstance(value, str):
            if value in {persona.id for persona in entries}:
                if value != session.persona:
                    log(f"persona {session.persona} -> {value} session={session_id}")
                    session.persona = value
                    # Chai Bot is stateless, but the previous persona's answers are not.
                    session.history.clear()
            else:
                log(f"unknown persona {value!r}, keeping {session.persona}")
        log(f"set_config_option {config_id}={value!r} session={session_id}")
        current = session.persona if session is not None else default
        return SetSessionConfigOptionResponse(
            config_options=[model_option(), persona_option(current, entries)]
        )

    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> PromptResponse:
        session = self._sessions.get(session_id)
        if session is None:
            session = self._sessions[session_id] = Session(cwd=os.getcwd())
        text = prompt_text(prompt)
        if not text:
            return PromptResponse(stop_reason="end_turn")
        if not session.titled:
            session.titled = True
            await self._notify(
                session_id,
                SessionInfoUpdate(title=text.splitlines()[0][:120]),
            )
        question = _with_history(session, text)
        session.task = asyncio.create_task(self._answer(session_id, session, text, question))
        try:
            await session.task
        except asyncio.CancelledError:
            log(f"prompt cancelled {session_id}")
            return PromptResponse(stop_reason="cancelled")
        finally:
            session.task = None
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        session = self._sessions.get(session_id)
        # The research runs inside the HTTP request, so aborting the task is the only cancel.
        if session is not None and session.task is not None:
            log(f"cancel {session_id}")
            session.task.cancel()

    async def _answer(self, session_id: str, session: Session, text: str, question: str) -> None:
        started = time.monotonic()
        last_note = [started]
        heartbeat = asyncio.create_task(self._heartbeat(session_id, started, last_note))
        failure: str | None = None
        answer: str | None = None

        async def on_progress(message: Any, progress: Any) -> None:
            last_note[0] = time.monotonic()
            note = str(message) if message else "working"
            await self._thought(session_id, f"Chai Bot: {note} ({elapsed(started)})")

        try:
            answer = await asyncio.wait_for(
                self.chai(session.persona).ask(question, on_progress), TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            cap = (
                f"{TIMEOUT_SECONDS / 60:.0f} min" if TIMEOUT_SECONDS >= 60 else f"{TIMEOUT_SECONDS:.0f}s"
            )
            failure = (
                f"no answer within {cap}, turn abandoned "
                "(raise CHAI_ACP_TIMEOUT_SECONDS to wait longer)"
            )
        except ChaiError as error:
            failure = str(error)
        except Exception as error:  # noqa: BLE001 - every failure becomes thread text
            failure = f"unexpected error: {error!r}"
        except asyncio.CancelledError:
            heartbeat.cancel()
            with contextlib.suppress(BaseException):
                await heartbeat
            raise
        finally:
            if not heartbeat.done():
                heartbeat.cancel()
                with contextlib.suppress(BaseException):
                    await heartbeat

        if answer is None:
            log(f"failure: {failure}")
            await self._message(session_id, f"**Chai Bot failed.** {failure}")
            return
        session.history.append((text, answer))
        log(f"answer in {elapsed(started)}, {len(answer)} chars")
        await self._message(session_id, answer)

    async def _heartbeat(self, session_id: str, started: float, last_note: list[float]) -> None:
        """Keep the turn visibly alive through gaps the server leaves silent (30 s+)."""
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if time.monotonic() - last_note[0] < HEARTBEAT_SECONDS:
                continue
            await self._thought(session_id, f"Chai Bot is still researching ({elapsed(started)})")

    async def _thought(self, session_id: str, text: str) -> None:
        # T3 drops messageId on thought chunks and only appends their text, so each
        # note must carry its own line break or the notes run together.
        line = text.rstrip("\n") + "\n"
        await self._notify(session_id, AgentThoughtChunk(content=TextContentBlock(text=line)))

    async def _message(self, session_id: str, text: str) -> None:
        await self._notify(session_id, AgentMessageChunk(content=TextContentBlock(text=text)))

    async def _notify(self, session_id: str, update: Any) -> None:
        with contextlib.suppress(Exception):
            await self._conn.session_update(session_id=session_id, update=update, source="chai-acp")

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()


def _with_history(session: Session, text: str) -> str:
    if not session.history:
        return text
    parts = ["Earlier questions in this thread, for context only:"]
    for question, answer in session.history[-2:]:
        parts.append(f"Q: {question[:500]}\nA: {answer[:1500]}")
    parts.append(f"Current question:\n{text}")
    return "\n\n".join(parts)


async def main() -> None:
    agent = ChaiAgent()
    try:
        await run_agent(agent)
    finally:
        await agent.aclose()


if __name__ == "__main__":
    asyncio.run(main())
