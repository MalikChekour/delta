"""Adaptateurs de fournisseurs, derriere une interface unique.

Le format d'echange interne est celui de l'API OpenAI (role/content/tool_calls) :
c'est le dialecte que parlent la quasi-totalite des fournisseurs, y compris les
serveurs locaux. L'adaptateur Anthropic traduit dans les deux sens.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import Settings
from .providers import ProviderSpec
from .tools import Registry

log = logging.getLogger(__name__)

Message = dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON brut, tel que renvoye par le modele

    def parsed(self) -> dict[str, Any]:
        """Les modeles echappent le JSON de facons variables : toujours parser."""
        if not self.arguments.strip():
            return {}
        try:
            value = json.loads(self.arguments)
        except json.JSONDecodeError as exc:
            raise ValueError(f"arguments JSON invalides pour {self.name} : {exc}") from exc
        return value if isinstance(value, dict) else {"value": value}


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: Le message assistant a rajouter a l'historique, au format interne.
    assistant_message: Message = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None


class LLMClient(Protocol):
    async def complete(
        self, system: str, messages: list[Message], registry: Registry, model: str
    ) -> Reply: ...


def _schemas_openai(registry: Registry) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in registry
    ]


def _schemas_anthropic(registry: Registry) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in registry
    ]


# --------------------------------------------------------------------------- #
# Fournisseurs compatibles OpenAI                                              #
# --------------------------------------------------------------------------- #


class OpenAICompatClient:
    """Couvre OpenRouter, DeepSeek, Venice, Groq, xAI, Ollama, vLLM, LM Studio..."""

    def __init__(self, settings: Settings, spec: ProviderSpec) -> None:
        from openai import AsyncOpenAI

        self.settings = settings
        self.spec = spec
        self._client = AsyncOpenAI(
            api_key=settings.api_key_for(spec),
            base_url=settings.base_url_for(spec),
            timeout=settings.request_timeout,
            max_retries=3,
            default_headers=dict(spec.extra_headers) or None,
        )

    async def complete(
        self, system: str, messages: list[Message], registry: Registry, model: str
    ) -> Reply:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": self.settings.max_tokens,
        }
        if len(registry):
            payload["tools"] = _schemas_openai(registry)
            payload["tool_choice"] = "auto"
        if self.settings.temperature is not None:
            payload["temperature"] = self.settings.temperature

        resp = await self._client.chat.completions.create(**payload)
        choice = resp.choices[0]
        msg = choice.message

        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "{}")
            for c in (msg.tool_calls or [])
        ]

        assistant: Message = {"role": "assistant", "content": msg.content or ""}
        if calls:
            assistant["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                }
                for c in calls
            ]

        usage = {}
        if resp.usage:
            usage = {
                "input": resp.usage.prompt_tokens or 0,
                "output": resp.usage.completion_tokens or 0,
            }

        return Reply(
            text=msg.content or "",
            tool_calls=calls,
            assistant_message=assistant,
            usage=usage,
            stop_reason=choice.finish_reason,
        )

    @staticmethod
    def tool_result(call: ToolCall, output: str) -> Message:
        return {"role": "tool", "tool_call_id": call.id, "content": output}


# --------------------------------------------------------------------------- #
# Anthropic (SDK officiel, protocole distinct)                                 #
# --------------------------------------------------------------------------- #


class AnthropicClient:
    def __init__(self, settings: Settings, spec: ProviderSpec) -> None:
        from anthropic import AsyncAnthropic

        self.settings = settings
        self.spec = spec
        self._client = AsyncAnthropic(
            api_key=settings.api_key_for(spec),
            timeout=settings.request_timeout,
            max_retries=3,
        )
        self._fallbacks_ok = os.getenv("HERMES_ANTHROPIC_FALLBACKS", "1") != "0"

    # -- traduction interne -> Anthropic ------------------------------------ #
    @staticmethod
    def _to_anthropic(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            if role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                }
                # Les tool_result consecutifs doivent tenir dans un seul message user.
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif role == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for call in m.get("tool_calls", []):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["function"]["name"],
                            "input": json.loads(call["function"]["arguments"] or "{}"),
                        }
                    )
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def _thinking(self, model: str) -> dict[str, str] | None:
        mode = os.getenv("HERMES_THINKING", "adaptive").strip().lower()
        if mode in {"off", "0", "disabled"}:
            return None
        # Haiku et les modeles anterieurs a 4.6 ne connaissent pas le mode adaptatif.
        if "haiku" in model or "-3-" in model:
            return None
        return {"type": "adaptive", "display": "omitted"}

    async def complete(
        self, system: str, messages: list[Message], registry: Registry, model: str
    ) -> Reply:
        payload: dict[str, Any] = {
            "model": model,
            "system": system,
            "messages": self._to_anthropic(messages),
            "max_tokens": self.settings.max_tokens,
        }
        thinking = self._thinking(model)
        if thinking:
            payload["thinking"] = thinking
        elif self.settings.temperature is not None:
            # temperature est rejetee quand la reflexion est active.
            payload["temperature"] = self.settings.temperature
        if len(registry):
            payload["tools"] = _schemas_anthropic(registry)

        message = await self._create(payload, model)

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=json.dumps(block.input))
                )

        text = "\n".join(p for p in text_parts if p)
        if message.stop_reason == "refusal":
            detail = getattr(message, "stop_details", None)
            category = getattr(detail, "category", None) if detail else None
            text = text or f"[Le modele a refuse la requete (categorie : {category}).]"

        assistant: Message = {"role": "assistant", "content": text}
        if calls:
            assistant["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                }
                for c in calls
            ]

        return Reply(
            text=text,
            tool_calls=calls,
            assistant_message=assistant,
            usage={
                "input": message.usage.input_tokens,
                "output": message.usage.output_tokens,
            },
            stop_reason=message.stop_reason,
        )

    async def _create(self, payload: dict[str, Any], model: str):
        """Emet la requete en streaming, avec repli si le beta n'est pas accepte."""
        use_fallbacks = self._fallbacks_ok and model.startswith(
            ("claude-opus-5", "claude-fable-5")
        )
        if use_fallbacks:
            try:
                async with self._client.beta.messages.stream(
                    **payload,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                ) as stream:
                    return await stream.get_final_message()
            except Exception as exc:  # noqa: BLE001 - tout echec du beta doit mener au repli
                log.warning("Repli sans server-side-fallback : %s", exc)
                self._fallbacks_ok = False

        async with self._client.messages.stream(**payload) as stream:
            return await stream.get_final_message()

    @staticmethod
    def tool_result(call: ToolCall, output: str) -> Message:
        return {"role": "tool", "tool_call_id": call.id, "content": output}


def build_client(settings: Settings, spec: ProviderSpec) -> LLMClient:
    if spec.kind == "anthropic":
        return AnthropicClient(settings, spec)
    return OpenAICompatClient(settings, spec)


def tool_result_message(call: ToolCall, output: str) -> Message:
    """Le format interne est commun : un seul constructeur suffit."""
    return {"role": "tool", "tool_call_id": call.id, "content": output}
