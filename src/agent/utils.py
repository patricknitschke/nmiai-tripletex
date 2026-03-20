"""Shared utilities for LLM content building and JSON parsing."""

import base64
import json
import logging

from .models import FileAttachment

logger = logging.getLogger("agent.utils")


def build_content(prompt: str, files: list[FileAttachment]) -> list[dict]:
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


def parse_json(raw: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks and minor corruption."""
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
