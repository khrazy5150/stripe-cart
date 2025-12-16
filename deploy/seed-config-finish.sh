#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SEEDING_DIR="${ROOT_DIR}/seeding"
VENV_DIR="${SEEDING_DIR}/.venv"

if [[ -d "$VENV_DIR" ]]; then
  echo "Removing virtual env at: $VENV_DIR"
  rm -rf "$VENV_DIR"
else
  echo "No virtual env found at: $VENV_DIR (nothing to do)"
fi

echo "✅ Done. The .venv is now cleared."
