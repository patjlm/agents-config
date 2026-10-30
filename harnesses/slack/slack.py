#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Read-only Slack lookup for the Slack harness.

Why this exists: the MCP returns compact strings and whoami only the
username; this helper adds ISO times, exact `--since` filtering, permalinks,
and the mention/activity views. On this Enterprise Grid token
`users.conversations` and `chat.getPermalink` are blocked
(`enterprise_is_restricted`), so channel names come from
conversations.info or an `in:#name` search. It keeps the agent from
rediscovering that API shape on every run.

Usage:
    slack.py whoami
    slack.py channel <channel-id-or-name>
    slack.py image <channel> <ts> [--index N]
    slack.py search <query> [--since <when>] [--limit N]
    slack.py mentions [--since <when>] [--limit N]
    slack.py activity [--since <when>] [--limit N]
    slack.py history <channel> [--since <when>] [--limit N]
    slack.py replies <channel> <ts> [--limit N]

<when> is YYYY-MM-DD, YYYY-MM-DDTHH:MM[:SS] (UTC) or an epoch ts.
<channel> is a channel id (C…/D…/G…) or a name (with or without #).

`mentions` searches the user id (the only reliable form — the same person
shows up as @patmarti and @Patrick Martin). `activity` lists threads the user
took part in and flags the ones whose last message is someone else's.

Read-only: this script never calls a mutating Slack method.
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
SECRET_DIR = HOME / ".secrets"
API = "https://slack.com/api"
MAX_TEXT = 500
MAX_SNIPPET = 120


def token(env: str, filename: str) -> str:
    # The secret files are the source of truth (the MCP reads the same ones);
    # an exported variable is only a fallback for a one-off run.
    path = SECRET_DIR / filename
    try:
        value = path.read_text().strip()
        if value:
            return value
    except OSError:
        pass
    value = os.environ.get(env)
    if value:
        return value.strip()
    sys.exit(f"error: no token in {path} or ${env}")


TOKEN = token("SLACK_XOXC_TOKEN", "slack-xoxc")
COOKIE = token("SLACK_XOXD_TOKEN", "slack-xoxd")
AUTH: dict = {}
USER_CACHE: dict[str, str] = {}


def request(method: str, **params) -> tuple[dict | None, str | None]:
    """Call a Slack method; (payload, None) or (None, error)."""
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Cookie", f"d={COOKIE}")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.load(response)
    except OSError as e:
        return None, str(e)
    if not payload.get("ok"):
        return None, payload.get("error", "unknown error")
    return payload, None


def api(method: str, **params) -> dict:
    payload, error = request(method, **params)
    if payload is None:
        sys.exit(f"error: {method}: {error}")
    return payload


def auth() -> dict:
    if not AUTH:
        AUTH.update(api("auth.test"))
    return AUTH


def me() -> str:
    return auth().get("user_id", "")


def iso(ts: str | float) -> str:
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def since_ts(when: str | None) -> float:
    if not when:
        return 0.0
    try:
        return float(when)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(when.replace("Z", "+00:00"))
    except ValueError:
        sys.exit(f"error: unsupported time {when!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def handle(user: str | None, username: str | None = None) -> str:
    if not user:
        return "unknown"
    if username:
        return f"@{username}"
    if user not in USER_CACHE:
        payload, _ = request("users.info", user=user)
        info = (payload or {}).get("user", {})
        # A deleted/unknown id must not kill the whole listing.
        USER_CACHE[user] = (
            info.get("profile", {}).get("display_name")
            or info.get("real_name")
            or info.get("name")
            or user
        )
    return f"@{USER_CACHE[user]}"


def describe(info: dict, cid: str) -> str:
    if info.get("is_im"):
        who = handle(info.get("user")) if info.get("user") else "unknown"
        return f"DM ({cid})" if who == "unknown" else f"DM with {who} ({cid})"
    if info.get("name"):
        return f"#{info['name']} ({cid})"
    return cid


def resolve_channel(ref: str) -> tuple[str, dict]:
    if ref[:1] in ("C", "D", "G") and ref[1:].isalnum():
        return ref, api("conversations.info", channel=ref).get("channel", {})
    name = ref.lstrip("#")
    # conversations.list is restricted on this token; search with in: is not.
    result = api("search.messages", query=f"in:#{name}", count=1)
    matches = (result.get("messages") or {}).get("matches") or []
    if not matches:
        sys.exit(f"error: channel {ref!r} not found (no messages for in:#{name})")
    channel = matches[0].get("channel") or {}
    return channel.get("id", ref), channel


def permalink(cid: str, ts: str) -> str:
    return f"{auth().get('url', '').rstrip('/')}/archives/{cid}/p{ts.replace('.', '')}"


def snippet(text: str, size: int = MAX_SNIPPET) -> str:
    return " ".join(str(text or "").split())[:size]


def line(ts: str, where: str, who: str, text: str, link: str, files: list) -> str:
    extra = ""
    if files:
        kinds = ",".join(sorted({f.get("mimetype", "file") for f in files}))
        extra = f"  [files: {len(files)} {kinds}]"
    return f"{iso(ts)} | {where} | {who} | {snippet(text, MAX_TEXT)}{extra} | {link}"


def search(
    query: str,
    cutoff: float,
    limit: int,
    keep: Callable[[dict], bool] | None = None,
) -> list[dict]:
    """search.messages matches since cutoff, newest first.

    `keep` filters inside the page loop, so `limit` counts kept matches (a
    mention search also returns the user's own messages, which must not eat
    the limit)."""
    paged_query = query
    if cutoff:
        # after: has day granularity and is exclusive — widen, then filter.
        day = datetime.fromtimestamp(cutoff, timezone.utc).date() - timedelta(days=1)
        paged_query = f"{query} after:{day.isoformat()}"
    results: list[dict] = []
    page = 1
    while len(results) < limit:
        data = api("search.messages", query=paged_query, count=100, page=page)
        matches = (data.get("messages") or {}).get("matches") or []
        if not matches:
            break
        for match in matches:
            if float(match.get("ts", "0")) < cutoff:
                continue
            if keep is not None and not keep(match):
                continue
            results.append(match)
            if len(results) >= limit:
                break
        paging = (data.get("messages") or {}).get("paging", {})
        if page >= paging.get("pages", 1):
            break
        page += 1
    return results


def print_match(match: dict) -> None:
    ts = match.get("ts", "0")
    channel = match.get("channel") or {}
    cid = channel.get("id", "")
    print(
        line(
            ts,
            describe(channel, cid),
            handle(match.get("user"), match.get("username")),
            match.get("text", ""),
            match.get("permalink") or permalink(cid, ts),
            match.get("files") or [],
        )
    )


def cmd_whoami(_args) -> None:
    info = auth()
    print(f"user: {info.get('user')} ({info.get('user_id')})")
    print(f"team: {info.get('team')} ({info.get('team_id')})")
    print(f"workspace: {info.get('url')}")


def cmd_channel(args) -> None:
    cid, info = resolve_channel(args.channel)
    print(describe(info, cid))


def download(url: str) -> bytes:
    """Slack private file: bearer + cookie, then cookie-only on 401."""
    attempts = [
        {"Authorization": f"Bearer {TOKEN}", "Cookie": f"d={COOKIE}"},
        {"Cookie": f"d={COOKIE}"},
    ]
    for headers in attempts:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                continue
            sys.exit(f"error: download: HTTP {e.code}")
        except OSError as e:
            sys.exit(f"error: download: {e}")
    sys.exit("error: download: unauthorized")


def find_message(cid: str, ts: str) -> dict | None:
    payload, _ = request("conversations.replies", channel=cid, ts=ts, limit=200)
    for message in (payload or {}).get("messages", []):
        if message.get("ts") == ts:
            return message
    payload, _ = request(
        "conversations.history",
        channel=cid,
        oldest=ts,
        latest=ts,
        inclusive="true",
        limit=1,
    )
    for message in (payload or {}).get("messages", []):
        if message.get("ts") == ts:
            return message
    return None


def cmd_image(args) -> None:
    """Download an image attached to a message; print the local path."""
    cid, _ = resolve_channel(args.channel)
    message = find_message(cid, args.ts)
    if message is None:
        sys.exit(f"error: message {args.ts} not found in {cid}")
    images = [
        f
        for f in message.get("files") or []
        if str(f.get("mimetype", "")).startswith("image/")
    ]
    if not images:
        sys.exit(f"error: message {args.ts} has no image attachment")
    if not 0 <= args.index < len(images):
        sys.exit(f"error: --index must be between 0 and {len(images) - 1}")
    attachment = images[args.index]
    url = attachment.get("url_private_download") or attachment.get("url_private")
    if not url:
        sys.exit("error: image attachment has no private URL")
    extension = mimetypes.guess_extension(attachment.get("mimetype", "")) or ".img"
    out = Path("/tmp") / (
        f"slack-image-{cid}-{args.ts.replace('.', '')}-{args.index}{extension}"
    )
    out.write_bytes(download(url))
    print(out)


def cmd_search(args) -> None:
    matches = search(args.query, since_ts(args.since), args.limit)
    for match in matches:
        print_match(match)
    if not matches:
        print("no matches")


def cmd_mentions(args) -> None:
    cutoff = since_ts(args.since)
    my_id = me()
    found: dict[tuple, dict] = {}
    # Slack's mention search on a browser token also returns my own messages;
    # keep only what other people/bots addressed to me.
    for match in search(
        f"<@{my_id}>", cutoff, args.limit, keep=lambda m: m.get("user") != my_id
    ):
        channel = match.get("channel") or {}
        found[(channel.get("id"), match.get("ts"))] = match
    for key in sorted(found, key=lambda k: float(found[k].get("ts", "0"))):
        print_match(found[key])
    if not found:
        print("no mentions")


def cmd_activity(args) -> None:
    """Threads I took part in since cutoff, with who spoke last.

    search.messages does not return thread_ts, so participation is decided
    from conversations.replies: a thread counts when one of its messages is
    mine or mentions me.
    """
    cutoff = since_ts(args.since)
    my_id = me()
    channels: dict[str, dict] = {}
    threads: dict[tuple, dict] = {}
    for match in search(f"from:{my_id}", cutoff, 200):
        channel = match.get("channel") or {}
        if channel.get("id"):
            channels[channel["id"]] = channel
    for cid, channel in channels.items():
        params = {"channel": cid, "limit": 200}
        if cutoff:
            params["oldest"] = str(cutoff)
        for message in api("conversations.history", **params).get("messages", []):
            ts = message.get("ts", "0")
            thread_ts = message.get("thread_ts")
            root = None
            if thread_ts and thread_ts != ts:
                root = thread_ts
            elif message.get("reply_count"):
                root = ts
            if root:
                threads[(cid, root)] = channel
    # Mentions in channels I did not post in still count as participation.
    for match in search(f"<@{my_id}>", cutoff, 200):
        channel = match.get("channel") or {}
        cid = channel.get("id")
        if cid:
            threads[(cid, match.get("thread_ts") or match.get("ts"))] = channel
    rows: list[tuple[float, str]] = []
    for (cid, root), channel in threads.items():
        replies = api(
            "conversations.replies", channel=cid, ts=root, limit=200
        ).get("messages", [])
        if not replies:
            continue
        participated = any(
            reply.get("user") == my_id or f"<@{my_id}>" in (reply.get("text") or "")
            for reply in replies
        )
        if not participated:
            continue
        last = replies[-1]
        last_ts = float(last.get("ts", "0"))
        if cutoff and last_ts < cutoff:
            continue
        last_user = last.get("user")
        needs = last_user != my_id
        rows.append(
            (
                last_ts,
                f"{iso(root)} | {describe(channel, cid)} | thread: "
                f"{snippet(replies[0].get('text', ''))} | last: "
                f"{handle(last_user)} {iso(last_ts)} | needs reply: "
                f"{'yes' if needs else 'no'} | {permalink(cid, last.get('ts', '0'))}",
            )
        )
    for _, text in sorted(rows)[-args.limit :]:
        print(text)
    if not rows:
        print("no threads")


def cmd_history(args) -> None:
    cid, info = resolve_channel(args.channel)
    where = describe(info, cid)
    params = {"channel": cid, "limit": args.limit}
    cutoff = since_ts(args.since)
    if cutoff:
        params["oldest"] = str(cutoff)
    messages = api("conversations.history", **params).get("messages", [])
    if not messages:
        print("no messages")
    for message in messages:
        ts = message.get("ts", "0")
        replies = message.get("reply_count")
        suffix = f"  [thread: {replies} replies]" if replies else ""
        print(
            line(
                ts,
                where,
                handle(message.get("user"), message.get("username")),
                message.get("text", ""),
                permalink(cid, ts),
                message.get("files") or [],
            )
            + suffix
        )


def cmd_replies(args) -> None:
    cid, info = resolve_channel(args.channel)
    where = describe(info, cid)
    messages = api(
        "conversations.replies", channel=cid, ts=args.ts, limit=args.limit
    ).get("messages", [])
    for message in messages:
        ts = message.get("ts", "0")
        print(
            line(
                ts,
                where,
                handle(message.get("user"), message.get("username")),
                message.get("text", ""),
                permalink(cid, ts),
                message.get("files") or [],
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Slack lookup")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("whoami").set_defaults(func=cmd_whoami)

    search_cmd = sub.add_parser("search")
    search_cmd.add_argument("query")
    search_cmd.add_argument("--since")
    search_cmd.add_argument("--limit", type=int, default=50)
    search_cmd.set_defaults(func=cmd_search)

    mentions = sub.add_parser("mentions")
    mentions.add_argument("--since")
    mentions.add_argument("--limit", type=int, default=50)
    mentions.set_defaults(func=cmd_mentions)

    activity = sub.add_parser("activity")
    activity.add_argument("--since")
    activity.add_argument(
        "--limit", type=int, default=50, help="most recent N threads"
    )
    activity.set_defaults(func=cmd_activity)

    history = sub.add_parser("history")
    history.add_argument("channel")
    history.add_argument("--since")
    history.add_argument("--limit", type=int, default=100)
    history.set_defaults(func=cmd_history)

    replies = sub.add_parser("replies")
    replies.add_argument("channel")
    replies.add_argument("ts")
    replies.add_argument("--limit", type=int, default=100)
    replies.set_defaults(func=cmd_replies)

    channel = sub.add_parser("channel")
    channel.add_argument("channel")
    channel.set_defaults(func=cmd_channel)

    image = sub.add_parser("image")
    image.add_argument("channel")
    image.add_argument("ts")
    image.add_argument("--index", type=int, default=0)
    image.set_defaults(func=cmd_image)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
