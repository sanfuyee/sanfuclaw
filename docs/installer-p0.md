# P0 Installer — Setup Wizard + Prebuilt Binaries

> Design doc for the first slice of the "install like regular software"
> roadmap described in the project review. P0 removes the two highest
> barriers for non-programmer users: hand-edited JSON config and the
> Python toolchain prerequisite.

## Goals

1. A first-time user who **doesn't know Python, pip, venv, or JSON** can
   get sanfuclaw running on macOS, Linux, or Windows with:
   - one download from the GitHub Releases page (a single executable), and
   - one interactive command (`sanfuclaw setup`) that asks plain questions
     and writes a working `~/.sanfuclaw/config.json`.
2. The existing developer flow (`pip install -e .`) keeps working
   untouched — P0 adds capabilities, it does not remove any.

## Non-goals (deferred to P1+)

- Homebrew / Scoop / winget packaging
- Graphical installers (`.dmg`, `.msi`, `.pkg`)
- Windows background-service autostart (Task Scheduler / NSSM wrapper)
- Menu-bar / tray icon
- Bundled free-tier LLM key, or auto-detection of local Ollama
- Setup wizard flags for fully unattended installs (add once the
  interactive version has stabilized)
- Code-signing / notarization for macOS binaries

## Scope

### In scope

| Deliverable | Path |
|---|---|
| Design doc (this file) | `docs/installer-p0.md` |
| `sanfuclaw setup` interactive command | `src/sanfuclaw/cli_setup.py` |
| First-run auto-invoke of the wizard | `src/sanfuclaw/cli.py` (tweak `_ensure_home_initialized`) |
| PyInstaller spec for single-file binaries | `packaging/sanfuclaw.spec` |
| GitHub Actions release workflow (macOS × 2, Linux, Windows) | `.github/workflows/release.yml` |
| README "Quick Start for non-programmers" section + link to Releases | `README.md` |

### Out of scope

Everything listed under **Non-goals** above. These show up in P1+.

## Setup wizard design

### Command

```
sanfuclaw setup
```

Invoked two ways:

1. **Explicitly by the user** at any time — re-runs the wizard, backs up the
   existing config to `config.json.bak.<UTC-timestamp>`, then overwrites.
2. **Automatically on first run**, when `~/.sanfuclaw/config.json` is
   absent *and* stdin is a TTY. If stdin is not a TTY (systemd unit, CI,
   Docker `-d`, piped input), fall back to the current behavior: write a
   template file and log a warning to stderr with the command to run
   later.

Both paths share one implementation.

### Flow

Each step is cancellable with Ctrl-C, which aborts cleanly without touching
disk. The wizard only writes to disk at the very end.

```
┌─────────────────────────────────────────────────────────────┐
│ Step 0 ▸ Welcome banner + environment check                 │
│   Detects: python version, npx, uvx                         │
│   Outcome: informational warnings only, never blocks        │
├─────────────────────────────────────────────────────────────┤
│ Step 1 ▸ LLM provider                                       │
│   Numbered menu:                                            │
│     1. HPC-AI        (CN, subsidized — recommended)         │
│     2. Moonshot/Kimi (CN)                                   │
│     3. DeepSeek      (CN)                                   │
│     4. OpenAI        (intl)                                 │
│     5. Anthropic     (intl, native tool_use)                │
│     6. Ollama        (local, no key)                        │
│     7. Custom        (any OpenAI-compatible endpoint)       │
│   Each preset fills provider, base_url, default model.      │
├─────────────────────────────────────────────────────────────┤
│ Step 2 ▸ API key                                            │
│   `getpass`-style hidden input. Skipped for Ollama.         │
│   Each preset prints the signup URL inline.                 │
├─────────────────────────────────────────────────────────────┤
│ Step 3 ▸ Channels (multi-select, CLI always on)             │
│     [y/N] Telegram  → asks for bot_token + BotFather link   │
│     [y/N] Discord   → asks for bot_token                    │
│     [y/N] WeChat    → note: run `sanfuclaw weixin-login`    │
│                        after setup to finish QR login       │
│   WebChat needs no config — comes up via `sanfuclaw serve`. │
├─────────────────────────────────────────────────────────────┤
│ Step 4 ▸ Recommended MCP bundle (y/N, default N)            │
│   If yes: enables `time`, `fetch`, `sequential-thinking`.   │
│   Warns if `uvx` / `npx` are missing but still writes the   │
│   entries — that lets the user fix the runtime later        │
│   without re-running setup.                                 │
├─────────────────────────────────────────────────────────────┤
│ Step 5 ▸ Background autostart (darwin/linux only)           │
│   If yes: subprocess `sanfuclaw service install --enable`.  │
│   Skipped entirely on Windows (tracked as P1).              │
├─────────────────────────────────────────────────────────────┤
│ Step 6 ▸ Write config + summary                             │
│   - Back up existing config.json → config.json.bak.<TS>     │
│   - Write fresh config.json from the collected answers      │
│   - Print: "Start with: sanfuclaw start"                    │
└─────────────────────────────────────────────────────────────┘
```

