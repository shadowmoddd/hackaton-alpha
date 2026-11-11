"""Калибровка порога «есть ответ в базе / нет ответа».

Файл вопросов (JSONL), по строке на вопрос:
    {"q": "Как выбрать систему налогообложения?", "answerable": true}
    {"q": "Как приготовить борщ?", "answerable": false}

Запуск:
    docker compose run --rm -v ./tests:/srv/tests bot python -m app.evaluate tests/questions.jsonl
Скрипт печатает лучший score поиска по каждому вопросу и точность решения
«отвечать / не отвечать» при разных порогах. Выберите порог и пропишите KB_MIN_SCORE в .env.
"""
import asyncio
import json
import sys

from . import db, search
from .config import settings


async def main(path: str) -> None:
    await db.init_pool()
    with open(path, encoding="utf-8") as f:
        items = [json.loads(line) for line in f if line.strip()]

    results = []
    for it in items:
        hits = await search.kb(it["q"])
        best = hits[0]["score"] if hits else 0.0
        results.append((it, best, hits[0]["title"] if hits else "-"))
        print(f"{best:5.2f}  answerable={it.get('answerable')!s:5}  {it['q'][:60]}  ->  {results[-1][2]}")

    labeled = [(it, best) for it, best, _ in results if "answerable" in it]
    if not labeled:
        return
    print(f"\nТекущий KB_MIN_SCORE = {settings.kb_min_score}")
    print("Порог  Точность  (верно отвечаем / верно отказываемся)")
    for thr in [x / 20 for x in range(1, 15)]:
        ok = sum(1 for it, best in labeled if (best >= thr) == bool(it["answerable"]))
        print(f"{thr:5.2f}  {ok / len(labeled):7.0%}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
