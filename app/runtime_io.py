from __future__ import annotations

import sys
from typing import TextIO


def _reconfigure_utf8(stream: TextIO | None) -> None:
    """Force a Python text stream to emit UTF-8 when possible.

    Windows scheduled tasks and redirected stdout may otherwise inherit a
    legacy OEM/ANSI encoding. That can either corrupt Cyrillic in logs or
    raise UnicodeEncodeError for characters such as non-breaking hyphens.
    """
    if stream is None:
        return

    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return

    try:
        reconfigure(
            encoding="utf-8",
            errors="replace",
            write_through=True,
        )
    except (OSError, ValueError):
        # Some wrapped/closed streams cannot be reconfigured. In that case
        # leave the stream untouched rather than breaking the worker startup.
        return


def configure_utf8_stdio() -> None:
    """Use UTF-8 consistently for Python stdout/stderr."""
    _reconfigure_utf8(sys.stdout)
    _reconfigure_utf8(sys.stderr)
