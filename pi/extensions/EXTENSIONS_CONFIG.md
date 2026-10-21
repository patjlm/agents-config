# Pi Extensions Configuration Guide

This document explains how to configure the installed extensions.

## Installed Extensions

- ✅ **todo.ts** - Todo list tool for LLM
- ✅ **question.ts** - Single question with multiple choice options
- ✅ **questionnaire.ts** - Multi-question questionnaire tool
- ✅ **handoff.ts** - Context transfer to new session
- ✅ **status-line.ts** - Footer status display

---

## Extension-by-Extension Configuration

### 1. **todo.ts** - Todo List Management

**Configuration Method:** Code-based (requires extension modification)

**Configurable Elements:**

| Setting | Location | Type | Default | Purpose |
|---------|----------|------|---------|---------|
| `empty message` | Line 79 | string | "No todos yet..." | Message when no todos exist |
| `max collapsed items` | Line 97 | number | 5 | Number of items shown in collapsed view |
| `completion display` | Line 101-102 | boolean | shows completion ratio | Display done/total count |

**Example Customization (if needed, edit the file directly):**

```typescript
// Line 79 - change empty message
`No todos yet. Ask the agent to add some!`

// Line 97 - change max items in collapsed view
const display = expanded ? todoList : todoList.slice(0, 5);

// Line 101-102 - customize completion display
let listText = theme.fg("muted", `${todoList.length} todo(s):`);
```

**Usage:**
```bash
# LLM can call:
/todo list
/todo add "implement feature X"
/todo toggle 1
/todo clear

# User command:
/todos                    # Opens formatted UI
```

**State Management:**
- ✅ Survives restarts (stored in session)
- ✅ Correct on branches (reconstructed from session entries)
- ❌ Not persisted to external files

---

### 2. **question.ts** - Single Question Tool

**Configuration Method:** Tool Parameters (LLM-driven, no config file needed)

**Configurable Elements:**

| Setting | Type | Default | Purpose |
|---------|------|---------|---------|
| `question` | string | required | The question text to display |
| `options[]` | array | required | Available choices |
| `options[].label` | string | required | Display text for option |
| `options[].description` | string | optional | Additional help text |

**Usage Example (LLM calls this):**

```typescript
// LLM-invoked example:
question({
  question: "Which framework do you prefer?",
  options: [
    { 
      label: "React", 
      description: "Component-based with hooks" 
    },
    { 
      label: "Vue", 
      description: "Progressive framework" 
    },
    { 
      label: "Angular", 
      description: "Full-featured framework" 
    }
  ]
})
```

**How It Works:**
1. ↑↓ Navigate options
2. Enter - Select option
3. Select "Type something." for custom answer
4. Esc - Cancel

**Return Value:**
```typescript
{
  question: string,
  options: string[],
  answer: string | null,
  wasCustom?: boolean
}
```

**Note:** No configuration file needed - configure via tool parameters when calling

---

### 3. **questionnaire.ts** - Multi-Question Tool

**Configuration Method:** Tool Parameters (LLM-driven, no config file needed)

**Configurable Elements:**

| Setting | Type | Default | Purpose |
|---------|------|---------|---------|
| `questions[]` | array | required | Array of question objects |
| `questions[].id` | string | required | Unique identifier |
| `questions[].label` | string | "Q1, Q2..." | Tab name (short) |
| `questions[].prompt` | string | required | Full question text |
| `questions[].options[]` | array | required | Available choices |
| `questions[].allowOther` | boolean | true | "Type something" option |

**Usage Example (LLM calls this):**

```typescript
questionnaire({
  questions: [
    {
      id: "scope",
      label: "Scope",
      prompt: "What is the scope of this change?",
      options: [
        { value: "small", label: "Small", description: "< 5 files" },
        { value: "medium", label: "Medium", description: "5-20 files" },
        { value: "large", label: "Large", description: "> 20 files" }
      ],
      allowOther: false
    },
    {
      id: "priority",
      label: "Priority",
      prompt: "What is the priority level?",
      options: [
        { value: "low", label: "Low" },
        { value: "medium", label: "Medium" },
        { value: "high", label: "High" }
      ],
      allowOther: true
    },
    {
      id: "notes",
      label: "Notes",
      prompt: "Any additional notes?",
      options: [
        { value: "none", label: "No additional notes" }
      ],
      allowOther: true
    }
  ]
})
```

