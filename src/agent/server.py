import logging
import time

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse


from .models import SolveRequest
from .orchestrator import solve_task
from .tripletex import TripletexClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agent")

app = FastAPI(title="NMiAI Tripletex Agent")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/solve")
async def solve(request: SolveRequest):
    start = time.time()

    logger.info("=" * 60)
    logger.info("NEW TASK RECEIVED")
    logger.info("Prompt: %s", request.prompt[:200])
    logger.info("Files: %s", [f.filename for f in request.files])
    logger.info("Base URL: %s", request.tripletex_credentials.base_url)

    client = TripletexClient(
        base_url=request.tripletex_credentials.base_url,
        session_token=request.tripletex_credentials.session_token,
    )

    try:
        # Multi-agent orchestrator: Chief plans → sub-agents execute → Chief adapts
        logger.info("-" * 40)
        logger.info("Starting multi-agent orchestrator...")
        result = await solve_task(request.prompt, request.files, client)

    except Exception as e:
        logger.exception("TASK FAILED with error: %s", e)
        return {"status": "error", "message": str(e)}

    elapsed = time.time() - start
    logger.info("-" * 40)
    logger.info("TASK COMPLETE in %.1fs", elapsed)
    logger.info("API calls made: %d", client.call_count)
    logger.info("API errors (4xx): %d", client.error_count)
    logger.info("=" * 60)

    return JSONResponse(content={
        "status": "completed", 
        "result": result, 
        "api_calls": client.call_count, 
        "api_errors": client.error_count}
    )
