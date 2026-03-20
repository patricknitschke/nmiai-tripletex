curl -X POST https://pining-for-the-woods-tripletex-v09-370009516620.europe-north1.run.app/solve \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Registrer noen nye kunder, Per johansen. per.joh@google.com. 46501234. og Kari Nordmann, kari.nordmann@google.com. 46501235.",
    "files": [],
    "tripletex_credentials": {
      "base_url": "https://kkpqfuj-amager.tripletex.dev/v2",
      "session_token": "eyJ0b2tlbklkIjoyMTQ3Njg2NDA1LCJ0b2tlbiI6IjJhYjczNTIyLTQzZWUtNDY5OC05ZDI3LTQzYzFhNDM2M2UxMiJ9"
    }
  }'