"""LLM abstraction layer — supports Anthropic (Claude) and Vertex AI (Gemini)."""

import json
import logging
import os

logger = logging.getLogger("agent.llm")


def _get_config() -> tuple[str, str]:
    """Return (provider, model) from env vars."""
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    default_model = "claude-sonnet-4-6" if provider == "anthropic" else "gemini-2.0-flash"
    model = os.environ.get("LLM_MODEL", default_model)
    return provider, model


# ---------------------------------------------------------------------------
# Simple completion (used by interpreter)
# ---------------------------------------------------------------------------

async def complete(system: str, user_content: list[dict], max_tokens: int = 2048) -> str:
    """Send a single message and return the text response."""
    provider, model = _get_config()
    logger.info("LLM complete: provider=%s, model=%s", provider, model)

    if provider == "anthropic":
        return await _anthropic_complete(system, user_content, model, max_tokens)
    elif provider == "vertex":
        return await _vertex_complete(system, user_content, model, max_tokens)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}. Use 'anthropic' or 'vertex'.")


async def _anthropic_complete(
    system: str, user_content: list[dict], model: str, max_tokens: int
) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text.strip()


async def _vertex_complete(
    system: str, user_content: list[dict], model: str, max_tokens: int
) -> str:
    from google import genai

    project = os.environ.get("GCP_PROJECT_ID")
    default_location = os.environ.get("GCP_LOCATION", "europe-north1")
    # Gemini 3.x models require "global" location
    location = "global" if model.startswith("gemini-3") else default_location
    client = genai.Client(vertexai=True, project=project, location=location)

    parts = _to_gemini_parts(user_content)
    response = await client.aio.models.generate_content(
        model=model,
        contents=parts,
        config=genai.types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
        ),
    )
    return response.text.strip()


# ---------------------------------------------------------------------------
# Tool use loop (used by fallback agent)
# ---------------------------------------------------------------------------

async def tool_use_loop(
    system: str,
    user_content: list[dict],
    tools: list[dict],
    execute_tool,
    max_iterations: int = 20,
    deadline: float | None = None,
) -> dict:
    """Run a tool-use agent loop. Returns when the model stops calling tools or deadline is reached."""
    provider, model = _get_config()
    logger.info("LLM tool loop: provider=%s, model=%s", provider, model)

    if provider == "anthropic":
        return await _anthropic_tool_loop(system, user_content, tools, execute_tool, model, max_iterations, deadline)
    elif provider == "vertex":
        return await _vertex_tool_loop(system, user_content, tools, execute_tool, model, max_iterations, deadline)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}. Use 'anthropic' or 'vertex'.")


async def _anthropic_tool_loop(
    system: str,
    user_content: list[dict],
    tools: list[dict],
    execute_tool,
    model: str,
    max_iterations: int,
    deadline: float | None = None,
) -> dict:
    import anthropic
    import time

    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    messages = [{"role": "user", "content": user_content}]

    for iteration in range(max_iterations):
        if deadline and time.time() > deadline:
            logger.warning("[Tool loop] Deadline reached, stopping early after %d iterations", iteration)
            return {"status": "completed", "iterations": iteration, "warning": "deadline"}

        logger.info("[Tool loop iteration %d] Calling %s...", iteration + 1, model)

        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            tools=tools,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            logger.info("[Tool loop] Done after %d iterations", iteration + 1)
            for block in response.content:
                if block.type == "text":
                    logger.info("[Tool loop] Final text: %s", block.text[:200])
            return {"status": "completed", "iterations": iteration + 1}

        tool_results = []
        for block in response.content:
            if block.type == "text" and block.text.strip():
                logger.info("[Tool loop] Thinking: %s", block.text.strip())
            if block.type == "tool_use":
                logger.info("[Tool loop] Tool: %s(%s)", block.name, json.dumps(block.input)[:200])
                result = await execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    logger.warning("[Tool loop] Hit max iterations (%d)!", max_iterations)
    return {"status": "completed", "iterations": max_iterations, "warning": "max_iterations"}


