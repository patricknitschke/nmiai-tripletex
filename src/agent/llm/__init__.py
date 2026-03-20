"""LLM abstraction layer — supports Anthropic (Claude) and Vertex AI (Gemini)."""

from .client import complete, tool_use_loop

__all__ = ["complete", "tool_use_loop"]
