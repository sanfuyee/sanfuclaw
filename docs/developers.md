# Developing Sanfuclaw

Everything a contributor or source-install user needs. End-user install
and configuration live in the top-level [README](../README.md) — this doc
picks up from "I want to build from source / change the code / cut a
release."

## Install from source

Requires Python ≥ 3.12. Recommended: install into an isolated venv so it
doesn't pollute the system Python.

```bash
# 1. Get the code
git clone https://github.com/sanfuyee/sanfuclaw.git
cd sanfuclaw

# 2. Create and activate a venv
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate

# 3. Upgrade pip (avoids resolver warnings on older base pythons)
python -m pip install --upgrade pip

# 4. Install (editable — source edits take effect immediately)
pip install -e .

# 5. Run the setup wizard (same as the binary flow)
sanfuclaw setup
```

All channel and MCP dependencies are bundled. The runtime only starts
what you declare in your config file (e.g. a Telegram bot under
`channels.telegram`, an MCP server under `mcp.servers.*`), so unused
integrations cost nothing beyond install size.

> Every new shell needs `source .venv/bin/activate` before `sanfuclaw` is
> on `PATH`. For a background service you skip activation — `sanfuclaw
> service install` bakes the venv's absolute path into the unit file
> (see the README's "Run as a background service" section).

The first time you run `sanfuclaw` in a terminal it prompts to launch the
interactive setup wizard. If stdin isn't a TTY (systemd, Docker `-d`,
CI) it falls back to writing a commented template to
`~/.sanfuclaw/config.json` and logs a reminder to run `sanfuclaw setup`
in an interactive shell.

## Running from a source checkout

Two equivalent entry points after `pip install -e .`:

| Form | When to use |
|------|-------------|
| `python -m sanfuclaw <cmd>` | Debug mode — runs straight out of the checkout, honors the active interpreter (venv, alt Python, `PYTHONPATH` tweaks). Tracebacks point at your working tree. |
| `sanfuclaw <cmd>` | Installed console script registered by `[project.scripts]`. Good for daily use, shell aliases, and service unit files. |

Both resolve to the same Typer app — flags, subcommands, and exit codes
are identical. Prefer `python -m sanfuclaw` when you're debugging the CLI
itself; prefer `sanfuclaw` for everything else.

### Edit-and-run loop

`pip install -e .` is the magic — `-e` (editable) drops a pointer in
`site-packages` aimed at `src/sanfuclaw/`, so source edits take effect
the next time the process starts. No reinstall, no restart of the venv.

The full debug loop:

```bash
# Terminal 1 — edit
vim src/sanfuclaw/agents/llm_agent.py

# Terminal 2 — run
python -m sanfuclaw start

# Done with this turn? Ctrl-C, save more edits, re-run. Same loop.
```

Two flags worth knowing:

- **`SANFUCLAW_HOME=/tmp/sf-debug`** — point everything (config,
  sessions DB, skills) at a throwaway directory so debug runs don't
  pollute your real `~/.sanfuclaw/`. Delete with `rm -rf /tmp/sf-debug`
  when you're done.
- **`--resume <id>`** — replay a specific session. Combined with
  `breakpoint()` inside a hot path (e.g. `_build_openai_tool_messages`),
  you can reproduce a multi-turn bug at exactly the failing step:

  ```bash
  python -m sanfuclaw start --resume cli-4cea
  # next user message hits the breakpoint
  ```

  `pp messages` in the resulting pdb prompt is the fastest way to see
  what's actually being sent to the LLM.

## Uninstall (source install)

```bash
pip uninstall sanfuclaw
```

`pip uninstall` removes only the Python package — Python packaging has
no post-uninstall hook, so user data under `~/.sanfuclaw/` (config,
sessions, credentials, skills, memory) stays put. Delete it explicitly
with `rm -rf ~/.sanfuclaw/` if you want a clean slate.

Stop any background service first (`sanfuclaw service uninstall`) so
systemd/launchd don't try to respawn a process whose binary just
disappeared.

## Tests and lint

Install dev extras once, then run the usual suspects:

```bash
pip install -e ".[dev]"          # adds pytest, pytest-asyncio, ruff, mypy
pytest                           # full test suite
pytest tests/test_storage -v     # narrow to a module
ruff check src/                  # lint
ruff format src/                 # auto-format
mypy src/                        # type-check (not yet gating in CI)
```

`pytest` config (async mode, pythonpath) is pinned in `pyproject.toml`
under `[tool.pytest.ini_options]` — no separate `conftest.py` or `pytest.ini`.

## Building a binary locally

Verifies the PyInstaller spec before pushing a release tag. Skip if you
only care about the Python package.

```bash
pip install pyinstaller
pyinstaller packaging/sanfuclaw.spec
./dist/sanfuclaw version         # smoke test (→ prints `sanfuclaw vX.Y.Z`)
./dist/sanfuclaw --help          # verify typer wiring
```

The spec over-collects hidden imports for SDKs with lazy dispatch
(`anthropic`, `openai`, `mcp`, `telegram`, `uvicorn`, `pydantic_settings`).
If a runtime error reports "No module named X" from inside the binary,
add `X` to the `hidden` list in `packaging/sanfuclaw.spec` and rebuild.

Design rationale: [docs/installer-p0.md](installer-p0.md).

## Cutting a release

CI publishes four single-file binaries (macOS arm64/x86_64, Linux
x86_64, Windows x86_64) whenever a `v*` tag is pushed.

```bash
# 1. Make sure `dev` is green locally
pytest && ruff check src/

# 2. Merge dev → main (PR or fast-forward, per your preference)
git checkout main && git merge --ff-only dev && git push origin main

# 3. Tag and push. Use -rcN for release candidates, drop the suffix for
#    the real thing. The workflow is triggered by the tag, not the branch.
git tag -a v0.3.0 -m "0.3.0"
git push origin v0.3.0
```

The workflow lives at `.github/workflows/release.yml`. A manual
`workflow_dispatch` build from the Actions tab is fine for dry-runs — it
builds the binaries but skips the Release publish step.

## Further reading

- [docs/architecture.md](architecture.md) — internals, hub-and-spoke
  layout, design decisions
- [docs/concepts.md](concepts.md) — conceptual primer on
  `system_prompt` / tools / skills / memory / history (what enters the
  LLM prompt each turn)
- [docs/installer-p0.md](installer-p0.md) — setup wizard and PyInstaller
  release pipeline design
- `config.example.json` — every config knob with inline comments
