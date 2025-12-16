#!/usr/bin/env bash
set -euo pipefail

# Usage (dev):
# ./seed-config-begin.sh dev us-west-2
# ./seed-config-finish.sh

# Usage (prod):
# ./seed-config-begin.sh prod us-west-2
# ./seed-config-finish.sh

ENVIRONMENT="${1:-dev}"
REGION="${2:-us-west-2}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SEEDING_DIR="${ROOT_DIR}/seeding"
SEED_SCRIPT="${SEEDING_DIR}/seed_app_config.py"
VENV_DIR="${SEEDING_DIR}/.venv"

if [[ ! -f "$SEED_SCRIPT" ]]; then
  echo "❌ Seed script not found at: $SEED_SCRIPT"
  exit 1
fi

echo "Begin to seed app-config for env=${ENVIRONMENT} in ${REGION}..."
echo "Using: ${SEED_SCRIPT}"
echo "Venv:  ${VENV_DIR}"

# Create venv in seeding/ and install minimal deps
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

python -m pip install --upgrade pip >/dev/null
python -m pip install boto3 >/dev/null

python "$SEED_SCRIPT" --region "${REGION}" --environment "${ENVIRONMENT}"

echo "✅ Seeding step complete. Run ./seed-config-finish.sh to clean up the venv."