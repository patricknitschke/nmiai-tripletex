PORT=${1:-8000}
uvicorn src.agent.server:app --reload --port $PORT