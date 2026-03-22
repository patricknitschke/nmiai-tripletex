#!/usr/bin/env bash
set -euo pipefail

VERSION=${1:-v13}
PROJECT_ID="ainm26osl-722"
REGION="europe-north1"
REPOSITORY="tripletex-images"
SERVICE="pining-for-the-woods-tripletex-${VERSION}"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${SERVICE}"
TAG="$(date +%Y%m%d-%H%M%S)"

if ! gcloud artifacts repositories describe "${REPOSITORY}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${REPOSITORY}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Cached Cloud Run images for NM i AI Tripletex" \
    --project="${PROJECT_ID}"
fi

gcloud builds submit . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --config cloudbuild.cached.yaml \
  --substitutions "_IMAGE=${IMAGE_BASE},_TAG=${TAG}"

gcloud run deploy "${SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE_BASE}:${TAG}" \
  --allow-unauthenticated \
  --memory 2Gi \
  --timeout 300 \
  --min-instances 1 \
  --concurrency 1 \
  --set-env-vars="LLM_PROVIDER=vertex,LLM_MODEL=gemini-3.1-pro-preview,GCP_PROJECT_ID=${PROJECT_ID},GCP_LOCATION=global"

curl -X GET "https://${SERVICE}-370009516620.${REGION}.run.app/health"