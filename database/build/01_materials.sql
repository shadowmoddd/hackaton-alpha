-- Единый слой "материалы" поверх исходных таблиц. Исходные public."..." не трогаем.
DROP SCHEMA IF EXISTS kb CASCADE;
CREATE SCHEMA kb;

CREATE TABLE kb.materials (
  id                 uuid PRIMARY KEY,
  kind               text NOT NULL CHECK (kind IN ('article','course','lesson','document','webinar','podcast','glossary','calculator')),
  title              text NOT NULL,
  description        text,
  slug               text,
  url                text,
  url_confirmed      boolean,           -- паттерн ссылки встречается внутри самого контента
  access             text NOT NULL CHECK (access IN ('free','paid','subscription','closed')),
  price_rub          integer,
  price_discount_rub integer,
  category           text,              -- рубрика статьи / тип документа
  duration           text,
  authors            text[],
  topics             text[],
  tags               text[],
  industries         text[],
  parent_id          uuid,              -- для урока: id курса
  lesson_no          integer,
  is_trial           boolean,
  published_at       timestamptz,
  starts_at          timestamptz,       -- для вебинаров
  is_closed          boolean NOT NULL DEFAULT false,
  is_recommendable   boolean NOT NULL DEFAULT true,
  rating_avg         numeric(3,2),
  rating_count       integer NOT NULL DEFAULT 0,
  enrollments        integer NOT NULL DEFAULT 0,
  tsv                tsvector
);

-- тип контента (uuid в ContentSettings/UserRatings)
CREATE TEMP TABLE _et(kind text, type_id uuid);
INSERT INTO _et VALUES
 ('document','da1ad865-8605-4ab1-9f6c-4489a26fbc35'),
 ('podcast', '142c23e2-bf71-4a73-b12e-6b1b5e4d47cc'),
 ('course',  '8b440b1a-00c3-423a-9bae-3376475eb203'),
 ('article', '8ede4179-0faf-407d-a4fc-1d94f9a63089'),
 ('webinar', 'ece4acf3-5dd3-47b9-9f08-99c0d6203cc7');

CREATE TEMP TABLE _pub AS SELECT "Id" id FROM public."Statuses" WHERE "Title"='Опубликовано';

-- цены
CREATE TEMP TABLE _sub AS
  SELECT "Cost" cost, "DiscountCost" disc FROM public."SubscriptionTariffs" WHERE "IsMain" LIMIT 1;

-- статьи
INSERT INTO kb.materials (id,kind,title,description,slug,url,url_confirmed,access,category,published_at,is_closed,is_recommendable)
SELECT a."Id",'article',a."Title"::text,a."Description"::text,a."Url"::text,
       'https://kurs.alfabank.ru/articles/'||a."Url"||'/', true,
       CASE WHEN a."IsClosed" THEN 'closed' ELSE 'free' END,
       s."Title"::text, a."Published", a."IsClosed", NOT a."IsClosed"
FROM public."Articles" a
LEFT JOIN public."ArticleSubjects" s ON s."Id"=a."SubjectId"
WHERE NOT a."IsDeleted" AND a."StatusId" IN (SELECT id FROM _pub);

-- курсы
INSERT INTO kb.materials (id,kind,title,description,slug,url,url_confirmed,access,price_rub,price_discount_rub,duration,published_at,is_closed,is_recommendable)
SELECT c."Id",'course',c."Title"::text,c."Description"::text,c."Url"::text,
       'https://kurs.alfabank.ru/courses/'||c."Url"||'/', true,
       CASE t."Title" WHEN 'Платный' THEN 'paid' WHEN 'По подписке' THEN 'subscription'
                      WHEN 'Закрытый' THEN 'closed' ELSE 'free' END,
       CASE t."Title"
         WHEN 'Платный' THEN (SELECT b."Cost" FROM public."CourseTariffBindings" b WHERE b."CourseId"=c."Id" ORDER BY b."IsMain" DESC LIMIT 1)
         WHEN 'По подписке' THEN (SELECT cost FROM _sub) END,
       CASE t."Title" WHEN 'По подписке' THEN (SELECT disc FROM _sub) END,
       c."Duration", c."Published", c."IsClosed", NOT c."IsClosed"
