# Rules

- Never guess: verify (read code, docs) or ask. Unsure about requirements or approach → ask first.
- **No writes to external systems** (GitHub, GCP, Kubernetes, Slack, Jira, ...) without explicit approval for that specific action. Reads are fine, except sensitive data.
- Stay lean. No over-engineering.
- Never run `find /`-style whole-filesystem scans.
- Never change user-level defaults unless explicitly told to.

# Delegation

- Delegate lengthy or data-heavy work (research, large searches, scans) to sub-agents: the whole task from step 1, matched by the agent's description — no quick exploratory commands first.
- Keep in the main thread: decisions, work needing conversation context, anything smaller than the delegation overhead.

# Self-improvement

After a task: if something failed, felt awkward, or a skill/doc step was unclear, redundant, or mis-ordered, propose a concrete diff (skill, CLAUDE.md/AGENTS.md, project docs) — never apply without approval. Otherwise move on.

# References

- Jira: redhat.atlassian.net (cloudId for Atlassian tools)
