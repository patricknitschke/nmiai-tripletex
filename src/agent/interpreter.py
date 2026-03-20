import base64
import json
import logging

from .models import FileAttachment
from .llm import complete
from .schemas import KNOWN_TASK_TYPES, get_extraction_prompt

logger = logging.getLogger("agent.interpreter")


# ── Step 1: Classify the task type ──────────────────────────────────

CLASSIFY_PROMPT = """\
You are a task classifier for Tripletex accounting software.
Read the accounting task prompt (which may be in Norwegian, English, Spanish, \
Portuguese, Nynorsk, German, or French) and identify the task type.

Known task types:
{task_types}

Additional fallback types:
- "delete_entry" — Delete or reverse an incorrect entry
- "unknown" — If the task doesn't clearly match any known type

Important mappings:
- Creating a SUPPLIER (leverandør) → use "create_customer" (suppliers are customers with isSupplier=true)
- Creating multiple resources (e.g., create customer AND then invoice) → pick the PRIMARY action (e.g., "create_invoice")

Return ONLY a JSON object: {{"task_type": "<type>"}}
"""


# ── Step 2: Extract fields guided by schema ─────────────────────────

EXTRACT_PROMPT = """\
You are a data extractor for Tripletex accounting software.
Read the task prompt and extract ONLY the fields listed below, using the EXACT \
field names specified. The prompt may be in Norwegian, English, Spanish, \
Portuguese, Nynorsk, German, or French.

{schema_prompt}

For file attachments (PDFs, images), extract any relevant data you can see \
(invoice numbers, amounts, customer names, line items, dates, etc.).

If the prompt mentions creating a dependent resource first (e.g., "create \
customer Acme, then invoice them"), include the dependent resource details \
in the appropriate field (e.g., a "customer" object).
"""


def _build_content(prompt: str, files: list[FileAttachment]) -> list[dict]:
    """Build LLM content blocks from prompt + file attachments."""
    content: list[dict] = [{"type": "text", "text": prompt}]

    for f in files:
        if f.mime_type.startswith("image/"):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": f.mime_type,
                    "data": f.content_base64,
                },
            })
            logger.info("Attached image: %s", f.filename)
        elif f.mime_type == "application/pdf":
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": f.content_base64,
                },
            })
            logger.info("Attached PDF: %s", f.filename)
        else:
            try:
                text_content = base64.b64decode(f.content_base64).decode("utf-8")
                content.append(
                    {"type": "text", "text": f"--- File: {f.filename} ---\n{text_content}"}
                )
                logger.info("Attached text file: %s", f.filename)
            except Exception:
                logger.warning("Could not decode file: %s", f.filename)

    return content


def _parse_json(raw: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks and minor corruption."""
    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Extract from markdown code block
    if "```" in raw:
        json_str = raw.split("```")[1]
        if json_str.startswith("json"):
            json_str = json_str[4:]
        try:
            return json.loads(json_str.strip())
        except json.JSONDecodeError:
            pass

    # Last resort: find the outermost { ... } and try to parse
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("No valid JSON found", raw, 0)


async def interpret_task(
    prompt: str, files: list[FileAttachment]
) -> dict:
    """Two-step interpretation: classify task type, then extract fields with schema."""

    content = _build_content(prompt, files)

    # ── Step 1: Classify ────────────────────────────────────────────
    task_type_list = "\n".join(f"- \"{t}\"" for t in KNOWN_TASK_TYPES)
    classify_system = CLASSIFY_PROMPT.format(task_types=task_type_list)

    raw_classify = await complete(classify_system, content)
    logger.info("Classify raw: %s", raw_classify[:300])

    try:
        classify_result = _parse_json(raw_classify)
        task_type = classify_result.get("task_type", "unknown")
    except (json.JSONDecodeError, IndexError):
        logger.error("Failed to parse classification: %s", raw_classify[:200])
        task_type = "unknown"

    logger.info("Classified task_type: %s", task_type)

    # ── Step 2: Extract fields using schema ─────────────────────────
    schema_prompt = get_extraction_prompt(task_type)

    if not schema_prompt:
        # Unknown task type — return minimal result for fallback
        logger.warning("No schema for task_type '%s' — skipping extraction", task_type)
        return {"task_type": task_type, "data": {}}

    extract_system = EXTRACT_PROMPT.format(schema_prompt=schema_prompt)

    data = {}
    for attempt in range(2):
        raw_extract = await complete(extract_system, content)
        logger.info("Extract raw (attempt %d): %s", attempt + 1, raw_extract[:500])

        try:
            extract_result = _parse_json(raw_extract)
            data = extract_result.get("data", extract_result)
            # Remove the task_type if it slipped into data
            data.pop("task_type", None)
            break
        except (json.JSONDecodeError, IndexError):
            logger.error("Failed to parse extraction (attempt %d): %s", attempt + 1, raw_extract[:200])
            if attempt == 0:
                logger.info("Retrying extraction...")

    logger.info("Extracted data keys: %s", list(data.keys()))
    return {"task_type": task_type, "data": data}