FROM public."Courses" c
JOIN public."CourseTypes" t ON t."Id"=c."TypeId"
WHERE NOT c."IsDeleted" AND c."StatusId" IN (SELECT id FROM _pub);

-- уроки (только реальные уроки, без групп-разделов)
INSERT INTO kb.materials (id,kind,title,description,slug,url,url_confirmed,access,duration,parent_id,lesson_no,is_trial,is_closed,is_recommendable)
SELECT x.lid,'lesson',x.title,x.descr,NULL,k.url,true,
       CASE WHEN x.is_trial THEN 'free' ELSE k.access END,
       x.dur,x.cid,x.no,x.is_trial,false,false
FROM (
  SELECT DISTINCT ON (l."Id") l."Id" lid, COALESCE(NULLIF(b."Title",''),l."Title"::text) title,
         l."Description"::text descr, l."Duration" dur, b."CourseId" cid, b."IsTrial" is_trial,
         row_number() OVER (PARTITION BY b."CourseId" ORDER BY b."Order") no
  FROM public."CourseLessonBindings" b
  JOIN public."Lessons" l ON l."Id"=b."LessonId"
  WHERE NOT b."IsDeleted" AND NOT l."IsGroup" AND NOT l."IsDeleted"
  ORDER BY l."Id", b."Order"
) x
JOIN kb.materials k ON k.id=x.cid AND k.kind='course';

-- документы
INSERT INTO kb.materials (id,kind,title,description,slug,url,url_confirmed,access,category,published_at,is_recommendable)
SELECT d."Id",'document',d."Title"::text,d."Description"::text,d."Url"::text,
       'https://kurs.alfabank.ru/documents/'||d."Url"||'/', true,'free',
       dt."Title"::text, d."Published", true
FROM public."Documents" d LEFT JOIN public."DocumentTypes" dt ON dt."Id"=d."TypeId"
WHERE NOT d."IsDeleted" AND d."StatusId" IN (SELECT id FROM _pub);

-- вебинары (паттерн /webinars/ в контенте не встречается -> url_confirmed=false)
INSERT INTO kb.materials (id,kind,title,description,slug,url,url_confirmed,access,duration,starts_at,is_closed,is_recommendable)
SELECT w."Id",'webinar',w."Title"::text,w."Description"::text,w."Url"::text,
       'https://kurs.alfabank.ru/webinars/'||w."Url"||'/', false,
       CASE WHEN w."IsClosed" THEN 'closed' ELSE 'free' END,
       w."Duration", w."StartsAt", w."IsClosed", NOT w."IsClosed"
FROM public."Webinars" w
WHERE NOT w."IsDeleted" AND w."StatusId" IN (SELECT id FROM _pub);

-- подкасты
INSERT INTO kb.materials (id,kind,title,description,slug,url,url_confirmed,access,price_rub,price_discount_rub,duration,published_at,is_closed,is_recommendable)
SELECT p."Id",'podcast',p."Title"::text,p."Description"::text,p."Url"::text,
       'https://kurs.alfabank.ru/podcasts/'||p."Url"||'/', true,
       CASE WHEN p."IsClosed" THEN 'closed' WHEN t."Title"='По подписке' THEN 'subscription' ELSE 'free' END,
       CASE WHEN t."Title"='По подписке' THEN (SELECT cost FROM _sub) END,
       CASE WHEN t."Title"='По подписке' THEN (SELECT disc FROM _sub) END,
       p."Duration", p."Published", p."IsClosed", NOT p."IsClosed"
FROM public."Podcasts" p LEFT JOIN public."CourseTypes" t ON t."Id"=p."TypeId"
WHERE NOT p."IsDeleted" AND p."StatusId" IN (SELECT id FROM _pub);

-- глоссарий
INSERT INTO kb.materials (id,kind,title,description,slug,url,url_confirmed,access,category,published_at,is_recommendable)
SELECT g."Id",'glossary',g."Title"::text,g."Description"::text,g."Url"::text,
       'https://kurs.alfabank.ru/vocabulary/'||g."Url"||'/', true,'free',
       g."Letter"::text, g."Published", false
