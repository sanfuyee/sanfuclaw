# Concepts: system_prompt, tools, skills, memory, history

> Conceptual primer on the five things that make up an LLM turn in
> sanfuclaw. For subsystem internals see [architecture.md](architecture.md).

Every time the agent calls the LLM, it assembles a request from five
distinct sources. They differ by **who writes them**, **when they
enter the prompt**, and **how long they live**. Confusing any two is
usually the root cause of "why did the model forget?" or "why is this
turn so expensive?" questions.

## One-table summary

| Concept | What it is | Written by | Enters prompt each turn? | Lifetime | In sanfuclaw |
|---------|------------|------------|--------------------------|----------|--------------|
| **system_prompt** | Base instructions, identity, global rules | User / runtime | **Yes, in full** | Frozen at startup | `config.system_prompt` + SCHEDULE / TOOL guidance + skills index + memory index |
| **tool** | A callable capability (executes an action) | Developer | **Schema only** (name + description + params) | Frozen at startup | `shell`, `web_fetch`, `load_skill`, `load_memory`, `schedule_*`, MCP tools |
| **skill** | A reusable task recipe (markdown) | User / developer | **Index only** (one line: `name: description`) | File on disk | `~/.sanfuclaw/skills/*.md`, full body fetched via `load_skill` |
| **memory** | A persistent cross-session note (markdown) | User (and a future LLM write path) | **Index only** (`MEMORY.md` verbatim) | File on disk | `~/.sanfuclaw/memory/*.md`, full body fetched via `load_memory` |
| **history** | What has been said and done in this session | LLM + user jointly | **Yes** (trimmed to fit token budget) | Persisted in SQLite | `Session.history` |

## What goes to the LLM on one turn

```
┌─────────────────────────────────────────────────────────────┐
│  SYSTEM PROMPT                                ← every turn, │
│  ├─ identity: "You are Sanfuclaw..."            in full     │
│  ├─ rules:    SCHEDULE_PROMPT_GUIDANCE                      │
│  ├─ rules:    TOOL_EFFICIENCY_GUIDANCE                      │
│  ├─ skills index:  "- weather-report: check weather..."     │
│  └─ memory  index: MEMORY.md (verbatim, ≤200 lines)         │
├─────────────────────────────────────────────────────────────┤
│  TOOLS (schemas)                              ← every turn, │
│  [shell, web_fetch, load_skill, load_memory,    in full     │
│   schedule_create, ...]                                      │
├─────────────────────────────────────────────────────────────┤
│  HISTORY                                      ← trimmed to  │
│  user:      "help me write release notes"       token budget│
│  assistant: (tool_use: load_skill)                          │
│  tool:      # Skill: release-notes\n...full body...         │  ← skill enters context here
│  assistant: (tool_use: shell, `git log ...`)                │
│  tool:      <git log output>                                 │
│  assistant: <final reply>                                    │
└─────────────────────────────────────────────────────────────┘
```

Key observation: skills and memory **don't** live in the prompt.  Their
**indices** do.  Their **bodies** only land in the history when the LLM
actually calls `load_skill` / `load_memory`.  That's the whole point of
the design — you pay for a page of detailed instructions only when it's
relevant, not on every turn.

## Three distinctions people mix up

### 1. tool vs. skill — "doing" vs. "telling"

- **tool** is an *action*. `shell` actually runs commands; `web_fetch`
  actually hits URLs. Written in Python; the LLM never sees the code,
  only the schema and the result.
- **skill** is *instructions*. A markdown file that says "when you
  generate release notes, first call `shell` with `git log`, then..."
  No implementation — just guidance on how to use tools.

Skills reference tools. Tools do not reference skills. A skill is
powerless on its own; it's a recipe that assumes a kitchen.

### 2. skill vs. memory — "how to do things" vs. "what I know"

Mechanically identical: directory of markdown, index in the system
prompt, lazy-loaded via a tool. They share the same `_parse_frontmatter`
loader on purpose. The difference is **content semantics**:

- **skill**: a reusable procedure anyone could follow. *"To do code
  review, check for X, Y, Z."* Usually committed to git.
- **memory**: a fact about this user or project that no one else needs.
  *"This user prefers terse replies."* *"Merge freeze starts 2026-03-05."*
  Usually private, curated over time.

Step 1 of sanfuclaw's memory port is read-only (hand-edited files);
Step 2 will add `save_memory` / `update_memory` / `forget_memory` so
the LLM can curate notes mid-conversation.

### 3. system_prompt vs. history — "rules" vs. "events"

- **system_prompt** is static: "you must..." / "you are...". Same every
  turn regardless of conversation.
- **history** is dynamic: a growing list of messages — user input,
  assistant replies, tool calls, tool results.

Both are sent every turn, but only history is subject to token-budget
trimming. The system_prompt is counted as fixed overhead in
`LLMAgent._fit_history_to_budget`, and the trimmer drops the oldest
history messages to make room for new ones.

## Why the layering exists: **token cost**

Every token in the system_prompt costs money on **every turn**. Every
token in history costs money **for as long as the session hasn't
trimmed it past that message**.

If all skills and all memories were concatenated into the system_prompt:

- A library of 50 skills × 2 KB each = 100 KB of context tax on every
  single LLM call, even when zero skills are relevant.
- Memory accumulated over months could easily hit 10–50 KB of context
  tax per turn, forever.

Splitting into *index + lazy body* means:

- The index stays ~one line per entry (under 100 tokens for a dozen
  entries).
- A body enters history only when actually needed, and then it's
  subject to history trimming — once the conversation moves on, it
  falls out of the window naturally.

This is also why the **write side** of memory (Step 2) has to be
careful. An LLM that enthusiastically saves everything will balloon
`MEMORY.md` and make every future turn more expensive. Claude Code's
auto-memory design has a prominent "What NOT to save" section for
exactly this reason, and sanfuclaw's Step 2 will adopt the same
discipline.

## Quick mental model

> **system_prompt** = employee handbook (you must follow)
> **tools**         = the equipment in the workshop (you can use)
> **skills**        = procedure manuals on the shelf (fetch when needed)
> **memory**        = your notebook about the customer (their preferences)
> **history**       = today's work log (what just happened)

## Where each one lives in the code

| Concept | Source of truth | Composition / trimming |
|---------|-----------------|------------------------|
| system_prompt | `Settings.llm.system_prompt` + guidance constants in `gateway/wiring.py` + `SkillRegistry.system_prompt_block()` + `MemoryRegistry.system_prompt_block()` | Concatenated once in `gateway/wiring.py::build_router`; frozen into `LLMAgent._system_prompt` |
| tool | `ToolRegistry` in `tools/registry.py`; schemas from each tool's `name`, `description`, `parameters_schema` | `ToolRegistry.to_llm_schemas()` called every turn |
| skill | `~/.sanfuclaw/skills/*.md` | Index injected at startup; body fetched on `load_skill` tool call |
| memory | `~/.sanfuclaw/memory/*.md` + `MEMORY.md` | Index injected at startup; body fetched on `load_memory` tool call |
| history | `Session.history` (in memory) + `messages` table (SQLite) | Trimmed per-turn by `LLMAgent._fit_history_to_budget` |
