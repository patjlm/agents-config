# Pi Extensions Installation Summary

**Installed:** ✅ 5 extensions

---

## Extensions Installed

| Extension | Status | Type | Config File |
|-----------|--------|------|-------------|
| **todo.ts** | ✅ Ready | Tool + Command | Code-based |
| **question.ts** | ✅ Ready | LLM Tool | Parameters only |
| **questionnaire.ts** | ✅ Ready | LLM Tool | Parameters only |
| **handoff.ts** | ✅ Ready | User Command | Code-based |
| **status-line.ts** | ✅ Ready | Automatic | Code-based |

---

## Installation Location

All extensions installed to: `~/.pi/agent/extensions/`

```
~/.pi/agent/extensions/
├── todo.ts
├── question.ts
├── questionnaire.ts
├── handoff.ts
├── status-line.ts
├── EXTENSIONS_CONFIG.md          ← Main configuration guide
├── INSTALLATION_SUMMARY.md       ← This file
├── todo.config.example.json
├── question.config.example.json
├── questionnaire.config.example.json
├── handoff.config.example.json
└── status-line.config.example.json
```

---

## Quick Start

### 1. **Todo Tool** - Manage todos in conversation

```bash
# LLM can call these:
/todo list
/todo add "implement feature"
/todo toggle 1
/todo clear

# User command:
/todos                    # Show formatted todo UI
```

**State:** Saved in session, survives restarts and branches correctly

---

### 2. **Question Tool** - Single-question with options

LLM-invoked (no user command):
```typescript
question({
  question: "Choose framework?",
  options: [
    { label: "React", description: "Component-based" },
    { label: "Vue", description: "Progressive" }
  ]
})
```

**Features:**
- ↑↓ Navigate options
- Enter to select
- "Type something." for custom answers
- Esc to cancel

---

### 3. **Questionnaire Tool** - Multi-question form

LLM-invoked (no user command):
```typescript
questionnaire({
  questions: [
    {
      id: "scope",
      label: "Scope",
      prompt: "What scope?",
      options: [
        { value: "small", label: "Small" },
        { value: "large", label: "Large" }
      ],
      allowOther: true
    },
    {
      id: "priority",
      label: "Priority",
      prompt: "Priority level?",
      options: [...]
    }
  ]
})
```

**Features:**
- Tab-based navigation for multiple questions
- Completion tracking (□ pending, ■ answered)
- Submit tab after all answered
- Custom answers supported

---

### 4. **Handoff Command** - Transfer context to new session

```bash
# User command (not LLM-callable):
/handoff <goal>

# Examples:
/handoff implement this for teams
/handoff review security aspects
/handoff add test coverage
```

**Workflow:**
1. User types `/handoff <goal>`
2. Extension generates focused prompt using LLM
3. User reviews and can edit generated prompt
4. New session created with parent tracking
5. Ready to continue work in focused session

**Features:**
- Captures full conversation context
- Respects compaction summaries
- User can review/edit prompt
- Parent session recorded

---

### 5. **Status Line** - Footer status display

**Automatic - no user interaction needed**

```
Session start:     [Ready]
During turn:       [● Turn 1...]
Turn complete:     [✓ Turn 1 complete]
```

**Features:**
- Shows progress during agent runs
- Themed colors (accent/success/dim)
- Spinner animation
- Auto-updates

---

## Configuration

### Extensions with Config Files ❌

**None of these extensions use JSON config files.** They are configured:

1. **todo.ts** → Edit extension code (lines 79, 97, 101-102)
2. **question.ts** → Configure via tool parameters when LLM calls
3. **questionnaire.ts** → Configure via tool parameters when LLM calls
4. **handoff.ts** → Edit SYSTEM_PROMPT in extension (lines 24-41)
5. **status-line.ts** → Edit extension code (lines 8-24)

### Configuration Files Provided 📄

Example files created for reference:
- `todo.config.example.json` - Shows what can be configured
- `question.config.example.json` - Tool parameter reference
- `questionnaire.config.example.json` - Tool parameter reference
- `handoff.config.example.json` - How to customize SYSTEM_PROMPT
- `status-line.config.example.json` - Customization options

**To customize an extension:**

1. Read the example config file (e.g., `todo.config.example.json`)
2. Edit the `.ts` extension file directly
3. Run `pi /reload` to apply changes

---

## Using Extensions

### Load Extensions

Extensions are **auto-discovered** - no configuration needed:

```bash
# Auto-loaded from:
~/.pi/agent/extensions/*.ts

# View loaded extensions:
pi /list-extensions

# Reload after editing:
pi /reload
```

