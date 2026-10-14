---
name: git-workflow
description: Git workflow automation with Conventional Commits, Jira integration, and PR creation. Use when creating commits, branches, or pull requests.
allowed-tools: Bash(git:*), Bash(gh:*), AskUserQuestion, Skill
---

# Git Workflow Skill

Automates git operations following Conventional Commits specification with Jira issue tracking integration.

## Conventional Commits Format

All commits and PR titles **MUST** follow the Conventional Commits v1.0.0 specification:
<https://www.conventionalcommits.org/en/v1.0.0/>

### Commit Message Structure

```text
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

### Valid Types

- **feat**: New feature (correlates with MINOR in SemVer)
- **fix**: Bug fix (correlates with PATCH in SemVer)
- **docs**: Documentation only changes
- **style**: Code style changes (formatting, missing semicolons, etc.)
- **refactor**: Code refactoring (neither fixes bug nor adds feature)
- **perf**: Performance improvements
- **test**: Adding or updating tests
- **build**: Changes to build system or dependencies
- **ci**: Changes to CI configuration files and scripts
- **chore**: Other changes that don't modify src or test files
- **revert**: Reverts a previous commit

### Rules

- Types are **case-insensitive** (convert to lowercase)
- Description is **required** after `type[scope]:`
- Scope is **optional**, enclosed in parentheses: `feat(api):`
- Breaking changes indicated by:
  - Exclamation mark after type/scope: `feat!: breaking change`
  - `BREAKING CHANGE:` footer (triggers MAJOR version)
  - Both methods can be used together

### Examples

```text
# Simple commit
fix: resolve authentication timeout

# With scope
feat(api): add user endpoint

# Breaking change with !
feat!: remove legacy API support

# Breaking change with footer
refactor: restructure configuration

BREAKING CHANGE: configuration format has changed
```

## Jira Integration

### Branch Naming

All branches **SHOULD** reference a Jira issue:

```text
<JIRA-ID>-<descriptive-name>

Examples:
GCP-194-fix-argocd-drift
GCP-201-add-helm-chart
PROJ-123-refactor-auth
```

### Detection and Validation Logic

1. Check current branch name for Jira pattern: `^[A-Z]+-\d+`
2. If pattern found, extract Jira ID (e.g., `GCP-194`)
3. **Use jira skill to fetch ticket details**: `jira issue view {JIRA-ID} --plain`
   - Verify ticket exists and is accessible
   - Review ticket summary, description, and status
   - Understand scope and requirements
   - Check if ticket is in appropriate state (not Closed/Done)
4. If pattern not found, ask user: "Is there a Jira issue for this work? (e.g., GCP-194, or 'none')"
5. If user provides Jira ID, fetch and validate ticket details
6. If user says 'none', proceed without Jira (discouraged but allowed)

### Commit Messages with Jira

When Jira issue exists, commits can optionally reference it in the body or footer:

```text
feat: add cleanup job for old resources

This implements automatic cleanup of resources older than 30 days.

Relates to GCP-194
```

### PR Titles with Jira

PR titles must include Jira reference in parentheses at the end:

```text
feat: add cleanup job (GCP-194)
fix: resolve authentication timeout (GCP-201)
refactor!: restructure configuration (PROJ-123)
```

### PR Body with Jira

PR body should include Jira reference:

```markdown
## Summary
- Added cleanup job for old resources
- Implemented 30-day retention policy

