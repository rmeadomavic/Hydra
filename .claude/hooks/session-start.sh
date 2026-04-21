#!/bin/bash
# session-start.sh — Install deps (web sessions) and provide session context
# Triggered by SessionStart hook

set -euo pipefail

cd "$CLAUDE_PROJECT_DIR" || exit 0

# Install dependencies in remote/web sessions
if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  # Log pip output to a file rather than discarding — silent failures here cause
  # the session to start with deps half-installed and surface as confusing
  # ImportError later.
  PIP_LOG="${TMPDIR:-/tmp}/hydra-session-start-pip.log"
  : > "$PIP_LOG"

  # Mirror the CI install order in .github/workflows/ci.yml. ultralytics and
  # supervision pull heavy transitive deps (torch, etc.) — install without deps,
  # then install everything else from requirements.txt excluding those plus
  # opencv-python (the headless build is used in CI/Docker/web sessions).
  if ! pip install --no-deps ultralytics supervision >>"$PIP_LOG" 2>&1; then
    echo "WARN: pip install ultralytics/supervision failed — see $PIP_LOG" >&2
  fi
  grep -v "opencv-python\|ultralytics\|supervision" requirements.txt > /tmp/reqs.txt
  if ! pip install -r /tmp/reqs.txt >>"$PIP_LOG" 2>&1; then
    echo "WARN: pip install -r /tmp/reqs.txt failed — see $PIP_LOG" >&2
  fi
  # Dev/test extras — mirror CI (.github/workflows/ci.yml): headless OpenCV for
  # web sessions, then requirements-dev.txt for pinned pytest/hypothesis/flake8
  # etc. mypy is added separately because it powers the PostToolUse lint hook
  # but isn't a CI dep.
  if ! pip install opencv-python-headless >>"$PIP_LOG" 2>&1; then
    echo "WARN: pip install opencv-python-headless failed — see $PIP_LOG" >&2
  fi
  if ! pip install -r requirements-dev.txt >>"$PIP_LOG" 2>&1; then
    echo "WARN: pip install -r requirements-dev.txt failed — see $PIP_LOG" >&2
  fi
  if ! pip install mypy >>"$PIP_LOG" 2>&1; then
    echo "WARN: pip install mypy failed — see $PIP_LOG" >&2
  fi

  # Make hydra_detect importable for mypy/tests
  echo "export PYTHONPATH=\"$CLAUDE_PROJECT_DIR\"" >> "$CLAUDE_ENV_FILE"
fi

echo "=== Recent commits ==="
git log --oneline -5 2>/dev/null

echo ""
echo "=== Uncommitted changes ==="
STATUS=$(git status --short 2>/dev/null | head -10)
if [ -z "$STATUS" ]; then
  echo "(clean working tree)"
else
  echo "$STATUS"
  COUNT=$(git status --short 2>/dev/null | wc -l)
  if [ "$COUNT" -gt 10 ]; then
    echo "... and $((COUNT - 10)) more"
  fi
fi

echo ""
echo "=== Reminders ==="
echo "- Hardware session? Run /jetson-check before starting"
echo "- New feature? Brainstorm first with /hydra, then spec -> plan -> implement"
echo "- Debugging hardware? Research first, ask after 2 failed attempts"
