-- Runtime data for both Telegram bot and Mini App. Knowledge base stays in schema kb.
CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.users (
    id          BIGINT PRIMARY KEY, -- Telegram user id
    chat_id     BIGINT NOT NULL,
    username    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.profiles (
    user_id     BIGINT PRIMARY KEY REFERENCES app.users(id) ON DELETE CASCADE,
    stage       TEXT,
    area        TEXT,
    tax_regime  TEXT,
    experience  TEXT,
    goal        TEXT,
    tz          TEXT NOT NULL DEFAULT 'Europe/Moscow',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.messages (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS app_messages_user_created_idx ON app.messages(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app.reminders (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    next_at     TIMESTAMPTZ NOT NULL,
    repeat_rule TEXT,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    tax_key     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS app_reminders_due_idx ON app.reminders(next_at) WHERE active;

CREATE TABLE IF NOT EXISTS app.events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT,
    type        TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS app_events_user_type_idx ON app.events(user_id, type, created_at);
