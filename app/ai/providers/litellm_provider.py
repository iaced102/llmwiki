"""
LiteLLM provider — LLM and embedding via the LiteLLM unified interface.

LiteLLM proxies 100+ models (OpenAI, Anthropic, Google, Azure, Ollama,
Mistral, Groq, Cohere, Bedrock, etc.) through a single API.

Usage in admin settings:
  - llm_provider:   litellm
  - llm_model_id:   <litellm model string, e.g. "gpt-4o", "claude-3-5-sonnet-20241022",
                     "gemini/gemini-2.0-flash", "ollama/llama3", "azure/gpt-4o", ...]
  - llm_api_key:    API key for the upstream provider (or blank for local models)
  - llm_base_url:   (optional) LiteLLM proxy URL if using a self-hosted proxy

For embedding:
  - embedding_provider: litellm
  - embedding_model_id: <litellm embedding model, e.g. "text-embedding-3-small",
                         "cohere/embed-english-v3.0", "ollama/nomic-embed-text", ...>
"""

import asyncio
import base64
import json
from typing import Optional

from loguru import logger

from app.ai.agent_protocol import AssistantTurn, ToolCall, neutral_to_openai_messages
from app.ai.providers.base import EmbeddingProvider, LLMProvider, ProviderConfig, VisionProvider


class LiteLLMLLM(LLMProvider):
    """LLM provider backed by LiteLLM."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)

    def _common_kwargs(self) -> dict:
        kwargs: dict = {}
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url
        return kwargs

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
    ) -> str:
        import litellm

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs = {
            "model": self.config.model_id,
            "messages": messages,
            "temperature": temperature,
            "drop_params": True,
            **self._common_kwargs(),
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = await litellm.acompletion(**kwargs)
        return response.choices[0].message.content or ""

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
    ) -> AssistantTurn:
        import litellm

        openai_messages = []
        if system:
            openai_messages.append({"role": "system", "content": system})
        openai_messages.extend(neutral_to_openai_messages(messages))

        kwargs: dict = {
            "model": self.config.model_id,
            "messages": openai_messages,
            "tools": tools,
            "temperature": temperature,
            "drop_params": True,
            **self._common_kwargs(),
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = await litellm.acompletion(**kwargs)

        choice = response.choices[0]
        message = choice.message
        text = message.content
        tool_calls: list[ToolCall] = []

        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                args: dict = {}
                if tc.function.arguments:
                    try:
                        args = json.loads(tc.function.arguments)
                    except Exception:
                        pass
                tool_calls.append(
                    ToolCall(id=tc.id, name=tc.function.name, arguments=args)
                )

        reason_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        finish_reason = reason_map.get(choice.finish_reason or "stop", "end_turn")

        return AssistantTurn(
            text=text or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

    async def test_connection(self) -> tuple[bool, str]:
        try:
            result = await self.generate("Say 'OK'", max_tokens=10, temperature=0)
            return True, f"OK — model={self.config.model_id}, response='{result[:50]}'"
        except Exception as e:
            return False, f"LiteLLM error: {e}"


class LiteLLMEmbedding(EmbeddingProvider):
    """Embedding provider backed by LiteLLM."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)

    def _common_kwargs(self) -> dict:
        kwargs: dict = {}
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url
        return kwargs

    async def embed(self, text: str) -> list[float]:
        import litellm

        response = await litellm.aembedding(
            model=self.config.model_id,
            input=[text],
            **self._common_kwargs(),
        )
        return response.data[0]["embedding"]

    async def embed_batch(
        self, texts: list[str], concurrency: int = 5
    ) -> list[list[float]]:
        import litellm

        batch_size = 100
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = await litellm.aembedding(
                model=self.config.model_id,
                input=batch,
                **self._common_kwargs(),
            )
            sorted_data = sorted(response.data, key=lambda x: x.get("index", 0) if isinstance(x, dict) else x.index)
            all_embeddings.extend(
                [d["embedding"] if isinstance(d, dict) else d.embedding for d in sorted_data]
            )

        logger.debug(f"LiteLLM: embedded {len(texts)} texts in batches of {batch_size}")
        return all_embeddings

    async def test_connection(self) -> tuple[bool, str]:
        try:
            result = await self.embed("test connection")
            dim = len(result)
            return True, f"OK — model={self.config.model_id}, dimensions={dim}"
        except Exception as e:
            return False, f"LiteLLM embedding error: {e}"


class LiteLLMVision(VisionProvider):
    """Vision provider backed by LiteLLM (any multimodal model)."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)

    def _common_kwargs(self) -> dict:
        kwargs: dict = {}
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url
        return kwargs

    async def analyze_image(
        self,
        image_data: bytes,
        mime_type: str = "image/jpeg",
        prompt: str | None = None,
    ) -> str:
        import litellm

        if not prompt:
            prompt = (
                "Describe this image in detail. "
                "If it's a diagram, flowchart, or table, explain the meaning and steps. "
                "If it's a regular image, provide a concise description."
            )

        b64_image = base64.b64encode(image_data).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64_image}"

        for attempt in range(3):
            try:
                response = await litellm.acompletion(
                    model=self.config.model_id,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": data_url, "detail": "low"},
                                },
                            ],
                        }
                    ],
                    temperature=0.2,
                    drop_params=True,
                    **self._common_kwargs(),
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                logger.warning(f"LiteLLM Vision attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(2)
        return ""

    async def test_connection(self) -> tuple[bool, str]:
        try:
            tiny_png = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
                b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
                b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            await self.analyze_image(tiny_png, "image/png", "What is this?")
            return True, f"OK — model={self.config.model_id}"
        except Exception as e:
            return False, f"LiteLLM Vision error: {e}"
            return True, f"OK — model={self.config.model_id}, dimensions={dim}"
        except Exception as e:
            return False, f"LiteLLM embedding error: {e}"
