---
description: Automate git fetch from remote and rebase on this
allowed-tools: Bash(git:*)
---

Usually, a remote called `upstream` will be set to be tracked by the local `main` or `master` branch.

When this command is invoked:
- git fetch from this upstream remote
- rebase the current branch onto upstream/main or upstream/master
- in case of conflicts, check with the user how to progress: abort or solve conflicts

