---
description: Perform deep web research with context-aware query enhancement
---

You are about to perform a deep web research task using the web-research-specialist agent.

## Instructions

1. **Analyze the research request**: The user has requested research on a specific topic. Review their request carefully.

2. **Enhance the query with context**:
   - If there's relevant conversation context (recent messages, files discussed, current work), incorporate it into the research prompt
   - Add specific details that would help get better research results
   - Include any technical constraints, preferences, or requirements from the conversation
   - If the user's request is already comprehensive, use it as-is

3. **Delegate to web-research-specialist agent**:
   - Use the Task tool with `subagent_type="web-research-specialist"`
   - Pass the enhanced research prompt
   - The agent should return a comprehensive summary with sources

4. **Present the results**:
   - Share the research findings with the user in a clear, organized format
   - Include key insights and actionable information
   - Cite sources when relevant

## Example Enhancement

**User request**: "research best practices for CLI tools"

**Enhanced query**: "Research best practices for building CLI tools in Python, focusing on:
- Argument parsing libraries (comparison of argparse, click, typer)
- User experience patterns (progress indicators, colors, error handling)
- Distribution methods (pip, pipx, uv)
- Testing strategies for CLI applications
Context: We're working on a Python CLI tool that uses uv for dependency management and typer for the CLI framework."

## Important Notes

- Always use the web-research-specialist agent - don't attempt web research yourself
- Be specific in your research prompts to get better results
- If the user's query is ambiguous, enhance it with reasonable assumptions based on context
- The goal is to save the user time by providing comprehensive, well-sourced information
