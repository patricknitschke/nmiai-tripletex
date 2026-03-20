curl -X POST http://localhost:8000/solve \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Opprett en ansatt med navn Kari Nordmann, e-post kari@example.no. Hun skal være kontoadministrator.",
    "files": [],
    "tripletex_credentials": {
      "base_url": "https://kkpqfuj-amager.tripletex.dev/v2",
      "session_token": "eyJ0b2tlbklkIjoyMTQ3Njg2NDA1LCJ0b2tlbiI6IjJhYjczNTIyLTQzZWUtNDY5OC05ZDI3LTQzYzFhNDM2M2UxMiJ9"
    }
  }'