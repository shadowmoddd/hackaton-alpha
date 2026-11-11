#!/usr/bin/env python3
"""Нарезает тексты материалов на куски для поиска: kb.chunks.

Запуск: PGHOST=... PGPORT=... PGUSER=... python3 02_chunks.py alfa
Платные уроки попадают в chunks с restricted=true (бот не должен их пересказывать).
"""
import html
import json
import re
import sys
from html.parser import HTMLParser

import psycopg2
from psycopg2.extras import execute_values

DB = sys.argv[1] if len(sys.argv) > 1 else "alfa"
MAX_CHARS = 900
MIN_CHARS = 300


class Strip(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "ul", "ol", "table", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag in ("td", "th"):
            self.out.append(" | ")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(s):
    if not s:
        return ""
    p = Strip()
    p.feed(s)
    text = "".join(p.out).replace("\xa0", " ")
    text = re.sub(r"[\ufeff\u200b\u200c\u200d\u2060]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def block_text(btype, html_content, params):
    """Достаёт текст из блока контента по его типу."""
    if btype == 0:
        return html_to_text(html_content)
    try:
        p = json.loads(params) if isinstance(params, str) else (params or {})
    except Exception:
        return ""
    if not isinstance(p, dict):
        return ""
    parts = []
    if btype == 2:  # врезка/совет
        parts += [p.get("title"), p.get("description")]
    elif btype == 3:  # цитата
        parts += [p.get("text")]
    elif btype == 4:  # список
        parts += [p.get("title")]
        parts += [f"- {html_to_text(str(i))}" for i in (p.get("items") or [])]
    elif btype == 13:  # аккордеон
        parts += [p.get("title"), html_to_text(p.get("text") or "")]
    return "\n".join(str(x).strip() for x in parts if x and str(x).strip())


def split_text(text):
    """Режем по абзацам на куски ~MAX_CHARS."""
    text = re.sub(r"[\ufeff\u200b\u200c\u200d\u2060\xa0]", " ", text)
    chunks, cur = [], ""
    for para in text.split("\n"):
        while len(para) > MAX_CHARS * 1.5:  # очень длинный абзац - режем по предложениям
            cut = para.rfind(". ", 0, MAX_CHARS)
            cut = cut + 1 if cut > 200 else MAX_CHARS
            head, para = para[:cut].strip(), para[cut:].strip()
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(head)
        if len(cur) + len(para) + 1 > MAX_CHARS and len(cur) >= MIN_CHARS:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    return [c for c in chunks if len(c.strip()) > 20]


def main():
    con = psycopg2.connect(dbname=DB)
    cur = con.cursor()
    cur.execute("DROP TABLE IF EXISTS kb.chunks")
    cur.execute("""
        CREATE TABLE kb.chunks (
          id          bigserial PRIMARY KEY,
          material_id uuid NOT NULL REFERENCES kb.materials(id) ON DELETE CASCADE,
          seq         integer NOT NULL,
          kind        text NOT NULL,
          text        text NOT NULL,
          restricted  boolean NOT NULL DEFAULT false,  -- true = платный контент, нельзя пересказывать
          char_len    integer NOT NULL,
          tsv         tsvector,
          UNIQUE (material_id, seq)
        )""")

    cur.execute("SELECT id, kind, title, description, access, is_trial, parent_id FROM kb.materials")
    mats = {r[0]: r for r in cur.fetchall()}

    # тексты блоков по типу материала
    sources = {
        "article":  ('public."ArticleEmbedItems"',  '"ArticleId"'),
        "lesson":   ('public."LessonEmbedItems"',   '"LessonId"'),
        "document": ('public."DocumentEmbedItems"', '"DocumentId"'),
        "glossary": ('public."GlossaryEmbedItems"', '"GlossaryId"'),
    }
    body = {}  # id -> text
    for kind, (tbl, fk) in sources.items():
        cur.execute(f'SELECT {fk}, "Type", "HtmlContent"::text, "Params"::text FROM {tbl} ORDER BY {fk}, "Order"')
        for mid, bt, hc, pr in cur.fetchall():
            if mid not in mats:
                continue
            t = block_text(bt, hc, pr)
            if t:
                body.setdefault(mid, []).append(t)

    # доп. текст карточек
    cards = {}
    cur.execute('SELECT "Id", "DescriptionAdditional"::text, "Target"::text FROM public."Courses"')
    for cid, da, tg in cur.fetchall():
        cards[cid] = [html_to_text(da), html_to_text(tg)]
    cur.execute('SELECT "CourseId", "Title"::text, "Description"::text FROM public."CourseInfoBindings" ORDER BY "CourseId","Order"')
    for cid, t, d in cur.fetchall():
        cards.setdefault(cid, []).append(f"- {t}" + (f": {d}" if d else ""))
    cur.execute('SELECT "CourseId", "Question"::text, "Answer"::text FROM public."CourseQuestions" WHERE NOT "IsDeleted" ORDER BY "CourseId","Order"')
    for cid, q, a in cur.fetchall():
        cards.setdefault(cid, []).append(f"Вопрос: {q}\nОтвет: {html_to_text(a)}")
    for tbl in ("Webinars", "Podcasts"):
        cur.execute(f'SELECT "Id", "DescriptionExt"::text FROM public."{tbl}"')
        for mid, de in cur.fetchall():
            if de:
                cards.setdefault(mid, []).append(html_to_text(de))

    rows = []
    for mid, (_, kind, title, desc, access, is_trial, parent) in mats.items():
        restricted = (access in ("paid", "subscription", "closed")) and not is_trial
        parts = []
        # карточка материала: заголовок + описание - всегда публичная
        head = title + (("\n" + html_to_text(desc)) if desc else "")
        card_parts = [head] + [x for x in cards.get(mid, []) if x]
        # у платных уроков и закрытого контента тело текста в chunks, но restricted
        text_parts = body.get(mid, [])
        seq = 0
        for c in split_text("\n".join(card_parts)):
            rows.append((mid, seq, kind, c, restricted and kind == 'lesson', len(c)))
            seq += 1
        if kind == "lesson" and desc:
            pass  # описание урока уже в карточке
        for c in split_text("\n".join(text_parts)):
            rows.append((mid, seq, kind, c, restricted, len(c)))
            seq += 1

    execute_values(
        cur,
        "INSERT INTO kb.chunks (material_id, seq, kind, text, restricted, char_len) VALUES %s",
        rows, page_size=2000)
    cur.execute("UPDATE kb.chunks SET tsv = to_tsvector('russian', text)")
    cur.execute("UPDATE kb.materials SET tsv = "
                "setweight(to_tsvector('russian', coalesce(title,'')),'A') || "
                "setweight(to_tsvector('russian', coalesce(description,'')),'B') || "
                "setweight(to_tsvector('russian', coalesce(array_to_string(topics,' ')||' '||array_to_string(tags,' '),'')),'C')")
    con.commit()
    cur.execute("SELECT kind, restricted, count(*), sum(char_len) FROM kb.chunks GROUP BY 1,2 ORDER BY 1,2")
    for r in cur.fetchall():
        print(r)


if __name__ == "__main__":
    main()
