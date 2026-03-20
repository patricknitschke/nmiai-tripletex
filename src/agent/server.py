import logging
import time

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse


from .models import SolveRequest
from .interpreter import interpret_task
from .router import route_task
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
        # Step 1: Interpret the task with Claude
        logger.info("-" * 40)
        logger.info("STEP 1: Interpreting task with Claude...")
        interpretation = await interpret_task(request.prompt, request.files)

        task_type = interpretation.get("task_type", "unknown")
        extracted_data = interpretation.get("data", {})
        logger.info("Interpreted task_type: %s", task_type)
        logger.info("Extracted data: %s", extracted_data)

        # Step 2: Route and execute
        logger.info("-" * 40)
        logger.info("STEP 2: Routing to workflow...")
        result = await route_task(task_type, extracted_data, client, request.prompt, request.files)

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
