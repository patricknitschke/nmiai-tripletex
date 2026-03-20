gcloud run deploy pining-for-the-woods-tripletex-v01 \
  --source . \
  --region europe-north1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --timeout 300 \
  --set-env-vars="LLM_PROVIDER=vertex,LLM_MODEL=gemini-2.5-flash,GCP_PROJECT_ID=ainm26osl-722,GCP_LOCATION=us-central1"