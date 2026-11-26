#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Find T3 Code worktrees no live thread points at, and remove the stale ones.

Read-only by default: it prints the candidates and stops. Removal needs
`--remove` with explicit paths plus `--confirmed`.

  uv run orphan_worktrees.py                     # report, remove nothing
  uv run orphan_worktrees.py --days 60           # other staleness window
  uv run orphan_worktrees.py --remove P [P...] --confirmed
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

T3 = Path.home() / ".t3"
V2_DB = T3 / "userdata" / "statev2.sqlite"
LEGACY_DB = T3 / "userdata" / "state.sqlite"


def sql(db, query):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(query).fetchall()
    finally:
        con.close()


def git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )


def projects():
    """Registered projects whose workspace root is a git repo."""
    rows = sql(V2_DB, "select workspace_root from projection_projects "
                      "where deleted_at is null")
    return sorted({
        os.path.normpath(r) for (r,) in rows
        if r and os.path.isdir(os.path.join(r, ".git"))
    })


def live_thread_paths():
    """Normed worktreePath of every live thread, v1 and v2, subagents included."""
    v2 = sql(
        V2_DB,
        """select case when json_valid(payload_json)
                       then json_extract(payload_json, '$.worktreePath') end
           from orchestration_v2_projection_threads
           where case when json_valid(payload_json)
                      then json_extract(payload_json, '$.deletedAt') end is null""",
    )
    v1 = sql(LEGACY_DB, "select worktree_path from projection_threads "
                        "where deleted_at is null")
    return {os.path.normpath(p) for (p,) in v2 + v1 if p}


def worktrees():
    """Registered worktrees per project, main checkouts dropped.

    Nested worktrees can be registered in two repos: dedupe by path, keep all.
    """
    found = {}
    for root in projects():
        r = git(root, "worktree", "list", "--porcelain")
        if r.returncode:
            print(f"warn: {root}: {r.stderr.strip()}", file=sys.stderr)
            continue
        entry, first = None, True
        for line in r.stdout.splitlines() + [""]:
            if not line:
                if entry and entry["path"] and not first:
                    path = os.path.normpath(entry["path"])
                    if path in found:
                        found[path]["repos"].append(root)
                    else:
                        entry["repos"] = [root]
                        found[path] = entry
                entry, first = None, False
                continue
            key, _, val = line.partition(" ")
            if key == "worktree":
                entry = {"path": val, "branch": None, "locked": ""}
            elif entry is None:
                continue
            elif key == "branch":
                entry["branch"] = val.removeprefix("refs/heads/")
            elif key == "locked":
                entry["locked"] = val or "locked"
    return found


def facts(w):
    """Dirty state, last commit, unpushed count. None when git cannot read it."""
    path = w["path"]
    w["dirty"] = w["last_commit"] = w["unpushed"] = w["age"] = None
    if git(path, "rev-parse", "--git-dir").returncode:
        return w
    w["dirty"] = bool(git(path, "status", "--porcelain").stdout.strip())
    w["last_commit"] = git(path, "log", "-1", "--format=%cs").stdout.strip() or None
    w["age"] = (date.today() - date.fromisoformat(w["last_commit"])).days \
        if w["last_commit"] else None
    count = git(path, "rev-list", "--count", "HEAD", "--not", "--remotes").stdout
    w["unpushed"] = int(count.strip()) if count.strip().isdigit() else None
    return w


def collect(days):
    live = live_thread_paths()
    wts = worktrees()
    data = {"bound": [], "orphan": [], "dead": [], "days": days}
    for path, w in wts.items():
        if not os.path.isdir(path):
            data["dead"].append(w)
        elif path in live:
            data["bound"].append(w)
        else:
            data["orphan"].append(facts(w))
    data["stale"] = sum(1 for p in live if not os.path.isdir(p) and p not in wts)
    data["candidates"] = [
        w for w in data["orphan"]
        if w["dirty"] is False and w["age"] is not None and w["age"] > days
    ]
    return data


def why(w, days):
    """Why an orphaned worktree was not offered for removal."""
    if w["dirty"] is None:
        return "git cannot read this worktree"
    out = ["dirty"] if w["dirty"] else []
    if w["age"] is None:
        out.append("no commit date")
    elif w["age"] <= days:
        out.append(f"last commit {w['age']}d ago")
    return ", ".join(out)