**How It Works:**
- **Single question:** Simple options list
- **Multiple questions:** Tab bar navigation
  - Tab navigation: Tab key or ←→ arrows
  - Option selection: ↑↓ then Enter
  - Submit tab: Shows after all answered
  - Esc - Cancel entire questionnaire

**Return Value:**
```typescript
{
  questions: Question[],
  answers: Answer[],  // { id, value, label, wasCustom, index? }
  cancelled: boolean
}
```

**Note:** No configuration file needed - configure via tool parameters when calling

---

### 4. **handoff.ts** - Context Transfer Command

**Configuration Method:** Code-based (system prompt customization)

**Configurable Elements:**

| Setting | Location | Type | Default | Purpose |
|---------|----------|------|---------|---------|
| `SYSTEM_PROMPT` | Line 24-41 | string | Multi-line prompt | Summarization instructions |
| `Goal format` | Line ~130 | string | "Usage: /handoff <goal>" | Command help text |

**System Prompt Configuration:**

The `SYSTEM_PROMPT` constant (lines 24-41) controls how the LLM generates handoff prompts. To customize:

**Default template focuses on:**
1. Summarizing relevant context (decisions, approaches, findings)
2. Listing relevant files discussed/modified
3. Clearly stating next task
4. Making it self-contained

**To modify the summarization behavior, edit the SYSTEM_PROMPT to focus on:**

```typescript
// Example: More code-focused summary
You are a context transfer assistant for code review sessions. Generate a focused prompt that:
1. Lists all code files modified or discussed
2. Summarizes technical decisions and their rationale
3. States the next technical task clearly
4. Includes specific code patterns or gotchas discovered
...

// Example: More requirement-focused summary
You are a requirements transfer assistant. Generate a focused prompt that:
1. Summarizes the feature requirements discussed
2. Lists acceptance criteria
3. Documents any constraints or dependencies
4. States the next implementation milestone
...
```

**Usage:**

```bash
# User command (not LLM-callable):
/handoff now implement this for teams as well
/handoff execute phase one of the plan
/handoff check other places that need this fix
```

**Workflow:**
1. User types `/handoff <goal>`
2. Extension extracts conversation history
3. LLM generates focused prompt using `SYSTEM_PROMPT`
4. User sees and can edit generated prompt
5. New session created with:
   - Parent session reference recorded
   - Pre-filled editor with handoff prompt
   - Ready to send when user confirms

**Return Value:** New session created with parent tracking

**Configuration Notes:**
- Current model used for summarization (respects `/model` setting)
- To use specific model, you'd need to edit line ~120-125
- Handoff respects abort signal (Esc cancels)

---

### 5. **status-line.ts** - Footer Status Display

**Configuration Method:** Code-based (theming and display)

**Configurable Elements:**

| Setting | Location | Type | Default | Purpose |
|---------|----------|------|---------|---------|
| `status key` | Line 8,14,20 | string | "status-demo" | Identifier for status line |
| `idle text` | Line 9 | string | "Ready" | Text when idle |
| `spinner char` | Line 15 | string | "●" | Animation character |
| `turn text` | Line 16 | string | " Turn N..." | During turn text |
| `complete char` | Line 21 | string | "✓" | Completion indicator |
| `complete text` | Line 22 | string | " Turn N complete" | After turn text |
| `colors` | Line 9,15,21 | theme function | dim/accent/success | Color theming |

**Current Behavior:**

```
Session start:     [Ready]
Turn starts:       [● Turn 1...]
Turn completes:    [✓ Turn 1 complete]
```

**Example Customization (edit lines 8-24):**

