"""Chat-model backends.

* ``AzureOpenAIChat``: model deployments in an Azure AI Foundry resource, keyless
  via Microsoft Entra ID (``DefaultAzureCredential``), or with an API key.
* ``OpenAICompatibleChat``: any OpenAI-compatible endpoint (NIM, vLLM, Ollama).
* ``ScriptedChat``: replays responses, for unit tests.

The offline demo policy lives in ``demo_policy.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class ChatModel:
    model = "unknown"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        raise NotImplementedError


def _parse(resp) -> ChatResult:
    msg = resp.choices[0].message
    calls = [
        ToolCall(c.id, c.function.name, json.loads(c.function.arguments or "{}"))
        for c in (msg.tool_calls or [])
    ]
    usage = getattr(resp, "usage", None)
    return ChatResult(
        msg.content,
        calls,
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
    )


class AzureOpenAIChat(ChatModel):
    def __init__(
        self,
        endpoint: str,
        deployment: str,
        api_version: str = "2024-10-21",
        api_key: str | None = None,
    ):
        from openai import AzureOpenAI

        if api_key:
            self.client = AzureOpenAI(
                azure_endpoint=endpoint, api_key=api_key, api_version=api_version
            )
        else:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            token = get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            )
            self.client = AzureOpenAI(
                azure_endpoint=endpoint, azure_ad_token_provider=token, api_version=api_version
            )
        self.model = deployment

    def chat(self, messages, tools=None) -> ChatResult:
        return _parse(
            self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tools or None, temperature=0
            )
        )


class OpenAICompatibleChat(ChatModel):
    def __init__(self, base_url: str, model: str, api_key: str | None = None):
        from openai import OpenAI

        self.client = OpenAI(
            base_url=base_url, api_key=api_key or "not-needed", max_retries=6, timeout=120
        )
        self.model = model

    def chat(self, messages, tools=None) -> ChatResult:
        return _parse(
            self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tools or None, temperature=0
            )
        )


class ScriptedChat(ChatModel):
    model = "scripted"

    def __init__(self, responses: list[ChatResult]):
        self.responses = list(responses)
        self.calls: list[tuple[list[dict], list[dict] | None]] = []

    def chat(self, messages, tools=None) -> ChatResult:
        self.calls.append(([dict(m) for m in messages], tools))
        return self.responses.pop(0)