### In Conversation

**LLM Tools (LLM can call automatically):**
- `todo` - Todo management
- `question` - Single question
- `questionnaire` - Multiple questions

**User Commands (you type):**
- `/todos` - Show todo UI
- `/handoff <goal>` - Start handoff workflow

**Automatic (no commands):**
- `status-line` - Shows in footer automatically

---

## Advanced Customization

### For Each Extension:

#### 1. **todo.ts** - Customize display

Edit file, customize:
- Empty message (line 79)
- Max items shown (line 97)
- Completion display (line 101-102)

Then: `pi /reload`

#### 2. **question.ts** - No static config

Configure at call time via tool parameters. Each call can have different questions/options.

#### 3. **questionnaire.ts** - No static config

Configure at call time via tool parameters. Design your questionnaire per-call.

#### 4. **handoff.ts** - Customize summarization

Edit `SYSTEM_PROMPT` (lines 24-41) to change summarization focus:
- Code-focused: List files, technical decisions
- Requirement-focused: Requirements, acceptance criteria
- Spec-focused: Design decisions, architecture

Then: `pi /reload`

#### 5. **status-line.ts** - Customize display

Edit lines 8-24 to customize:
- Text messages
- Spinner character
- Color scheme
- Status key (for multiple displays)

Then: `pi /reload`

---

## State Management

### How State is Stored

All extensions store state in **session entries**, NOT external files:

✅ **Advantages:**
- Survives restarts
- Correct on branches
- History preserved
- No file clutter

❌ **Not suitable for:**
- Persistent cross-session data
- Sharing data between sessions
- External integrations

### Example: Todo State

```
Session timeline:
├── /todo add "task 1"
├── /todo add "task 2"
├── /todo toggle 1           ← Current branch records this
├── [branch]                 ← Fork here
│   ├── /todo toggle 2       ← Alternate branch has different state
│   └── /todo clear
└── /todo list               ← Original branch still has task 1 done + task 2 pending
```

---

## Keyboard Shortcuts

### Question Tool
```
↑↓        Navigate options
Enter     Select option / Submit custom answer
Esc       Cancel question / Return from custom mode
```

### Questionnaire Tool
```
Tab/←→    Navigate between questions (multi-question only)
↑↓        Navigate current options
Enter     Select option / Enter custom mode
Esc       Cancel questionnaire / Return from custom mode
```

### Todo UI (/todos)
```
↑↓        Navigate (if list was very long)
Esc       Close todo UI
```

### Status Line
```
(No interaction - automatic display in footer)
```

---

## Troubleshooting

### Extensions Not Showing Up

```bash
# Check if extensions loaded:
pi /list-extensions

# Reload extensions:
pi /reload

# Check extension files exist:
ls -la ~/.pi/agent/extensions/
```

### Changes Not Taking Effect

After editing a `.ts` file:
```bash
pi /reload
```

This reloads all extensions, skills, prompts, and themes.

### Todo State Lost

Tod state is stored in session. If state appears lost:
- Check you're in correct session (use `/session-info`)
- Verify you're on correct branch (use `/tree`)
- State is branch-specific

---

## Reference Files

| File | Purpose |
|------|---------|
| `EXTENSIONS_CONFIG.md` | Full configuration guide (read this for details) |
| `INSTALLATION_SUMMARY.md` | This file - quick reference |
| `todo.config.example.json` | What can be customized in todo.ts |
| `question.config.example.json` | Tool parameter reference |
| `questionnaire.config.example.json` | Tool parameter reference |
| `handoff.config.example.json` | SYSTEM_PROMPT customization |
| `status-line.config.example.json` | Display customization options |

---

## Next Steps

1. **Start using:** Try `/todos` command or have LLM call `todo` tool
2. **Customize:** Edit any `.ts` file and run `pi /reload`
3. **Read guides:** See `EXTENSIONS_CONFIG.md` for detailed configuration
4. **Try handoff:** Use `/handoff <goal>` to transfer context to new session

---

## Support

All extensions are examples from Pi's official distribution:
- Source: `/home/patmarti/.npm-global/lib/node_modules/@earendil-works/pi-coding-agent/examples/extensions/`
- Docs: `EXTENSIONS_CONFIG.md` in this directory
- Pi docs: See pi documentation for extension development

---

**Installation Date:** $(date)
**Extensions:** 5 installed
**Config Approach:** Code-based (no JSON config files used)
**Status:** ✅ Ready to use
