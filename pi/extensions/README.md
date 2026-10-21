# Pi Extensions Directory

Welcome! This directory contains your installed Pi extensions and their configuration guides.

## 📦 What's Here

### Installed Extensions (5 total)

| # | Extension | File | Purpose |
|---|-----------|------|---------|
| 1 | **Todo** | `todo.ts` | LLM-managed todo list with `/todos` UI |
| 2 | **Question** | `question.ts` | Single-question dialog for LLM to ask user |
| 3 | **Questionnaire** | `questionnaire.ts` | Multi-question form with tab navigation |
| 4 | **Handoff** | `handoff.ts` | `/handoff <goal>` - transfer context to new session |
| 5 | **Status Line** | `status-line.ts` | Automatic footer status display |

### Documentation Files

| File | Purpose | Read If... |
|------|---------|-----------|
| **README.md** | This file - overview | You're just getting started |
| **INSTALLATION_SUMMARY.md** | Quick reference guide | You want quick answers |
| **EXTENSIONS_CONFIG.md** | Detailed configuration | You want to customize extensions |
| **`*.config.example.json`** | Configuration examples | You're customizing a specific extension |

---

## 🚀 Quick Start

### 1. Todo List
```bash
# User command to see todos:
/todos

# LLM can use these:
todo list          # Show all todos
todo add "task"    # Add new todo
todo toggle 1      # Mark done/undone
todo clear         # Clear all
```

### 2. Question Tool (LLM-called)
```typescript
// LLM calls this:
question({
  question: "Which option?",
  options: [
    { label: "Option A" },
    { label: "Option B" }
  ]
})
```

### 3. Questionnaire Tool (LLM-called)
```typescript
// LLM calls this for multi-step forms:
questionnaire({
  questions: [
    {
      id: "q1",
      label: "Question 1",
      prompt: "Your question?",
      options: [...]
    }
  ]
})
```

### 4. Handoff Command
```bash
# User command (not LLM-callable):
/handoff implement this for teams
/handoff review security aspects
/handoff add test coverage
```

### 5. Status Line
**Automatic** - shows progress in footer:
```
Ready           (when idle)
● Turn 1...     (during processing)
✓ Turn 1 done   (when complete)
```

---

## 📋 Configuration Overview

### Quick Decision Tree

**Q: Want to customize an extension?**

1. ✅ **It's a `.ts` file?**
   - Edit the file directly
   - Run `pi /reload`
   - See example `.json` file for what can change

2. ✅ **It's `question` or `questionnaire`?**
   - No static config needed
   - Configure when LLM calls (via tool parameters)
   - Each call can have different questions

3. ✅ **It's `handoff`?**
   - Edit the `SYSTEM_PROMPT` constant in `handoff.ts`
   - Run `pi /reload`
   - See `handoff.config.example.json` for ideas

4. ✅ **It's `status-line`?**
   - Edit display text/colors in `status-line.ts`
   - Run `pi /reload`
   - See `status-line.config.example.json` for options

### Configuration Method by Extension

| Extension | Config Method | Config File Needed? |
|-----------|---------------|-------------------|
| todo.ts | Edit extension code | No JSON file |
| question.ts | Tool parameters | No |
| questionnaire.ts | Tool parameters | No |
| handoff.ts | Edit SYSTEM_PROMPT | No JSON file |
| status-line.ts | Edit extension code | No JSON file |

---

## 📚 Detailed Guides

### For Beginners
Start with: **INSTALLATION_SUMMARY.md**
- Quick start examples
- Keyboard shortcuts
- Basic troubleshooting

### For Configuration
Read: **EXTENSIONS_CONFIG.md**
- Line-by-line configuration options
- Detailed examples
- How to modify each extension

### For Specific Extension
Read: **`{extension}.config.example.json`**
- What can be customized
- Default values
- How values affect behavior

---

## 🔧 Common Tasks

### Use Todo List
```bash
pi /reload          # Make sure loaded
# In conversation, or user types:
/todos              # Open UI
```

### Ask User a Question
```typescript
// LLM calls this automatically:
question({ question: "Pick one", options: [{label: "A"}, {label: "B"}] })
```

### Create Multi-Step Form
```typescript
// LLM calls this:
questionnaire({ questions: [{id: "q1", label: "Q1", ...}, ...] })
```

### Hand Off to New Session
```bash
/handoff continue with teams
# System generates prompt, you review and accept
```

