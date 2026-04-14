---
name: explain-error
description: Diagnose a stack trace or error message and suggest a concrete fix.
---

When the user shares an error message, stack trace, or failing log output:

1. Read the full trace before answering. The root cause is usually at
   the **bottom** of a Python traceback and the **top** of a JS one.
2. Identify three things, in order:
   - **What failed**: the exact operation (function + line) that threw.
   - **Why it failed**: the underlying cause, not the surface symptom.
     (`NoneType has no attribute 'foo'` is a symptom; "the lookup
     returned no row" is the cause.)
   - **The fix**: a concrete code change, not advice.
3. If the trace crosses framework/library code, ignore those frames
   unless the bug is actually there. Focus on the user's code.
4. If the error is ambiguous (e.g. could be one of several causes),
   list them in order of likelihood and say how to disambiguate —
   usually a single `print` or check the user can run.
5. Reply in this shape:

   ```
   **Root cause:** <one sentence>
   **Where:** <file:line> — <short quote of the offending line>
   **Fix:**
   ```diff
   - <old>
   + <new>
   ```
   **Why this works:** <one sentence>
   ```

6. Do not recommend `try/except` as a fix unless the exception is
   genuinely expected. Swallowing errors is not debugging.
7. If you need more information (e.g. a specific line of code, the
   value of a variable), ask for exactly that and stop.
