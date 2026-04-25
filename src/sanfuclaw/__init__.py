"""Sanfuclaw — A local-first personal AI agent."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sanfuclaw")
except PackageNotFoundError:
    # No installed metadata (raw checkout without `pip install`, or a
    # stripped-down build). Single source of truth lives in pyproject.toml,
    # so this fallback exists only to keep `from sanfuclaw import __version__`
    # from raising in odd contexts; real installs always read the real value.
    __version__ = "0+unknown"