### Customize Todo Display
```bash
1. Edit ~/.pi/agent/extensions/todo.ts
2. Modify lines 79, 97, 101-102
3. Save
4. Run: pi /reload
```

### Customize Status Line Colors
```bash
1. Edit ~/.pi/agent/extensions/status-line.ts
2. Change theme.fg() colors (lines 9, 15, 21)
3. Save
4. Run: pi /reload
```

---

## 💡 Pro Tips

### State Management
- ✅ Todo state **survives restarts** (stored in session)
- ✅ State is **correct on branches** (branch-aware)
- ✅ **No external files** (all in-session)

### Handoff Workflow
1. `/handoff <goal>` → extracts context
2. LLM generates focused prompt
3. **You review and edit** before sending
4. New session created with parent tracking

### Status Line
- **Automatic** - no user interaction
- **Per-session** - resets when session starts
- **Multi-display** possible (use different keys)

### Question vs Questionnaire
- **Question:** Single question with options (+ custom)
- **Questionnaire:** Multiple questions with tabs
- Both support custom user text via "Type something."

---

## 🔍 Understanding Configuration

### Why No JSON Config Files?

These extensions are **designed for simplicity**:

1. **question.ts & questionnaire.ts**
   - Configured per-call (each invocation different)
   - No static config needed

2. **todo.ts, handoff.ts, status-line.ts**
   - Simple customization (edit extension code directly)
   - Fewer dependencies
   - Easier to understand and modify

### When You Might Need Configuration

- Distribute custom versions of extensions
- Share settings across teams
- Integrate with external systems
- Need more complex customization

For these, you'd create a `.config.json` file and have the extension read it. See **EXTENSIONS_CONFIG.md** "State Management" section.

---

## 📂 File Structure

```
~/.pi/agent/extensions/
│
├── README.md                           ← You are here
├── INSTALLATION_SUMMARY.md             ← Quick reference
├── EXTENSIONS_CONFIG.md                ← Detailed guide
│
├── todo.ts                             ← Extension file
├── todo.config.json                    ← Config (if created)
├── todo.config.example.json            ← Example config
│
├── question.ts                         ← Extension file
├── question.config.example.json        ← Parameter reference
│
├── questionnaire.ts                    ← Extension file
├── questionnaire.config.example.json   ← Parameter reference
│
├── handoff.ts                          ← Extension file
├── handoff.config.example.json         ← SYSTEM_PROMPT guide
│
└── status-line.ts                      ← Extension file
    └── status-line.config.example.json ← Customization guide
```

---

## ⚡ Quick Troubleshooting

### "Extension not loading"
```bash
pi /list-extensions     # Check if listed
pi /reload              # Reload all extensions
```

### "Changes not working"
```bash
# After editing a .ts file:
pi /reload
```

### "Todo state lost"
Check:
```bash
/session-info           # Which session?
/tree                   # Which branch?
```
State is per-session and per-branch.

### "Don't see status line"
- It only shows during agent activity
- Appears in footer during turns
- Shows "Ready" when idle

---

## 📖 Further Reading

- **Pi Documentation:** Check your pi installation docs for extension development
- **Example Extensions:** See the official pi extensions directory for more examples
- **TypeScript:** Extensions use TypeScript (no compilation needed, runs via jiti)

---

## ✅ Verification Checklist

- [ ] All 5 extensions installed in `~/.pi/agent/extensions/`
- [ ] Can run `pi /list-extensions` (shows loaded extensions)
- [ ] Can run `pi /reload` (reloads successfully)
- [ ] Can type `/todos` in a conversation (opens UI)
- [ ] Read `INSTALLATION_SUMMARY.md` for quick reference
- [ ] Read `EXTENSIONS_CONFIG.md` for detailed customization

---

## 📝 Next Steps

1. **Try it out**
   - Start a conversation
   - Have LLM call `question` tool
   - Use `/todos` command
   - Try `/handoff` command

2. **Customize** (if desired)
   - Pick an extension
   - Read its `.config.example.json`
   - Edit the `.ts` file
   - Run `pi /reload`

3. **Integrate**
   - Combine extensions in prompts
   - Create workflows using them
   - Share configurations with team

---

## 📞 Support

These are official Pi extensions. For issues:
- Check `EXTENSIONS_CONFIG.md` for configuration details
- Review extension source code (well-commented)
- See pi documentation for extension API details

---

**Status:** ✅ Ready to use
**Extensions:** 5 installed  
**Config Approach:** Code-based (edit .ts files, no JSON configs)
**Last Updated:** 2024

