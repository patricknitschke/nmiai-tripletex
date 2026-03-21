VERSION=${1:-v13}
gcloud run deploy "pining-for-the-woods-tripletex-${VERSION}" \
  --source . \
  --region europe-north1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --timeout 300 \
  --min-instances 1 \
  --concurrency 1 \
  --set-env-vars="LLM_PROVIDER=vertex,LLM_MODEL=gemini-3.1-pro-preview,GCP_PROJECT_ID=ainm26osl-722,GCP_LOCATION=global"

curl -X GET https://pining-for-the-woods-tripletex-${VERSION}-370009516620.europe-north1.run.app/health

while true; do
  clear
  gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=${SERVICE}" \
    --project ainm26osl-722 \
    --freshness=2m \
    --limit=50 \
    --format="table(timestamp,textPayload)" \
    --order=asc
  sleep 5
done