#!/bin/bash
# session-start.sh — Install deps (web sessions) and provide session context
# Triggered by SessionStart hook

set -euo pipefail

cd "$CLAUDE_PROJECT_DIR" || exit 0

# Install dependencies in remote/web sessions
if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  # ultralytics and supervision pull heavy transitive deps — install without deps
  # then install the rest from requirements.txt excluding those + opencv
  pip install --no-deps ultralytics supervision 2>/dev/null
  grep -v "opencv-python\|ultralytics\|supervision" requirements.txt > /tmp/reqs.txt
  pip install -r /tmp/reqs.txt 2>/dev/null
  pip install opencv-python-headless httpx pytest flake8 mypy 2>/dev/null

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
