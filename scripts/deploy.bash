VERSION=${1:-v13}
gcloud run deploy "pining-for-the-woods-tripletex-${VERSION}" \
  --source . \
  --region europe-north1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --timeout 110 \
  --set-env-vars="LLM_PROVIDER=vertex,LLM_MODEL=gemini-3.1-pro-preview,GCP_PROJECT_ID=ainm26osl-722,GCP_LOCATION=global"