def describe(w):
    print(f"  {w['path']}")
    print(f"    repo {' '.join(w['repos'])}  branch {w['branch'] or 'detached'}  "
          f"last commit {w['last_commit']} ({w['age']}d)  "
          f"{'dirty' if w['dirty'] else 'clean'}")
    if w["locked"]:
        print(f"    locked: {w['locked']}")
    if w["unpushed"]:
        print(f"    {w['unpushed']} commit(s) not on any remote")
    if not w["branch"]:
        print("    detached HEAD: commits survive only if a ref still points there")


def report(data):
    print(f"registered worktrees: {len(data['bound']) + len(data['orphan']) + len(data['dead'])}"
          f"  bound: {len(data['bound'])}  orphaned: {len(data['orphan'])}"
          f"  dead registrations: {len(data['dead'])}")
    print(f"stale thread pointers: {data['stale']} live thread(s) point at paths "
          f"that no longer exist — nothing to remove")

    cand = data["candidates"]
    print(f"\nCandidates (orphaned, clean, last commit older than {data['days']} days): {len(cand)}")
    for w in sorted(cand, key=lambda x: x["path"]):
        describe(w)
    if not cand:
        print("  none")

    rest = [w for w in data["orphan"] if w not in cand]
    print(f"\nOrphaned, not offered: {len(rest)}")
    for w in sorted(rest, key=lambda x: x["path"]):
        print(f"  {w['path']} — {why(w, data['days'])}")
    if not rest:
        print("  none")

    print(f"\nDead registrations (directory gone, safe to prune): {len(data['dead'])}")
    for w in data["dead"]:
        print(f"  {w['path']} — git -C {w['repos'][0]} worktree prune")
    if not data["dead"]:
        print("  none")

    if cand:
        print("\nNothing was removed. Show these paths to the user and ask for "
              "approval, then rerun with --remove <path>... --confirmed.")


def remove(paths, data):
    by_path = {w["path"]: w for w in data["orphan"]}
    cand = {w["path"] for w in data["candidates"]}
    touched, failed = set(), []
    for raw in paths:
        path = os.path.normpath(raw)
        w = by_path.get(path)
        if w is None or path not in cand:
            print(f"skip {path}: not a candidate")
            failed.append(path)
            continue
        repo = w["repos"][0]
        touched.add(repo)
        # A git lock only blocks pruning, it is no reason to keep the worktree.
        args = ["worktree", "remove"] + (["--force"] if w["locked"] else []) + [path]
        r = git(repo, *args)
        if r.returncode:
            if f"worktree {path}\n" in git(repo, "worktree", "list", "--porcelain").stdout:
                print(f"fail {path}: {r.stderr.strip()}")
                failed.append(path)
                continue
            # Read-only leftovers: deregistered, but the tree could not be deleted.
            subprocess.run(["chmod", "-R", "u+w", path])
            shutil.rmtree(path, ignore_errors=True)
            if os.path.exists(path):
                print(f"fail {path}: still present after chmod -R u+w")
                failed.append(path)
                continue
            print(f"removed {path} (after chmod -R u+w + rm -rf)")
        else:
            print(f"removed {path}")
    for repo in sorted(touched):
        git(repo, "worktree", "prune")
    if touched:
        print(f"\npruned dead registrations in {len(touched)} repo(s). Branches "
              f"survive every removal; rerun the report to see what remains.")
    else:
        print("nothing pruned: no removal happened.")
    if failed:
        print(f"{len(failed)} path(s) left in place.")
        return 1
    return 0


def main():
    doc = __doc__.splitlines()
    ap = argparse.ArgumentParser(
        description=doc[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(doc[2:]).strip(),
    )
    ap.add_argument("--days", type=int, default=31,
                    help="staleness window in days (default 31)")
    ap.add_argument("--remove", nargs="+", metavar="PATH",
                    help="remove these candidate paths")
    ap.add_argument("--confirmed", action="store_true",
                    help="required with --remove: the user approved these paths")
    args = ap.parse_args()

    if args.remove and not args.confirmed:
        print("refusing to remove without --confirmed; show the report first",
              file=sys.stderr)
        return 2

    data = collect(args.days)
    if args.remove:
        return remove(args.remove, data)
    report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
