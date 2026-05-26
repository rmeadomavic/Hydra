"""Version + update-channel + last-update surface for ``GET /api/health``.

PR-A (issue #152) only stubs the OTA pipeline; these helpers expose the
two pieces of operator-visible state that the actual update path (PR-B/C)
will populate later:

* ``/etc/hydra/channel`` — one of ``stable`` or ``beta`` (single token,
  no shell metacharacters). Defaults to ``stable`` if the file is absent
  or unreadable.
* ``/var/lib/hydra/last-update.json`` — written by ``platform-update.sh``
  after each run. Shape ``{"ts": <unix>, "status": "ok"|"failed",
  "version": str}``. Returns ``None`` if absent or malformed — the
  health endpoint must never crash because of a corrupt status file.

Both paths are overridable via env (``HYDRA_CHANNEL_PATH`` /
``HYDRA_LAST_UPDATE_PATH``) so tests can pin them at a tmp path without
touching ``/etc``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)

_DEFAULT_CHANNEL_PATH = "/etc/hydra/channel"
_DEFAULT_LAST_UPDATE_PATH = "/var/lib/hydra/last-update.json"
_DEFAULT_CHANNEL = "stable"


def _read_channel_file() -> str:
    """Return the active update channel (``stable`` by default).

    Reads ``HYDRA_CHANNEL_PATH`` (or ``/etc/hydra/channel``) and returns
    the stripped first non-empty token. Any IO or decode error falls
    back to ``stable`` — the operator should never see a 500 because the
    channel file is missing on a fresh box.
    """
    path = os.environ.get("HYDRA_CHANNEL_PATH", _DEFAULT_CHANNEL_PATH)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
    except (OSError, UnicodeDecodeError) as exc:
        _log.debug("channel file unreadable at %s: %s", path, exc)
        return _DEFAULT_CHANNEL
    if not raw:
        return _DEFAULT_CHANNEL
    # First whitespace-delimited token only — keeps a stray newline or
    # trailing comment from leaking into ``/api/health``.
    return raw.split()[0]


def _read_last_update() -> Optional[Dict[str, Any]]:
    """Return the parsed last-update record, or ``None`` if absent/bad.

    Expected payload (written by a future PR-B's ``platform-update.sh``)::

        {"ts": <unix-int>, "status": "ok"|"failed", "version": "<sha>"}

    Defensive: any IO error, JSON decode error, or non-dict top-level
    yields ``None`` and a debug log line. ``/api/health`` callers treat
    ``None`` as "no update has ever run" — which is the truth on a fresh
    image.
    """
    path = os.environ.get("HYDRA_LAST_UPDATE_PATH", _DEFAULT_LAST_UPDATE_PATH)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _log.debug("last-update file at %s unreadable/malformed: %s", path, exc)
        return None
    if not isinstance(data, dict):
        _log.debug("last-update file at %s is not a JSON object: %r", path, type(data))
        return None
    return data
