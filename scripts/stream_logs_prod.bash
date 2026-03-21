VERSION=${1:-v13}
SERVICE="pining-for-the-woods-tripletex-${VERSION}"

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