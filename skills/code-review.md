---
name: code-review
description: Review a code diff or file and report issues bucketed by severity.
---

When the user asks you to review code, a diff, or a pull request:

1. If the user provided a file path or git reference (e.g. "review the
   last commit"), use the `shell` tool to fetch the content — prefer
   `git diff <ref>`, `git show <ref>`, or `cat <path>`.
2. Read the code carefully before forming an opinion.
3. Report findings in exactly three buckets. Skip any bucket that has no
   items rather than padding it with weak remarks.

   ```
   ## 🔴 Bugs
   - <file:line> — <one-sentence problem> → <one-sentence fix>

   ## 🟡 Smells
   - <file:line> — <issue> → <suggestion>

   ## 🟢 Nits
   - <file:line> — <nit>
   ```

4. Rules for the review itself:
   - Focus on correctness, security, and concurrency first. Style last.
   - Cite file:line so the user can jump to the issue.
   - Do not restate what the code does. Do not praise.
   - If you're unsure whether something is a bug, put it in 🟡 Smells
     with "verify: …" rather than inventing certainty.
   - No more than 10 items total unless the user asks for exhaustive.
5. End with a one-line verdict: `Verdict: ship it` / `Verdict: fix bugs first` /
   `Verdict: needs rework`.
