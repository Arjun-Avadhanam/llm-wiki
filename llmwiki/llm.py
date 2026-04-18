"""LLM API wrapper for OpenRouter (OpenAI-compatible)."""

import json
import time
from dataclasses import dataclass

from openai import OpenAI
from rich.console import Console

from llmwiki.config import load_config

console = Console()

_client = None
_config = None

# Global verbose flag — toggled by CLI --verbose option
verbose = False


@dataclass
class LLMResponse:
    """Structured response from an LLM call."""
    text: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str


def _get_client():
    """Lazy-initialize the OpenAI client pointing at OpenRouter."""
    global _client, _config
    if _client is None:
        _config = load_config()
        _client = OpenAI(
            base_url=_config["api"]["base_url"],
            api_key=_config["api"]["key"],
        )
    return _client, _config


def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    expect_json: bool = False,
) -> LLMResponse:
    """Call the LLM and return a structured response with token usage.

    Args:
        system_prompt: System-level instructions.
        user_prompt: The user message / content to process.
        model: Override the default model from config.
        max_tokens: Override default max_tokens.
        temperature: Override default temperature.
        expect_json: If True, parse response as JSON and raise on invalid JSON.

    Returns:
        LLMResponse with text, token counts, and model used.

    Raises:
        RuntimeError: After 3 retries on transient API errors or invalid JSON.
    """
    client, cfg = _get_client()
    model = model or cfg["api"]["model"]
    max_tokens = max_tokens or cfg["api"]["max_tokens"]
    temperature = temperature if temperature is not None else cfg["api"]["temperature"]

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    if verbose:
        console.print("\n[dim]--- SYSTEM PROMPT ---[/dim]")
        console.print(f"[dim]{system_prompt[:500]}{'...' if len(system_prompt) > 500 else ''}[/dim]")
        console.print("[dim]--- USER PROMPT ---[/dim]")
        console.print(f"[dim]{user_prompt[:500]}{'...' if len(user_prompt) > 500 else ''}[/dim]")

    last_error = None
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            text = response.choices[0].message.content
            usage = response.usage

            result = LLMResponse(
                text=text,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                model=model,
            )

            if verbose:
                console.print("[dim]--- RESPONSE ---[/dim]")
                console.print(f"[dim]{text[:500]}{'...' if len(text) > 500 else ''}[/dim]")
                console.print(f"[dim]Tokens: {result.prompt_tokens} in / {result.completion_tokens} out / {result.total_tokens} total[/dim]\n")

            if expect_json:
                # Strip markdown code fences if the LLM wraps JSON in ```json ... ```
                cleaned = text.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1]  # remove first line
                    cleaned = cleaned.rsplit("```", 1)[0]  # remove closing fence
                json.loads(cleaned)  # validate — raises ValueError if malformed
                result.text = cleaned

            return result

        except json.JSONDecodeError as e:
            last_error = e
            if attempt < 2:
                console.print(f"[yellow]Invalid JSON from LLM, retrying ({attempt + 1}/3)...[/yellow]")
                time.sleep(1)
            continue

        except Exception as e:
            last_error = e
            if attempt < 2:
                wait = 2 ** attempt
                console.print(f"[yellow]Retry {attempt + 1}/3 after {wait}s: {e}[/yellow]")
                time.sleep(wait)

    raise RuntimeError(f"LLM call failed after 3 attempts: {last_error}")
