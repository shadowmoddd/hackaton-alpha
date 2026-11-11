CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- индексы поиска
CREATE INDEX materials_tsv_gin   ON kb.materials USING gin (tsv);
CREATE INDEX materials_title_trg ON kb.materials USING gin (title gin_trgm_ops);
CREATE INDEX materials_kind_idx  ON kb.materials (kind);
CREATE INDEX materials_topics_gin ON kb.materials USING gin (topics);
CREATE INDEX materials_parent_idx ON kb.materials (parent_id);
CREATE INDEX chunks_tsv_gin      ON kb.chunks USING gin (tsv);
CREATE INDEX chunks_material_idx ON kb.chunks (material_id);

-- Что бот имеет право цитировать пользователю: без платного контента
CREATE VIEW kb.answerable_chunks AS
SELECT c.id AS chunk_id, c.material_id, c.seq, c.kind, c.text, c.tsv,
       m.title, m.url, m.access
FROM kb.chunks c JOIN kb.materials m ON m.id = c.material_id
WHERE NOT c.restricted;

-- Что можно рекомендовать (опубликовано, не закрыто, не урок и не термин)
CREATE VIEW kb.recommendable AS
SELECT * FROM kb.materials WHERE is_recommendable;

-- Справочник тем (для онбординга)
CREATE VIEW kb.topic_list AS
SELECT t."Title"::text AS topic, t."Url"::text AS slug, t."Description"::text AS description,
       (SELECT count(*) FROM kb.materials m WHERE t."Title"::text = ANY(m.topics) AND m.is_recommendable) AS materials
FROM public."Topics" t WHERE NOT t."IsDeleted" ORDER BY t."Order";

-- По умолчанию уроки не возвращаются (рекомендуем курс целиком); уроки: kb.search(q, 8, ARRAY['lesson']).
-- Поиск: SELECT * FROM kb.search('как платить налоги на УСН', 8);
-- Возвращает материалы (не куски) по релевантности; платный контент можно рекомендовать, но не раскрывать.
CREATE FUNCTION kb.search(q text, lim integer DEFAULT 8, kinds text[] DEFAULT NULL)
RETURNS TABLE (material_id uuid, kind text, title text, url text, access text,
               price_rub integer, snippet text, score real)
LANGUAGE sql STABLE AS $$
  WITH qq AS (SELECT websearch_to_tsquery('russian', q) AS tq),
  hit AS (
    SELECT c.material_id, max(ts_rank_cd(c.tsv, qq.tq)) AS r_chunk,
           (array_agg(c.id ORDER BY ts_rank_cd(c.tsv, qq.tq) DESC))[1] AS best_chunk
    FROM kb.chunks c, qq
    WHERE c.tsv @@ qq.tq AND NOT c.restricted
    GROUP BY c.material_id
  ),
  card AS (
    SELECT m.id, ts_rank_cd(m.tsv, qq.tq) AS r_card
    FROM kb.materials m, qq WHERE m.tsv @@ qq.tq
  )
  SELECT m.id, m.kind, m.title, m.url, m.access, m.price_rub,
         left(ch.text, 400),
         (coalesce(card.r_card,0)*2 + coalesce(hit.r_chunk,0)
           + CASE WHEN m.kind='lesson' THEN -0.05 ELSE 0 END)::real
  FROM kb.materials m
  LEFT JOIN hit  ON hit.material_id = m.id
  LEFT JOIN card ON card.id = m.id
  LEFT JOIN kb.chunks ch ON ch.id = hit.best_chunk
  WHERE (hit.material_id IS NOT NULL OR card.id IS NOT NULL)
    AND m.is_closed IS NOT TRUE
    AND ((kinds IS NULL AND m.kind <> 'lesson') OR m.kind = ANY(kinds))
  ORDER BY 8 DESC
  LIMIT lim
$$;

-- Поиск кусков текста для ответа (RAG). Только публичный контент.
-- SELECT * FROM kb.search_chunks('когда платить взносы ИП', 5);
CREATE FUNCTION kb.search_chunks(q text, lim integer DEFAULT 6)
RETURNS TABLE (chunk_id bigint, material_id uuid, kind text, title text, url text, text text, score real)
LANGUAGE sql STABLE AS $$
  SELECT a.chunk_id, a.material_id, a.kind, a.title, a.url, a.text,
         ts_rank_cd(a.tsv, websearch_to_tsquery('russian', q))::real
  FROM kb.answerable_chunks a
  WHERE a.tsv @@ websearch_to_tsquery('russian', q)
  ORDER BY 7 DESC LIMIT lim