### Provider presets

Hard-coded in `cli_setup.py`. Shape:

```python
@dataclass
class Preset:
    key: str           # stable id for matching
    label: str         # shown in the menu
    provider: str      # goes to llm.provider
    base_url: str      # goes to llm.base_url
    default_model: str # goes to llm.model (user can override)
    signup_url: str    # printed when user picks it
    needs_key: bool    # False for Ollama
```

Presets ship:

| key | provider | default_model | base_url |
|---|---|---|---|
| `hpc-ai` | `openai_compat` | `zai-org/glm-5.1` | `https://api.hpc-ai.com/inference/v1` |
| `moonshot` | `openai_compat` | `moonshot-v1-8k` | `https://api.moonshot.cn/v1` |
| `deepseek` | `openai_compat` | `deepseek-chat` | `https://api.deepseek.com/v1` |
| `openai` | `openai_compat` | `gpt-4o-mini` | `https://api.openai.com/v1` |
| `anthropic` | `anthropic` | `claude-sonnet-4-6` | *(unused; SDK default)* |
| `ollama` | `openai_compat` | `llama3.2` | `http://127.0.0.1:11434/v1` |
| `custom` | user input | user input | user input |

Tables like this live in one place (`_PRESETS`) and are the only source
of truth for the menu labels, defaults, and signup URLs. Adding a new
provider later means adding one row.

### Config write semantics

The wizard produces a **complete** `config.json`, not a partial one.
Rationale: we want users who re-run setup to get a clean file without
having to reason about merge corners; users with hand-tuned configs
simply shouldn't re-run the wizard (we back up the old file in case they
do).

Refactor `_default_config_text()` in `cli.py` into
`render_config(*, llm_preset, api_key, channels, mcp_bundle)` exported
from a small helper module. The current no-arg callers (`init` command,
TTY-less first-run fallback) invoke it with defaults — their output stays
byte-identical to today's.

The produced file keeps the commented-out channel/MCP examples so users
can enable more integrations later by uncommenting, matching today's
convention (see `core/config._strip_line_comments`).

Backup: if `config.json` already exists, copy it to
`config.json.bak.YYYYMMDDTHHMMSSZ` before overwriting. Never delete the
backup automatically — disk is cheap, trust is expensive.

### Environment check (Step 0)

Non-blocking. Prints a one-line summary like:

```
Environment:
  python  3.12.7      ✓
  npx     not found   (needed for: most MCP servers)
  uvx     not found   (needed for: mcp-server-time, mcp-server-fetch)
```

Detection is `shutil.which("npx")` / `shutil.which("uvx")`. We never
exit based on these checks — they exist so the user knows what to install
if they hit MCP errors later, not as a gatekeeper.

### What the wizard does *not* do

- **Doesn't validate the API key** against the provider. A bad key fails
  loudly on first message; we don't want the wizard to hang on a network
  round-trip or fail offline installs. (Adding `--test-key` is P1.)
- **Doesn't install Node.js or uv.** We point to upstream install docs if
  the user wants MCP. Bundling Node is out of scope even for P3.
- **Doesn't touch secrets in env vars.** Values entered go to
  `config.json` only. Users who prefer env-var secrets (e.g. for CI)
  keep doing that; they don't need the wizard.

## PyInstaller spec

### File

`packaging/sanfuclaw.spec` — kept out of the repo root to avoid cluttering
project-level tooling.

### What gets bundled

- Entry: `src/sanfuclaw/__main__.py` (already the `python -m sanfuclaw` entry).
- Datas: `src/sanfuclaw/webchat/index.html` and any future static assets
  under `webchat/` — PyInstaller needs these explicitly listed, they're
  not picked up from package data automatically in one-file mode.
- Hidden imports: `anthropic`, `openai`, `mcp`, `python_telegram_bot`
  submodules that are pulled in via string lookups. Exact list is
  validated empirically by running the binary and adding any missing
  module reported at startup.

### Output

