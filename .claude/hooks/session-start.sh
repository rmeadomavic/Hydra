#!/bin/bash
# session-start.sh — Provide session context and workflow reminders
# Triggered by SessionStart hook

cd "$CLAUDE_PROJECT_DIR" || exit 0

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
