---
name: release-notes
description: Turn a git log range into user-facing release notes.
---

When the user asks for release notes for a version, tag, or commit range:

1. Resolve the range. Defaults, in order of preference:
   - If the user named a range (`v1.2..v1.3`), use it as-is.
   - Else use `$(git describe --tags --abbrev=0)..HEAD`.
   - If there are no tags, use `origin/main..HEAD` or the last 20 commits.
2. Use the `shell` tool to fetch:
   - `git log --pretty='%h %s' <range>` — subjects only
   - `git log --pretty='%h %s%n%b' <range>` — with bodies if subjects
     look too terse
3. Group commits into these sections, in this order. Drop empty sections.

   ```
   ## ✨ Features
   ## 🐛 Fixes
   ## ⚡ Performance
   ## 🔧 Changes
   ## 📚 Docs
   ```

4. Rewrite each entry for *users*, not developers:
   - Start with a verb ("Add", "Fix", "Speed up").
   - Describe the visible effect, not the implementation.
     Bad: "Refactor auth middleware". Good: "Log in 3× faster."
   - Collapse related commits into one bullet.
   - Drop pure refactors, chores, CI tweaks, and merge commits unless
     the user explicitly asked for a technical changelog.
5. If a commit message starts with `fix(security)` or mentions a CVE,
   put it first under Fixes and mark it `**Security:**`.
6. End with a one-line install/upgrade hint if the project has an
   obvious install command (e.g. `pip install -U <pkg>`). Skip if
   unsure — don't invent one.