## Related Issues
- Relates to GCP-194
- Fixes #456
```

## Workflow Operations

### Commit Validation

When user provides a commit message:

1. Parse and validate Conventional Commits format
2. Check type is valid (case-insensitive)
3. Ensure description exists after colon
4. If invalid, suggest corrected format
5. Ask for user confirmation before proceeding

**Example validation flow:**

```
User: "Add feature"
→ Invalid format (missing type)
→ Suggest: "feat: add feature"
→ Ask: "Use this format? (y/n/edit)"
```

### Branch Creation

When creating branch from main/master:

1. Check for Jira issue (current branch or ask user)
2. Analyze diff to generate descriptive name
3. Prepend Jira ID if available: `GCP-194-add-cleanup-job`
4. Ask user: "Create branch '{name}' from latest upstream? (y/n/custom)"
5. If custom, accept user's branch name
6. Execute:

```bash
git fetch upstream || git fetch origin
git stash push -m "temp for new branch"
git checkout -b {branch_name} upstream/main || origin/main
git stash pop
```

### Branch Validation

If on feature branch without Jira prefix:

1. Warn user: "Branch '{name}' doesn't reference a Jira issue"
2. Suggest: "Rename to 'GCP-XXX-{name}'? (y/n/skip)"
3. If yes, help rename branch

### PR Creation

When creating pull request:

1. Extract Jira ID from branch name
2. Analyze commits to generate conventional title
3. If single commit, use that commit message
4. If multiple commits, analyze all and create appropriate title
5. Include Jira reference in title: `feat: description (GCP-194)`
6. Generate detailed body with:
   - Summary of changes (bullet points)
   - Jira reference: "Relates to GCP-194"
   - GitHub issue refs if applicable: "Fixes #123"
7. Execute:

```bash
gh pr create --title "{title}" --body "{body}" || echo "PR may already exist"
```

## Commit Scope Handling

When adding files to commit:

- **No file arguments** → `git add .` (all changes)
- **File/path arguments** → `git add path1 path2 ...` (specific scope)
- **Always validate** paths exist and have changes before adding

## Complete PR Workflow

Full workflow from commit to PR:

1. **Jira check**: Extract from branch or ask user
2. **Jira validation**: Use jira skill to fetch ticket details
   - Verify ticket exists and is accessible
   - Review ticket summary, description, and acceptance criteria
   - Understand scope and requirements
   - Validate ticket status (should be In Progress or similar)
   - Use ticket context to inform commit message and PR description
3. **Branch check**: Create/validate branch with Jira prefix
4. **Scope determination**: All files or specific paths
5. **Commit message**: Validate/generate conventional format using Jira context
6. **Stage files**: `git add [scope]`
7. **Create commit**: `git commit -m "{message}"`
8. **Push branch**: `git push -u origin $(git branch --show-current)`
9. **Create PR**: `gh pr create --title "{title}" --body "{body}"` with Jira context
10. **Jira update**: Optionally suggest adding PR link as comment to Jira ticket
11. **Report**: Show branch, commit SHA, PR URL, committed files, Jira ticket link

## Safety Checks

Before executing operations:

- ✅ Validate specified paths exist
- ✅ Verify paths have actual changes (`git status`)
- ✅ Check branch is not main/master before committing
- ✅ Stash changes when creating new branch
- ✅ Confirm destructive operations with user
- ✅ Handle git/gh command failures gracefully

## Error Handling

- **Invalid commit format** → Suggest correction, ask confirmation
- **Missing Jira on branch** → Warn, offer to rename or proceed
- **No changes to commit** → Inform user, don't create empty commit
- **PR already exists** → Report existing PR, don't error
- **Git command fails** → Show error, explain likely cause

## Quick Reference

### Common Patterns

```bash
# All changes, auto-generate message with Jira
→ Auto-detects Jira from branch, creates conventional commit

# Specific message, all changes
"fix: resolve auth timeout"
→ Validates format, commits all changes

# Specific files with message
"feat(api): add endpoint" api/routes.go api/handler.go
→ Validates format, commits only specified files

# From main, no branch
→ Asks for Jira, generates branch name, creates branch, commits

# Feature branch without Jira prefix
→ Warns, suggests rename with Jira prefix
```

### Type Selection Guide

- New functionality → `feat`
- Bug fix → `fix`
- Documentation → `docs`
- Code cleanup/restructure → `refactor`
- Performance improvement → `perf`
- Test changes → `test`
- Build/dependency changes → `build`
- CI/CD changes → `ci`
- Maintenance → `chore`

## Integration with Jira Skill

This skill **integrates with the jira skill** to provide context-aware commits and PRs:

**When to fetch Jira details:**

- After extracting Jira ID from branch name
- Before generating commit messages
- Before creating PR title and body
- To validate ticket is in appropriate state

**What to extract from Jira:**

- Ticket summary (helps generate better commit messages)
- Ticket description (provides context for PR body)
- Ticket status (validate it's In Progress, not Closed/Done)
- Acceptance criteria (validate changes align with requirements)

**Jira skill usage:**

```bash
# View ticket details for context
jira issue view GCP-194 --plain

# Optionally add PR link to ticket after PR creation
jira issue comment add GCP-194 "PR created: https://github.com/org/repo/pull/123" --no-input
```

**Benefits:**

- Ensures commits align with ticket scope
- Generates more accurate commit messages using ticket summary
- Creates better PR descriptions with ticket context
- Validates work matches ticket requirements
- Maintains traceability between code and tickets

## Integration Notes

This skill is designed to be invoked by:

- `/git-pr` slash command
- Automatic invocation when Claude detects git workflow needs
- Explicit user request: "create a PR following our standards"

The skill enforces organizational standards for:

- Conventional Commits compliance
- Jira issue tracking and context validation
- Consistent branch naming
- Professional PR formatting
- Alignment between code changes and ticket requirements