async def _vertex_tool_loop(
    system: str,
    user_content: list[dict],
    tools: list[dict],
    execute_tool,
    model: str,
    max_iterations: int,
    deadline: float | None = None,
) -> dict:
    import time
    from google import genai

    project = os.environ.get("GCP_PROJECT_ID")
    default_location = os.environ.get("GCP_LOCATION", "europe-north1")
    # Gemini 3.x models require "global" location
    location = "global" if model.startswith("gemini-3") else default_location
    client = genai.Client(vertexai=True, project=project, location=location)

    gemini_tools = _to_gemini_tools(tools)
    parts = _to_gemini_parts(user_content)

    contents = [genai.types.Content(role="user", parts=parts)]

    for iteration in range(max_iterations):
        if deadline and time.time() > deadline:
            logger.warning("[Tool loop] Deadline reached, stopping early after %d iterations", iteration)
            return {"status": "completed", "iterations": iteration, "warning": "deadline"}

        logger.info("[Tool loop iteration %d] Calling %s...", iteration + 1, model)

        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=genai.types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=4096,
                tools=gemini_tools,
            ),
        )

        # Check for function calls (guard against empty/blocked responses)
        parts = response.candidates[0].content.parts if response.candidates and response.candidates[0].content and response.candidates[0].content.parts else []
        function_calls = [part for part in parts if part.function_call]

        if not function_calls:
            logger.info("[Tool loop] Done after %d iterations", iteration + 1)
            text = response.text if response.text else ""
            if text:
                logger.info("[Tool loop] Final text: %s", text[:200])
            return {"status": "completed", "iterations": iteration + 1}

        # Log any thinking text before tool calls
        all_parts = response.candidates[0].content.parts or []
        for part in all_parts:
            if hasattr(part, 'text') and part.text and part.text.strip() and not hasattr(part, 'function_call'):
                logger.info("[Tool loop] Thinking: %s", part.text.strip())

        # Add assistant response to history
        contents.append(response.candidates[0].content)

        # Execute function calls and build responses
        function_responses = []
        for part in function_calls:
            fc = part.function_call
            logger.info("[Tool loop] Tool: %s(%s)", fc.name, json.dumps(dict(fc.args))[:200])
            result = await execute_tool(fc.name, dict(fc.args))
            function_responses.append(
                genai.types.Part.from_function_response(
                    name=fc.name,
                    response=result,
                )
            )

        contents.append(genai.types.Content(role="user", parts=function_responses))

    logger.warning("[Tool loop] Hit max iterations (%d)!", max_iterations)
    return {"status": "completed", "iterations": max_iterations, "warning": "max_iterations"}


# ---------------------------------------------------------------------------
# Content format converters
# ---------------------------------------------------------------------------

def _to_gemini_parts(content_blocks: list[dict]) -> list:
    """Convert Anthropic-style content blocks to Gemini parts."""
    from google import genai
    import base64

    parts = []
    for block in content_blocks:
        if block["type"] == "text":
            parts.append(genai.types.Part.from_text(text=block["text"]))
        elif block["type"] == "image":
            parts.append(genai.types.Part.from_bytes(
                data=base64.b64decode(block["source"]["data"]),
                mime_type=block["source"]["media_type"],
            ))
        elif block["type"] == "document":
            parts.append(genai.types.Part.from_bytes(
                data=base64.b64decode(block["source"]["data"]),
                mime_type="application/pdf",
            ))
    return parts


def _to_gemini_tools(anthropic_tools: list[dict]) -> list:
    """Convert Anthropic tool definitions to Gemini function declarations."""
    from google import genai

    declarations = []
    for tool in anthropic_tools:
        declarations.append(genai.types.FunctionDeclaration(
            name=tool["name"],
            description=tool["description"],
            parameters=tool["input_schema"],
        ))
    return [genai.types.Tool(function_declarations=declarations)]
