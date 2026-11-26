---
name: t3-cleanup-1m-orphaned-worktrees
description: Find T3 Code git worktrees that no live thread points at, report the clean ones with a last commit older than a month, and remove them only after the user approves. Use when cleaning up T3 worktrees or reclaiming disk space from T3 Code.
allowed-tools: Bash(git:*), Bash(uv:*), Bash(sqlite3:*), Read, AskUserQuestion
---

# T3 Code: remove orphaned worktrees

T3 Code registers a git worktree per thread and deletes it with the thread.
Killed or badly archived threads leave the worktree registered but unowned.
Find those, show the stale ones, remove them once the user approves.

## Safety rules

- **Remove nothing before the user approves.** Print the candidates with their
  last commit date and dirty state, then stop and ask. Approval must name paths.
- Read-only until then: SQLite opens `mode=ro`, git is only `list`, `status`,
  `log`, `rev-list`.
- Read the thread tables again in the same run that removes (the script does).
- One worktree at a time, each with its own repo:
  `git -C <that worktree's project root> worktree remove <path>`.
- Never delete `.t3/worktrees/*` as a batch; the t3 server owns that tree.

## Flow

```sh
cd ~/dev/patjlm/agents-config
S=skills/t3-cleanup-1m-orphaned-worktrees/scripts/orphan_worktrees.py
uv run $S                                   # report only, ~2 s
uv run $S --remove <path> [<path>...] --confirmed   # after approval
```

1. Run `uv run $S`. It prints candidates as
   `path / repo / branch / last commit (age) / clean|dirty`, plus locked or
   unpushed notes, and lists what it did not offer and why.
2. Show the candidate block verbatim and ask the user to approve those paths.
   No candidates means nothing to do: say so and stop.
3. Remove with `--remove ... --confirmed`, listing only approved paths. It
   refuses anything that is no longer a candidate and prunes the repos it
   removed from.
4. Rerun the report and tell the user what remains and why.

`--days N` moves the staleness window (default 31).

## Data sources

| What | Where |
|---|---|
| Projects | `~/.t3/userdata/statev2.sqlite`, `projection_projects.workspace_root` (`deleted_at is null`) |
| Threads | same DB, `orchestration_v2_projection_threads`, `payload_json` → `$.worktreePath`, `$.deletedAt` |
| Legacy threads | `~/.t3/userdata/state.sqlite`, `projection_threads.worktree_path` |
| Worktrees | `git -C <workspace_root> worktree list --porcelain`, minus the main checkout |

Skip project roots without `.git` (`~/.t3/scratch*` are plain dirs). Compare
paths with `os.path.normpath`; a trailing slash otherwise looks like an orphan.

## Classification

- **Bound**: a live thread points at it. Settled, snoozed, archived and subagent
  threads count. Deleted threads do not.
- **Orphan**: registered worktree with no live thread.
- **Candidate**: orphan, `git status --porcelain` empty, last commit older than
  the window. Locked entries are candidates too.
- **Not offered**: dirty, too recent, or unreadable by git — reported with the
  reason.
- **Dead registration**: directory gone; the report prints the
  `git -C <repo> worktree prune` command for it. Removals prune their own repo.
- **Stale thread pointer**: live thread pointing at a path that no longer
  exists. Common (dozens). Nothing to remove.

## Traps

- **Locked**: a git lock only blocks `worktree prune`, so a locked orphan is
  still a candidate; removal adds `--force`. Report the lock reason verbatim.
- **Read-only leftovers**: worktrees holding `hack/tools/bin/envtest-kube/…`
  have `r-x` files in a `dr-x` dir, so `worktree remove` deregisters the
  worktree and then fails to delete the tree. The script retries with
  `chmod -R u+w <path>` and `rm -rf <path>`; same by hand.
- **Unreadable worktree** (stale `.git` gitfile): git fails on every command, so
  git cannot remove it. Report it; do not force anything.
- **Detached HEAD**: its commits survive removal only if a ref still points
  there. Branches survive `worktree remove`, so say so in the report.
