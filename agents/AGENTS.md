# Global Rules

- Never guess — always verify by reading code, docs, or asking.
- If unsure about requirements or approach, ask before proceeding.
- **Never write to any external system** (create/update/delete on GitHub, GCP, Kubernetes, Slack, Jira, etc.) without explicit user approval for that specific action. Reading is fine except for sensitive data.
- Use sub-agents for lengthy or data-heavy tasks (research, large searches, exploration, file/jira/github/slack/... scanning, ...).
- Stay focused and lean. Do not over-engineer.
- bypass sandbox to git commit: gpg agent access is blocked, do *NOT* try to start it
- bypass sandbox to git push: we are usually using ssh, do *NOT* try to use HTTPS
- bypass sandbox to `gh` commands: token is stored in keyring, which sandbox blocks
- bypass sandbox to `terraform` commands: provider binaries need execution access sandbox blocks
- when running git commands in non-current folders, prefer using option `-C` rather that changing directory

# Optional self-improvement

After completing an action, briefly reflect on your execution:
- Did anything fail, feel awkward, or require unnecessary retries?
- Were you missing context that CLAUDE.md or another project doc should have provided?
- Is there a step in a skill or documentation that was unclear, redundant, or in the wrong order?

If you identify a concrete improvement, suggest it to the user as a diff to the relevant file (skill definition, CLAUDE.md, AGENTS.md, etc., at the repo or user level). Do not apply changes without approval. If nothing stands out, move on — do not force feedback.