FROM public."Glossary" g
WHERE NOT g."IsDeleted" AND g."StatusId" IN (SELECT id FROM _pub);

-- калькуляторы
INSERT INTO kb.materials (id,kind,title,description,slug,url,url_confirmed,access,is_recommendable)
SELECT c."Id",'calculator',c."Title"::text,c."Description",c."Url"::text,
       'https://kurs.alfabank.ru/calculators/'||c."Url"||'/', true,'free', true
FROM public."Calculators" c WHERE NOT c."IsDeleted";

-- темы / теги / отрасли
UPDATE kb.materials m SET topics = s.arr FROM (
  SELECT cs."EntityId" id, array_agg(t."Title"::text ORDER BY ct."Order") arr
  FROM public."ContentSettings" cs
  JOIN public."ContentSettingsTopics" ct ON ct."ContentSettingsId"=cs."Id"
  JOIN public."Topics" t ON t."Id"=ct."TopicId"
  GROUP BY 1) s WHERE s.id=m.id;
UPDATE kb.materials m SET tags = s.arr FROM (
  SELECT cs."EntityId" id, array_agg(t."Title"::text ORDER BY ct."Order") arr
  FROM public."ContentSettings" cs
  JOIN public."ContentSettingsTags" ct ON ct."ContentSettingsId"=cs."Id"
  JOIN public."Tags" t ON t."Id"=ct."TagId"
  GROUP BY 1) s WHERE s.id=m.id;
UPDATE kb.materials m SET industries = s.arr FROM (
  SELECT cs."EntityId" id, array_agg(t."Title"::text ORDER BY ct."Order") arr
  FROM public."ContentSettings" cs
  JOIN public."ContentSettingsIndustries" ct ON ct."ContentSettingsId"=cs."Id"
  JOIN public."Industries" t ON t."Id"=ct."IndustryId"
  GROUP BY 1) s WHERE s.id=m.id;
-- уроки наследуют темы курса
UPDATE kb.materials l SET topics=c.topics, tags=c.tags, industries=c.industries
FROM kb.materials c WHERE l.kind='lesson' AND c.id=l.parent_id;

-- авторы
UPDATE kb.materials m SET authors = s.arr FROM (
  SELECT id, array_agg(a."Title"::text) arr FROM (
    SELECT "ArticleId" id,"AuthorId" aid FROM public."ArticleAuthorBindings"
    UNION SELECT "CourseId","AuthorId" FROM public."CourseAuthorBindings"
    UNION SELECT "PodcastId","AuthorId" FROM public."PodcastAuthorBindings"
    UNION SELECT "WebinarId","AuthorId" FROM public."WebinarAuthorBindings"
  ) u JOIN public."Authors" a ON a."Id"=u.aid GROUP BY id) s WHERE s.id=m.id;

-- оценки и записи на курсы
UPDATE kb.materials m SET rating_avg=s.av, rating_count=s.n FROM (
  SELECT "EntityId" id, round(avg("Value")::numeric,2) av, count(*) n
  FROM public."UserRatings" WHERE NOT "IsDeleted" GROUP BY 1) s WHERE s.id=m.id;
UPDATE kb.materials m SET enrollments=s.n FROM (
  SELECT "CourseId" id, count(*) n FROM public."CourseUserBindings" WHERE NOT "IsDeleted" GROUP BY 1) s WHERE s.id=m.id;

-- связи "похожие материалы" (Id -> ItemId)
CREATE TABLE kb.related (
  from_id uuid NOT NULL, to_id uuid NOT NULL, ord integer,
  PRIMARY KEY (from_id,to_id)
);
INSERT INTO kb.related
SELECT r."Id", r."ItemId", min(r."Order") FROM public."RecommendedItems" r
WHERE r."Id" IN (SELECT id FROM kb.materials) AND r."ItemId" IN (SELECT id FROM kb.materials)
GROUP BY 1,2;