`dist/sanfuclaw` (POSIX) / `dist/sanfuclaw.exe` (Windows) — single-file,
~80–120 MB uncompressed. No Python needed on the target machine.

### Known caveats

- macOS Gatekeeper: unsigned binaries trigger "cannot be opened because
  the developer cannot be verified." The user right-clicks → Open once.
  P2 adds proper signing + notarization.
- Linux glibc: Ubuntu 22.04 runner targets glibc 2.35. Older distros
  may need to build from source or use the Python path. Documented in
  README.
- `uvicorn`'s reload feature is stripped in one-file mode; we don't use
  it in prod so no user impact.

## Release workflow

### Trigger

On push of a git tag matching `v*`, e.g. `git tag v0.3.0 && git push origin v0.3.0`.

### Jobs

```yaml
jobs:
  build:
    strategy:
      matrix:
        include:
          - { os: macos-14,     arch: arm64,  asset: sanfuclaw-macos-arm64   }
          - { os: macos-13,     arch: x86_64, asset: sanfuclaw-macos-x86_64  }
          - { os: ubuntu-22.04, arch: x86_64, asset: sanfuclaw-linux-x86_64  }
          - { os: windows-latest, arch: x86_64, asset: sanfuclaw-windows-x86_64.exe }
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e . pyinstaller
      - run: pyinstaller packaging/sanfuclaw.spec
      - uses: actions/upload-artifact@v4
        with: { name: ${{ matrix.asset }}, path: dist/sanfuclaw* }

  release:
    needs: build
    runs-on: ubuntu-latest
    permissions: { contents: write }
    steps:
      - uses: actions/download-artifact@v4
      - uses: softprops/action-gh-release@v2
        with:
          files: "**/sanfuclaw*"
          generate_release_notes: true
```

Naming convention: `sanfuclaw-<platform>-<arch>[.exe]` — matches the
download links we'll put in the README.

## Documentation changes

### README.md

Insert a new top section **before** the existing `Quick Start` heading:

```markdown
## Install

### For non-programmers (recommended)

1. Download the binary for your platform from the
   [Releases page](https://github.com/sanfuyee/sanfuclaw/releases):
   - macOS (Apple Silicon): `sanfuclaw-macos-arm64`
   - macOS (Intel):         `sanfuclaw-macos-x86_64`
   - Linux (x86_64):        `sanfuclaw-linux-x86_64`
   - Windows:               `sanfuclaw-windows-x86_64.exe`
2. Make it executable (macOS/Linux only):
   `chmod +x sanfuclaw-*` and move it to somewhere on your PATH.
3. Run the setup wizard: `sanfuclaw setup`
4. Start chatting: `sanfuclaw start`

The wizard asks plain questions (which LLM provider, what API key, which
messaging channels) and writes the config for you.

#### macOS first-run note

The binary is not yet code-signed, so macOS will warn you on first launch.
Right-click the binary → Open → Open in the confirmation dialog. You
only need to do this once.

### For developers

Keep the current `pip install -e .` flow...
```

The old `Quick Start ▸ Install` content stays under **"For developers"**
unchanged.

### docs/architecture.md

Add one line to the "Current state" list:

```
- ✅ **Setup wizard** — `sanfuclaw setup` collects LLM/channel/MCP
  choices interactively, writes a complete `config.json`.
```

## Delivery checklist

- [x] `docs/installer-p0.md` written
- [x] `src/sanfuclaw/cli_setup.py` implemented
- [x] `cli.py` registers `setup` command + wires TTY-aware first-run prompt
- [x] `packaging/sanfuclaw.spec` written (build verification on CI pending
      first tag push)
- [x] `.github/workflows/release.yml` builds four artifacts on tag push
- [x] `README.md` updated with the non-programmer section
- [x] `docs/architecture.md` "Current state" list updated

## Open questions / things to validate during build

- Does PyInstaller pick up `mcp` correctly? MCP uses `anyio` + `trio`
  detection at import time; hidden-import entries may need `trio`, `trio.abc`.
- `python-telegram-bot` does model loading via entry points; verify
  the bundled binary actually connects to Telegram.
- `aiosqlite` ships a `.so`/`.dll` for sqlite3 only on some platforms;
  default to using the platform's sqlite3 runtime (the stdlib binding)
  rather than bundling to keep the binary size down.
- Whether the CLI binary should also serve `webchat/index.html`. Yes —
  it's a tiny file and removes a confusing "why is my webchat 404"
  failure mode.

If any of these blocks the release workflow, document the workaround
in this file under a new "Build notes" section rather than debugging
in CI logs.
