---
description: Automate git commit, push, and PR creation workflow with safety checks
argument-hint: [optional: "commit message"] [optional: file/path file/path ...]
allowed-tools: Bash(git:*), Bash(gh:*), Bash(jira:*), AskUserQuestion, Skill
---

Execute the complete git PR workflow using the git-workflow skill with arguments: $ARGUMENTS

The git-workflow skill will:

1. **Validate Jira issue** - Extract from branch or prompt for issue number
2. **Fetch Jira context** - Use jira skill to retrieve ticket details (summary, description, status)
3. **Validate alignment** - Ensure changes align with ticket scope and requirements
4. **Check branch status** - Create new branch from main if needed, validate Jira prefix
5. **Validate commit message** - Ensure Conventional Commits format compliance
6. **Stage changes** - Add all files or specific paths as specified
7. **Create commit** - With validated conventional message informed by Jira context
8. **Push branch** - To origin with upstream tracking
9. **Create PR** - With conventional title including Jira reference and detailed body using ticket context
10. **Update Jira** - Optionally add PR link as comment to the Jira ticket

**Argument parsing**:

- First quoted string = commit message (will be validated for Conventional Commits format)
- Remaining arguments = file paths to stage (if none, stages all changes)

**Examples**:

- `/git-pr` → Full workflow with auto-generated message
- `/git-pr "fix: resolve authentication timeout"` → All changes with specified message
- `/git-pr "feat(api): add user endpoint"` → Conventional format with scope
- `/git-pr helm/` → Only helm directory, auto-generated message
- `/git-pr "refactor: cleanup" file1.go file2.go` → Specific files with message

The git-workflow skill handles all Conventional Commits validation, Jira integration, and PR formatting automatically.