$$;

-- ===================== Схема бота (пустая, заполняет сам бот) =====================
DROP SCHEMA IF EXISTS bot CASCADE;
CREATE SCHEMA bot;

CREATE TABLE bot.users (
  id           bigserial PRIMARY KEY,
  tg_user_id   bigint UNIQUE,           -- Telegram user id (NULL у тестовых профилей)
  tg_chat_id   bigint,
  username     text,
  timezone     text NOT NULL DEFAULT 'Europe/Moscow',
  is_test      boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz
);

-- Профиль собирается из диалога, все поля необязательные
CREATE TABLE bot.user_profiles (
  user_id        bigint PRIMARY KEY REFERENCES bot.users(id) ON DELETE CASCADE,
  business_stage text CHECK (business_stage IN ('idea','starting','running','scaling')),
  industry       text,
  tax_regime     text CHECK (tax_regime IN ('npd','usn_income','usn_income_expense','osno','psn','ausn','unknown')),
  has_employees  boolean,
  current_goal   text,       -- "хочу найти клиентов", "разобраться с налогами"
  experience     text CHECK (experience IN ('newbie','some','experienced')),
  interests      text[],     -- темы из kb.topic_list
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE bot.messages (
  id         bigserial PRIMARY KEY,
  user_id    bigint NOT NULL REFERENCES bot.users(id) ON DELETE CASCADE,
  role       text NOT NULL CHECK (role IN ('user','assistant','system')),
  content    text NOT NULL,
  material_ids uuid[],       -- какие материалы бот предложил в этом ответе
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX messages_user_idx ON bot.messages (user_id, created_at DESC);

-- Напоминания: разовые и повторяющиеся, можно менять частоту и выключать
CREATE TABLE bot.reminders (
  id            bigserial PRIMARY KEY,
  user_id       bigint NOT NULL REFERENCES bot.users(id) ON DELETE CASCADE,
  kind          text NOT NULL CHECK (kind IN ('study','tax','event','custom')),
  text          text NOT NULL,
  material_id   uuid REFERENCES kb.materials(id),   -- что предложить по клику
  schedule_type text NOT NULL CHECK (schedule_type IN ('once','daily','weekly','monthly','every_n_days')),
  interval_days integer,                  -- для every_n_days
  weekday       smallint CHECK (weekday BETWEEN 1 AND 7),
  day_of_month  smallint CHECK (day_of_month BETWEEN 1 AND 31),
  time_of_day   time NOT NULL DEFAULT '10:00',
  next_fire_at  timestamptz,              -- когда сработает в следующий раз (UTC)
  ends_at       timestamptz,
  is_active     boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_sent_at  timestamptz,
  sent_count    integer NOT NULL DEFAULT 0
);
CREATE INDEX reminders_due_idx ON bot.reminders (next_fire_at) WHERE is_active;

-- Лог отправок: чтобы не спамить и считать возвраты на платформу
CREATE TABLE bot.reminder_log (
  id          bigserial PRIMARY KEY,
  reminder_id bigint NOT NULL REFERENCES bot.reminders(id) ON DELETE CASCADE,
  sent_at     timestamptz NOT NULL DEFAULT now(),
  clicked_at  timestamptz,
  status      text NOT NULL DEFAULT 'sent' CHECK (status IN ('sent','failed','clicked','snoozed','stopped'))
);

-- Тестовые профили для демо (синтетика, не реальные люди)
INSERT INTO bot.users (username, is_test) VALUES ('test_newbie', true), ('test_ip_usn', true), ('test_seller', true);
INSERT INTO bot.user_profiles (user_id, business_stage, industry, tax_regime, has_employees, current_goal, experience, interests)
SELECT id, v.st, v.ind, v.tx, v.emp, v.goal, v.exp, v.interests
FROM bot.users u JOIN (VALUES
  ('test_newbie','idea',NULL,'unknown',false,'не знаю, с чего начать','newbie',ARRAY[]::text[]),
  ('test_ip_usn','running','услуги','usn_income',false,'разобраться с налогами и отчётностью','some',ARRAY['Налоги']),
  ('test_seller','running','розничная торговля','usn_income',true,'продвигать товары на маркетплейсах','some',ARRAY['Маркетплейсы'])
) v(un,st,ind,tx,emp,goal,exp,interests) ON v.un=u.username;

ANALYZE;