```typescript
// Option 1: Different status key (for multiple status displays)
ctx.ui.setStatus("my-status-key", ...);

// Option 2: Custom spinner characters
const spinner = theme.fg("accent", "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏".charAt(turnCount % 10));

// Option 3: Show more detail
const text = theme.fg("dim", 
  ` Turn ${turnCount} - Processing...`
);

// Option 4: Include model info
const modelInfo = ctx.model ? `[${ctx.model.id}]` : "";
ctx.ui.setStatus("status-demo", `${spinner}${modelInfo} Turn ${turnCount}...`);

// Option 5: Different themes per state
// During turn: warning color
// Complete: success color
// Idle: dim color
```

**Theme Colors Available:**

```typescript
theme.fg("text", "Normal text")
theme.fg("accent", "Highlighted")
theme.fg("success", "Success state")
theme.fg("warning", "Warning state")
theme.fg("error", "Error state")
theme.fg("dim", "Dimmed text")
theme.fg("muted", "Muted text")
```

**Usage Notes:**
- Status line appears in footer during agent activity
- Multiple status displays possible (use different keys)
- Auto-updates on `turn_start` and `turn_end` events
- No configuration file needed

---

## Configuration File Locations

### Where to Place Config Files

**Global (all projects):**
```
~/.pi/agent/extensions/
├── todo.config.json          ← Global config (if created)
├── question.config.json       ← N/A (tool parameters only)
├── questionnaire.config.json  ← N/A (tool parameters only)
├── handoff.config.json        ← N/A (edit extension for SYSTEM_PROMPT)
└── status-line.config.json    ← N/A (edit extension for theming)
```

**Project-local (this project only):**
```
.pi/extensions/
├── todo.config.json
└── ...
```

---

## Extensions That Don't Need Config Files

### **question.ts** and **questionnaire.ts**
- Configured entirely through **tool parameters** when LLM calls them
- No static config file approach
- Each invocation can have different questions/options

### **handoff.ts**
- Configurable by editing `SYSTEM_PROMPT` constant in extension (lines 24-41)
- Or by modifying model selection logic (line ~120)
- No external config file approach

### **status-line.ts**
- Configurable by editing theme colors and text in extension
- Or by registering multiple status displays with different keys
- No external config file approach

---

## Loading Extensions

Extensions are auto-discovered from:
```bash
~/.pi/agent/extensions/*.ts        # Global (auto-loaded)
.pi/extensions/*.ts                 # Project-local (auto-loaded)
```

Check if extensions are loaded:
```bash
pi /list-extensions    # View loaded extensions
pi /reload             # Reload extensions
```

---

## Command Reference

### Todo Extension
```bash
# LLM tool calls:
todo list              # List all todos
todo add "task"        # Add new todo
todo toggle 1          # Mark todo #1 done/undone
todo clear             # Clear all todos

# User command:
/todos                 # Show formatted todo UI
```

### Question & Questionnaire
- **No user commands** - LLM only
- Called via `question(...)` and `questionnaire(...)` tool parameters

### Handoff
```bash
# User command (not LLM-callable):
/handoff <goal>        # Transfer context to new session

# Examples:
/handoff implement this for teams
/handoff review the security implications
/handoff add tests for edge cases
```

### Status Line
- **No user commands** - Automatic display
- Shows in footer during agent activity
- Auto-updates on turn events

---

## Summary Table

| Extension | Config File | CLI Command | LLM Tool | User Command |
|-----------|-------------|-------------|----------|--------------|
| **todo.ts** | ❌ Code-based | N/A | ✅ `todo` | ✅ `/todos` |
| **question.ts** | ❌ Parameters | N/A | ✅ `question` | ❌ |
| **questionnaire.ts** | ❌ Parameters | N/A | ✅ `questionnaire` | ❌ |
| **handoff.ts** | ❌ Code-based | N/A | ❌ | ✅ `/handoff` |
| **status-line.ts** | ❌ Code-based | N/A | ❌ | ❌ (auto) |

---

## Notes

- Extensions store **state in sessions**, not external files
- State automatically branches correctly (same point in history)
- All extensions are **enabled by default**
- Use `pi /reload` after editing extension code
- Extension code is stored in `~/.pi/agent/extensions/`
