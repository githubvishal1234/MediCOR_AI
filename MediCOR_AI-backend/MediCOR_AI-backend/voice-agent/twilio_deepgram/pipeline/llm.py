"""
The conversational model, over any OpenAI-compatible chat endpoint.

Deepgram's managed Voice Agent would have supplied an LLM as part of its
socket. This stack does not use it -- see tts.py for why -- so the model
is ours to call, and the obvious choice is the one this project already
runs: Groq, which `agent.py` has been using on live calls and which the
backend already uses for call summaries.

Nothing here is Groq-specific. `base_url` and `model` are configuration,
so OpenAI, Together, a local vLLM or anything else speaking the same
`/chat/completions` shape works without a code change.

WHAT THIS FILE IS RESPONSIBLE FOR, AND WHAT IT IS NOT

It turns a conversation into either *something to say* or *tools to run*.
It does not decide what a tool does, does not talk to the backend, and
does not know what a patient is. `orchestrator.py` runs the loop;
`tools.py` owns the actions.

TWO THINGS CARRIED OVER FROM THE LIVEKIT STACK BY HARD EXPERIENCE

  * **`parallel_tool_calls` is off.** Small models asked for several
    tools at once produce partially-valid batches, and half a booking is
    worse than none.

  * **Groq's tool-call validator is strict about `required`.** The
    LiveKit agent hit this as "did not match schema: missing
    properties". Schemas here therefore mark only genuinely mandatory
    arguments as required, and every optional one is safe to omit -- the
    same shape `tools.FUNCTION_SCHEMAS` already uses.
"""

import json
import logging
from typing import Any, Optional

import httpx

from .. import settings

logger = logging.getLogger("twilio-deepgram.llm")


class LLMError(Exception):
    """The model could not be reached or returned something unusable."""


class ToolCall:
    """One tool the model wants run."""

    def __init__(self, call_id: str, name: str, arguments: dict[str, Any]):
        self.id = call_id
        self.name = name
        self.arguments = arguments

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ToolCall {self.name}({', '.join(sorted(self.arguments))})>"


class Reply:
    """
    What the model produced for one turn: words, tool calls, or both.

    Both is legal and is handled: some models narrate ("Let me check
    that") alongside a tool call, and speaking that while the tool runs
    is better than silence.
    """

    def __init__(self, text: str = "", tool_calls: Optional[list[ToolCall]] = None):
        self.text = (text or "").strip()
        self.tool_calls = tool_calls or []

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def to_openai_tools(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    `tools.FUNCTION_SCHEMAS` in OpenAI's function-calling shape.

    Converted rather than duplicated so there is one definition of what
    the agent can do. The Deepgram Voice Agent path consumes the same
    list in its own shape; a second copy here is a second thing to keep
    in step, and the one that drifted would be whichever is used less.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema.get("parameters")
                or {"type": "object", "properties": {}},
            },
        }
        for schema in schemas
    ]


class ChatLLM:
    """A conversation with one model, for one call."""

    def __init__(self, system_prompt: str, tool_schemas: list[dict[str, Any]]):
        self._tools = to_openai_tools(tool_schemas)
        # The running conversation. The system prompt is index 0 and is
        # never trimmed; see `_trim`.
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        self._client = httpx.AsyncClient(
            base_url=settings.LLM_BASE_URL,
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY or ''}"},
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- conversation state --------------------------------------------

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._trim()

    def add_assistant(self, reply: Reply) -> None:
        message: dict[str, Any] = {"role": "assistant", "content": reply.text or None}
        if reply.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in reply.tool_calls
            ]
        self.messages.append(message)

    def add_tool_result(self, call: ToolCall, content: str) -> None:
        self.messages.append(
            {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content}
        )

    def _trim(self) -> None:
        """
        Keep the conversation bounded.

        A long call would otherwise grow the prompt until every turn is
        slow and expensive. The system prompt is kept whole -- it carries
        the emergency block -- and the oldest exchanges are dropped.

        Trimming from the front can orphan a `tool` message whose
        `assistant` tool call has gone, which some providers reject
        outright, so orphans are dropped with the turn rather than left
        dangling.
        """
        limit = settings.LLM_MAX_HISTORY_MESSAGES
        if len(self.messages) <= limit:
            return
        head, tail = self.messages[0], self.messages[-(limit - 1):]
        while tail and tail[0].get("role") == "tool":
            tail.pop(0)
        self.messages = [head] + tail

    # -- the model -----------------------------------------------------

    async def respond(self) -> Reply:
        """
        One completion against the current conversation.

        Raises `LLMError` on anything unusable. The orchestrator turns
        that into an apology and an offer to transfer -- the caller never
        hears about a model.
        """
        payload: dict[str, Any] = {
            "model": settings.LLM_MODEL,
            "messages": self.messages,
            "temperature": settings.LLM_TEMPERATURE,
            "max_tokens": settings.LLM_MAX_TOKENS,
            # See the module docstring: half a booking is worse than none.
            "parallel_tool_calls": False,
        }
        if self._tools:
            payload["tools"] = self._tools
            payload["tool_choice"] = "auto"
        if settings.LLM_EXTRA_BODY:
            payload.update(settings.LLM_EXTRA_BODY)

        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.RequestError as e:
            raise LLMError(f"model unreachable: {e}") from e

        if response.status_code >= 400:
            # Deliberately truncated. The body can be long and can echo
            # back the prompt, which on this product is clinical text.
            raise LLMError(f"model returned {response.status_code}: {response.text[:200]}")

        try:
            body = response.json()
            choice = body["choices"][0]["message"]
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"unparseable model response: {e}") from e

        return Reply(
            text=choice.get("content") or "",
            tool_calls=_parse_tool_calls(choice.get("tool_calls")),
        )


def _parse_tool_calls(raw: Any) -> list[ToolCall]:
    """
    Read the model's tool calls, skipping any that are malformed.

    A model emitting broken JSON for its arguments must not end the call.
    Skipping leaves the turn with no tool call, which the orchestrator
    handles as "it said something instead" -- recoverable, and far better
    than an exception mid-sentence.
    """
    calls: list[ToolCall] = []
    for item in raw or []:
        function = (item or {}).get("function") or {}
        name = function.get("name")
        if not name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                logger.warning("Tool %s had unparseable arguments; skipping", name)
                continue
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(ToolCall(item.get("id") or name, name, arguments))
    return calls
