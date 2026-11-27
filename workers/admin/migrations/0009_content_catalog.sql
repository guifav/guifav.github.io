-- Catálogo de posts do blog e estado dos coletores do painel (issue #193, fase 1 do épico #192).
-- O cron do Worker `blog-admin` copia /blog/catalogo.json para `content_catalog`, trocando tudo numa transação, e
-- registra o resultado em `collectors` (source = 'catalogo_posts'). As telas do painel leem só o D1.
-- As fases 2 e 3 do #192 usam a mesma tabela `collectors` para outros coletores.
CREATE TABLE IF NOT EXISTS content_catalog (
  path TEXT PRIMARY KEY
    CHECK (
      length(path) BETWEEN 8 AND 200
      AND substr(path, 1, 6) = '/blog/'
      AND substr(path, length(path), 1) = '/'
      AND substr(path, 7, 1) != '-'
      AND substr(path, 7, length(path) - 7) NOT GLOB '*[^a-z0-9-]*'
    ),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
  published_on TEXT NOT NULL CHECK (published_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')
);
CREATE INDEX IF NOT EXISTS content_catalog_published ON content_catalog (published_on DESC, path ASC);

CREATE TABLE IF NOT EXISTS collectors (
  source TEXT PRIMARY KEY,
  last_attempt_at TEXT NOT NULL,
  last_success_at TEXT,
  last_error TEXT,
  item_count INTEGER
);
