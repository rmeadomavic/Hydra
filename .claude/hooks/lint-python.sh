#!/bin/bash
# lint-python.sh — Auto-run flake8 and mypy on edited Python files
# Triggered by PostToolUse hook on Edit/Write

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only lint Python files
if [[ ! "$FILE_PATH" =~ \.py$ ]]; then
  exit 0
fi

# Skip if file was deleted
if [[ ! -f "$FILE_PATH" ]]; then
  exit 0
fi

# Run flake8 (style + errors)
if command -v flake8 &> /dev/null; then
  echo "--- flake8 ---"
  flake8 --max-line-length 100 --ignore W503 "$FILE_PATH" 2>&1
fi

# Run mypy (type checking)
if command -v mypy &> /dev/null; then
  echo "--- mypy ---"
  mypy --ignore-missing-imports --no-error-summary "$FILE_PATH" 2>&1
fi

# Always exit 0 so Claude can proceed — findings are advisory
exit 0
