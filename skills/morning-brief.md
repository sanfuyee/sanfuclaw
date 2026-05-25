---
name: morning-brief
description: Compose a daily morning brief — weather, top news, and the day's plan.
---

When the user asks for their morning brief, or when the scheduler fires
this skill via a cron prompt like "Generate my morning brief", produce a
single Markdown report following the structure below. **Issue all data-
gathering tool calls in parallel in one round** — there are no
dependencies between weather, news, and clipboard reads, so serial
fetches just waste tool rounds.

## Inputs to gather (parallel, single round)

1. `weather` — for the user's default city. If the user has previously
   given you a city in this session, use it; otherwise default to
   `Shanghai` and mention the assumption in the brief.
2. `web_fetch` — `https://news.ycombinator.com/` for tech headlines.
   Extract the top 5 story titles + links.
3. `web_fetch` — `https://hnrss.org/frontpage` (RSS, plaintext-friendly)
   as a fallback if the HTML fetch returns junk or 5xx.
4. `clipboard_read` — only call this if the user explicitly asked you to
   include "what I just copied" in the brief. Otherwise skip.

## Format

Render exactly this template, in this order, with no preamble or
trailing commentary:

```
☀️ Good morning. Here's your brief for {today}.

🌤  Weather — {city}
{one-line weather summary from the weather tool}

📰 Top of HN
1. {title} — {url}
2. {title} — {url}
3. {title} — {url}
4. {title} — {url}
5. {title} — {url}

📋 Notes
{omit this whole section unless the user asked you to include
clipboard or other notes}

— Have a good one.
```

## Rules

- Replace `{today}` with today's full date (e.g. `Wednesday, May 8 2026`).
- If a tool returns an error, write `(unavailable: {reason})` in that
  section instead of the data — never silently drop a section, and never
  invent placeholder data.
- Keep the whole report under ~30 lines. Strip URL query strings to
  shorten links.
- Do **not** call `task_write` for this skill — the brief is one-shot,
  not a multi-step plan.

## Scheduling this skill

The skill runs whenever the agent receives a prompt that triggers it.
To make it proactive, ask the agent (in any chat) to schedule it, e.g.:

> Schedule my morning brief at 8am every day to the CLI channel.

The agent will use `schedule_create` with cron `0 8 * * *` and a prompt
like `Generate my morning brief`, which the agent will recognize and
load this skill for.
