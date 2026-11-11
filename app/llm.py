import json
import re

from openai import AsyncOpenAI

from .config import settings

_chat_client = AsyncOpenAI(
    base_url=settings.llm_base_url, api_key=settings.llm_api_key or "none"
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
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user})
    kwargs = dict(
        model=settings.llm_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode:
        try:
            r = await _chat_client.chat.completions.create(
                **kwargs, response_format={"type": "json_object"}
            )
        except Exception:  # не все OpenAI-совместимые API поддерживают json_mode
            r = await _chat_client.chat.completions.create(**kwargs)
    else:
        r = await _chat_client.chat.completions.create(**kwargs)
    return r.choices[0].message.content or ""


def parse_json(text: str) -> dict:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                return data if isinstance(data, dict) else {}
            except Exception:
                pass
    return {}


async def embed(texts: list[str]) -> list[list[float]] | None:
    if not settings.embed_model:
        return None
    out: list[list[float]] = []
    for i in range(0, len(texts), 64):
        r = await _embed_client.embeddings.create(
            model=settings.embed_model, input=texts[i:i + 64]
        )
        out.extend(d.embedding for d in r.data)
    return out
