"""
Cognitive Analysis — shared LLM helper for cognitive modules.

Provides analyze_with_brain(): a thin wrapper around brain.ask() that
extracts structured JSON from the LLM reply. Used by executive_action,
monologue_watcher, and sovereign_curiosity to add semantic understanding
without pulling in external dependencies.

Always returns None on failure — callers fall back to rule-based logic.
"""

import json
import re


def analyze_with_brain(prompt: str, schema_description: str, timeout: int = 60) -> dict | None:
    """Call the brain module with a prompt and extract structured JSON from the reply.

    Args:
        prompt: The analysis prompt to send.
        schema_description: JSON schema description for the expected response.
        timeout: Timeout in seconds (unused currently, brain handles its own).

    Returns:
        Parsed JSON dict on success, None on any failure (LLM offline, parse
        error, empty reply, brain module unavailable).
    """
    if not prompt or not schema_description:
        return None

    # Build the full prompt with JSON-only instruction
    full_prompt = (
        f"{prompt}\n\n"
        f"Reply with JSON only. No preamble, no markdown fences, no explanation.\n"
        f"The JSON must conform to this schema:\n"
        f"{schema_description}"
    )

    # Try brain module
    try:
        from brain import ask
    except ImportError:
        return None

    try:
        result = ask(full_prompt, history=None)
    except Exception:
        return None

    if not isinstance(result, dict):
        return None

    reply = result.get("reply", "")
    if not reply or not isinstance(reply, str):
        return None

    # Extract JSON object from reply — handles markdown fences, preamble, etc.
    return _extract_json(reply)


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from text that may contain markdown fences or preamble.

    Tries multiple strategies in order:
    1. Direct parse (text is pure JSON).
    2. Extract from ```json fences.
    3. Regex find first { ... } pair.
    """
    text = text.strip()

    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: markdown code fence
    fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: find first balanced JSON object
    try:
        # Find the outermost { ... }
        start = text.find("{")
        if start == -1:
            return None
        # Walk forward tracking brace depth
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    return None
