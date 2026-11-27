-- Fila de repetição da remoção no BigQuery (épico #197, corte 2).
-- O export do GA4 para o BigQuery atrasa (até 72 h, além da tabela intraday em streaming buffer que pode
-- recusar DML): por isso o DELETE precisa ser repetido em D+1 (attempt = 2) e em D+3 (attempt = 3) pelo job diário
-- do heating (e em attempt = 1 quando a tentativa inicial foi pulada ou falhou).
CREATE TABLE IF NOT EXISTS ga4_purge_queue (
  uid TEXT NOT NULL
    CHECK (length(uid) BETWEEN 1 AND 128 AND uid NOT GLOB '*[^0-9A-Za-z]*'),
  due_at TEXT NOT NULL
    CHECK (due_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  attempt INTEGER NOT NULL
    CHECK (attempt BETWEEN 1 AND 3),
  PRIMARY KEY (uid, attempt)
);

CREATE INDEX IF NOT EXISTS ga4_purge_queue_due ON ga4_purge_queue (due_at);
