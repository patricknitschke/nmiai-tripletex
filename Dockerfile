FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY src/ src/
COPY docs/tripletex_openapi.json docs/tripletex_openapi.json

CMD ["uvicorn", "src.agent.server:app", "--host", "0.0.0.0", "--port", "8080"]
