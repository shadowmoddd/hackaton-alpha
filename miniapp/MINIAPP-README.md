# Skilly Mini App

React + TypeScript + Vite интерфейс для трека 2: чат с ИИ-помощником предпринимателя, каталог материалов Альфа-Курса, профиль бизнеса и напоминания.

## Запуск и сборка

```bash
npm ci
npm run build
npm run dev
```

## Подключение

При production-сборке задайте `VITE_API_BASE_URL=https://ВАШ-API`. Frontend передаёт `Telegram.WebApp.initData` в `X-Telegram-Init-Data`; backend проверяет подпись. Не помещайте Telegram token или LLM API key в `VITE_*` переменные.

API endpoints: `/api/chat`, `/api/history`, `/api/profile`, `/api/materials`, `/api/reminders`. История, профиль и напоминания серверные; чат обрабатывается тем же `dialog.respond()`, что и сообщения Telegram-бота.

Полная инструкция по Docker, БД и деплою находится в корневом `README.md`.
