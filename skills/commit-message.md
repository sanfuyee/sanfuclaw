---
name: commit-message
description: Generate a Conventional Commits message from the current staged diff.
---

When the user asks for a commit message:

1. Use the `shell` tool to gather context:
   - `git diff --cached --stat` — which files and how big
   - `git diff --cached` — the actual changes
   - `git log --oneline -5` — match the repo's tone
2. If nothing is staged, run `git diff --stat` + `git diff` on the
   working tree instead, and note at the end that the user should
   `git add` first.
3. Classify the change into a Conventional Commits type:
   `feat` (new capability) · `fix` (bug fix) · `refactor` (no behavior
   change) · `perf` · `docs` · `test` · `chore` · `build` · `ci`.
4. Produce **one** commit message in this format:

   ```
   <type>(<scope>): <imperative summary, ≤72 chars>

   <optional body — only if the *why* is non-obvious. 1-3 short lines.
   No bullet lists of what changed — the diff already shows that.>
   ```

5. Rules:
   - Subject line in imperative mood ("add", not "added" or "adds").
   - No trailing period on the subject.
   - Omit the body entirely if the subject is self-explanatory.
   - Never mention tool names, agents, or that the message was generated.
   - If the diff spans unrelated changes, point that out and suggest
     splitting into multiple commits — don't force one message.
6. Output only the message inside a fenced code block, then nothing else.
