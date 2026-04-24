# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — builds a single-file `sanfuclaw` binary.

Invoke from the repo root:

    pyinstaller packaging/sanfuclaw.spec

Produces:
    dist/sanfuclaw       (POSIX)
    dist/sanfuclaw.exe   (Windows)

Design rationale: docs/installer-p0.md.
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

# SPECPATH is set by PyInstaller to the directory of this spec file.
project_root = Path(SPECPATH).parent  # noqa: F821 — injected by PyInstaller
src_root = project_root / "src"

# ---------------------------------------------------------------------------
# Hidden imports
#
# PyInstaller's static analysis misses anything imported via string / lazy
# dispatch. The packages below all do that somewhere:
#   - anthropic / openai: streaming response types
#   - mcp: anyio backends (asyncio vs trio) selected at import time
#   - telegram: handlers discovered via attribute lookup
#   - uvicorn: lifespan + protocols picked by config string
#   - pydantic_settings: sources plugin registry
# Over-collecting is cheaper than missing a module and getting a cryptic
# "No module named X" at runtime.
# ---------------------------------------------------------------------------
hidden = []
for pkg in (
    "anthropic",
    "openai",
    "mcp",
    "telegram",          # python-telegram-bot
    "uvicorn",
    "pydantic_settings",
):
    try:
        hidden += collect_submodules(pkg)
    except Exception:
        # Missing optional packages shouldn't break the build on a minimal
        # install — the relevant channel/tool just won't be usable.
        pass


# ---------------------------------------------------------------------------
# Data files
#
# gateway/server.py resolves webchat via `Path(__file__).parent.parent /
# "webchat"`, so the bundled file must land at `sanfuclaw/webchat/` inside
# the one-file archive. Listing additional static assets here is the
# supported PyInstaller pattern; package-data mechanisms (pkgutil /
# importlib.resources) don't work transparently in one-file mode.
# ---------------------------------------------------------------------------
datas = [
    (str(src_root / "sanfuclaw" / "webchat" / "index.html"),
     "sanfuclaw/webchat"),
]


# ---------------------------------------------------------------------------
# Analysis / packaging
# ---------------------------------------------------------------------------
a = Analysis(                                                     # noqa: F821
    [str(src_root / "sanfuclaw" / "__main__.py")],
    pathex=[str(src_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Exclude tests + dev-only tooling so they don't bloat the binary.
    excludes=["tests", "pytest", "_pytest", "ruff", "mypy"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)                                  # noqa: F821

exe = EXE(                                                        # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="sanfuclaw",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX often trips AV false-positives; skip it.
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,   # GitHub Actions runners already match arch to OS.
    codesign_identity=None,
    entitlements_file=None,
)
