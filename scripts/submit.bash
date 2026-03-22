#!/usr/bin/env bash
set -euo pipefail

# Usage: ./scripts/submit.bash <task_id>
# Submits your service URL to the competition platform for a given task.

VERSION=${1:-v79}
TASK_ID=${2:?"Usage: $0 <task_id>"}

# Competition API
API_BASE="https://api.ainm.no"
ACCESS_TOKEN="${AINM_ACCESS_TOKEN:?"Set AINM_ACCESS_TOKEN env var (copy from browser cookie)"}"

# Service URL (instance 0)
SERVICE="pining-for-the-woods-tripletex-${VERSION}-0"
REGION="europe-north1"
SERVICE_URL="https://${SERVICE}-370009516620.${REGION}.run.app"

echo "Submitting to task: ${TASK_ID}"
echo "Service URL: ${SERVICE_URL}"

RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "${API_BASE}/tasks/${TASK_ID}/submissions" \
  -H "Content-Type: application/json" \
  -H "Cookie: access_token=${ACCESS_TOKEN}" \
  -H "Origin: https://app.ainm.no" \
  -d "{\"url\": \"${SERVICE_URL}\"}")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

if [[ "$HTTP_CODE" == "201" ]]; then
  echo "Submitted! (201 Created)"
  echo "$BODY"
else
  echo "Failed with HTTP ${HTTP_CODE}"
  echo "$BODY"
  exit 1
fi
