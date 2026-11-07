"""Claude Messages API adapter. The harness retains ownership of tool execution."""

from __future__ import annotations

import copy
import json
import os
from typing import Any

from harness.provider import SECRET_ENV, Completion, ToolCall


class AnthropicProvider:
    name = "claude"

    def __init__(
        self, model: str | None = None, *, client: Any = None, output_schema: dict | None = None
    ) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("Claude requires ANTHROPIC_API_KEY")
        self.model = (model or os.environ.get("HARNESS_MODEL") or "").strip()
        if not self.model:
            raise RuntimeError("Claude requires HARNESS_MODEL with an explicit Claude model ID")
        self._client = client
        self._output_schema = copy.deepcopy(output_schema)
        self.usage: dict[str, int] = {}

    def _request(self, payload: dict) -> dict:
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=600.0, max_retries=2)
        try:
            with self._client.messages.stream(**payload) as stream:
                response = stream.get_final_message()
            if isinstance(response, dict):
                body = response
            else:
                body = response.model_dump(mode="json", exclude_none=True)
        except Exception as exc:
            raise RuntimeError(f"Anthropic request failed: {_redact(str(exc))}") from exc
        usage = body.get("usage") or {}
        for name in (
            "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"
        ):
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                self.usage[name] = self.usage.get(name, 0) + value
        return body

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        system, translated = _messages(messages)
        payload: dict = {
            "model": self.model,
            "max_tokens": 8192,
            "messages": translated,
            "cache_control": {"type": "ephemeral"},
        }
        if system:
            payload["system"] = system
        if self._output_schema is not None:
            payload["output_config"] = {"format": {"type": "json_schema", "schema": self._output_schema}}
        if tools:
            payload["tools"] = [
                {
                    "name": spec["name"],
                    "description": spec.get("description", ""),
                    "input_schema": _strict_schema(spec.get("parameters") or {"type": "object"}),
                    "strict": True,
                }
                for spec in tools
            ]
        return _completion(self._request(payload), {spec["name"] for spec in tools})

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def _strict_schema(schema: dict) -> dict:
    """Close object schemas without changing which parameters are optional."""
    result = copy.deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("properties", {})
                node["additionalProperties"] = False
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(result)
    return result


def _messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system: list[str] = []
    translated: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            system.append(_redact(str(message.get("content") or "")))
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message["tool_call_id"],
                "content": _redact(str(message.get("content") or "")),
            }
            if message.get("is_error"):
                block["is_error"] = True
            if translated and translated[-1]["role"] == "user" and all(
                item.get("type") == "tool_result" for item in translated[-1]["content"]
            ):
                translated[-1]["content"].append(block)
            else:
                translated.append({"role": "user", "content": [block]})
        elif role in {"user", "assistant"}:
            if role == "assistant" and message.get("content_blocks") is not None:
                # Signed thinking blocks must be returned exactly as the API sent them.
                content = _clean_blocks(message["content_blocks"])
            else:
                content = []
                text = _redact(str(message.get("content") or ""))
                if text:
                    content.append({"type": "text", "text": text})
                for call in message.get("tool_calls") or []:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["name"],
                            "input": json.loads(_redact(json.dumps(call.get("arguments") or {}))),
                        }
                    )
            translated.append({"role": role, "content": content or [{"type": "text", "text": " "}]})
        else:
            raise RuntimeError(f"Unsupported Anthropic message role: {role}")
    return "\n\n".join(system), translated


def _completion(body: dict, allowed_tools: set[str]) -> Completion:
    reason = body.get("stop_reason")
    if reason not in {"end_turn", "tool_use"}:
        raise RuntimeError(f"Anthropic response did not complete: {reason}")
    blocks = body.get("content")
    if not isinstance(blocks, list):
        raise RuntimeError("Anthropic response has no valid content blocks")
    text: list[str] = []
    calls: list[ToolCall] = []
    ids: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            raise RuntimeError("Anthropic response has a malformed content block")
        kind = block.get("type")
        if kind == "text":
            if not isinstance(block.get("text"), str):
                raise RuntimeError("Anthropic response has malformed text")
            text.append(block["text"])
        elif kind == "tool_use":
            call_id, name, arguments = block.get("id"), block.get("name"), block.get("input")
            if (
                not isinstance(call_id, str)
                or not call_id
                or call_id in ids
                or not isinstance(name, str)
                or name not in allowed_tools
                or not isinstance(arguments, dict)
            ):
                raise RuntimeError("Anthropic response has a malformed or unavailable tool call")
            ids.add(call_id)
            calls.append(ToolCall(name=name, arguments=arguments, id=call_id))
        elif kind not in {"thinking", "redacted_thinking"}:
            raise RuntimeError(f"Anthropic response has an unsupported block: {kind}")
    if bool(calls) != (reason == "tool_use"):
        raise RuntimeError("Anthropic response has inconsistent tool use and stop reason")
    return Completion(text="\n".join(text), tool_calls=calls, content_blocks=copy.deepcopy(blocks))


def _redact(text: str) -> str:
    for name in SECRET_ENV:
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _clean_blocks(blocks: list[dict]) -> list[dict]:
    clean = []
    for block in blocks:
        serialized = json.dumps(block)
        redacted = _redact(serialized)
        if block.get("type") in {"thinking", "redacted_thinking"} and redacted != serialized:
            raise RuntimeError("Cannot relay signed Anthropic content containing a configured credential")
        clean.append(json.loads(redacted))
    return clean
