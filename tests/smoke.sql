-- Run from the project root: docker compose exec -T db psql -U alfa -d alfa < tests/smoke.sql
SELECT 'kb.materials' AS object, count(*) AS rows FROM kb.materials
UNION ALL SELECT 'kb.chunks', count(*) FROM kb.chunks
UNION ALL SELECT 'kb.answerable_chunks', count(*) FROM kb.answerable_chunks;

SELECT kind, access, count(*)
FROM kb.materials
GROUP BY kind, access
ORDER BY kind, access;

-- Должно вернуть 0: фрагменты с restricted=true не попадают в RAG view.
SELECT count(*) AS restricted_chunks_exposed
FROM kb.chunks c
WHERE c.restricted
  AND EXISTS (SELECT 1 FROM kb.answerable_chunks a WHERE a.chunk_id = c.id);
