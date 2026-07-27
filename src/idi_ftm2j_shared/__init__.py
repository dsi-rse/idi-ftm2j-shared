"""Shared utilities for FTM2J pipeline processors.

The installed package version is available as ``__version__`` or via
``get_version()``.
"""

from importlib.metadata import version

PACKAGE_NAME = "idi-ftm2j-shared"

__version__ = version(PACKAGE_NAME)

__all__ = ["PACKAGE_NAME", "__version__", "get_version"]


def get_version() -> str:
    """Return the installed package version."""
    return __version__
