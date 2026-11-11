-- Возврат пользователей и полезные действия за последние 30 дней.
SELECT date_trunc('day', created_at) AS day,
       count(DISTINCT user_id) FILTER (WHERE type IN ('miniapp_message', 'answer', 'recommend')) AS active_users,
       count(*) FILTER (WHERE type = 'miniapp_message') AS miniapp_messages,
       count(*) FILTER (WHERE type = 'material_clicked') AS material_clicks,
       count(*) FILTER (WHERE type = 'reminder_sent') AS reminders_sent
FROM app.events
WHERE created_at >= now() - interval '30 days'
GROUP BY 1 ORDER BY 1;
