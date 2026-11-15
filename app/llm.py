
import json
import re

from openai import AsyncOpenAI

from .config import settings


_chat_client = AsyncOpenAI(
    base_url=settings.llm_base_url,
    api_key=settings.llm_api_key or "none",
)

_embed_client = AsyncOpenAI(
    base_url=settings.embed_base_url or settings.llm_base_url,
    api_key=settings.embed_api_key or settings.llm_api_key or "none",
)


async def chat(
    system: str,
    user: str,
    history: list[tuple[str, str]] | None = None,
    temperature: float = 0.2,
    json_mode: bool = False,
    max_tokens: int = 700,
) -> str:
    messages = [{"role": "system", "content": system}]

    for role, content in history or []:
        if role in ("user", "assistant") and content:
            messages.append({
                "role": role,
                "content": content,
            })

    messages.append({
        "role": "user",
        "content": user,
    })

    kwargs = {
        "model": settings.llm_model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
        "temperature": temperature,
    }

    # GPT-OSS на Groq использует эти параметры через extra_body.
    if "gpt-oss" in settings.llm_model.lower():
        kwargs["extra_body"] = {
            "reasoning_effort": "low",
            "include_reasoning": False,
        }

    if json_mode:
        try:
            response = await _chat_client.chat.completions.create(
                **kwargs,
                response_format={"type": "json_object"},
            )
        except Exception:
            response = await _chat_client.chat.completions.create(
                **kwargs,
            )
    else:
        response = await _chat_client.chat.completions.create(
            **kwargs,
        )

    if not response.choices:
        raise RuntimeError("LLM вернула пустой список вариантов ответа")

    message = response.choices[0].message
    answer = (message.content or "").strip()

    if not answer:
        raise RuntimeError(
            "LLM вернула пустой текст ответа. "
            f"Finish reason: {response.choices[0].finish_reason}"
        )

    return answer


def parse_json(text: str) -> dict:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)

        if match:
            try:
                data = json.loads(match.group(0))
                return data if isinstance(data, dict) else {}
            except Exception:
                pass

    return {}


async def embed(texts: list[str]) -> list[list[float]] | None:
    if not settings.embed_model:
        return None

    output: list[list[float]] = []

    for i in range(0, len(texts), 64):
        response = await _embed_client.embeddings.create(
            model=settings.embed_model,
            input=texts[i:i + 64],
        )

        output.extend(item.embedding for item in response.data)

    return output
