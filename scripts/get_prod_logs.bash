VERSION=${1:-v13}
LIMIT=${2:-1000}
sudo gcloud run services logs read "pining-for-the-woods-tripletex-${VERSION}" \
  --region europe-north1 \
  --project ainm26osl-722 \
  --limit ${LIMIT}
