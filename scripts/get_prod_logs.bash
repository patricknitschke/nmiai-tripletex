VERSION=${1:-v13}
sudo gcloud run services logs read "pining-for-the-woods-tripletex-${VERSION}" \
  --region europe-north1 \
  --project ainm26osl-722 \
  --limit 5000
