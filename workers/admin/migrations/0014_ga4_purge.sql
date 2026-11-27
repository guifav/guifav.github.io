-- Fila única de remoção no Google (GA4 e BigQuery; épico #197, corte 2).
-- Qualquer remoção de vínculo em `site_identities` (pelo Worker no POST /prioridade, pelo `--forget` ou pelo
-- `--optout`) dispara o trigger e enfileira o `uid` aqui com `ga4_pendente = 1` e `bq_tentativas = 0`.
-- O job diário do heating (e o `--forget`/`--optout` quando há credencial GCP) envia `properties.submitUserDeletion`
-- no GA4 enquanto `ga4_pendente = 1`, persiste em `sessoes` os pares `(user_pseudo_id, ga_session_id)` descobertos
-- para nunca perdê-los entre tentativas e repete o DELETE no BigQuery (D+1, D+3 e enquanto houver tabela adiada por
-- streaming buffer, até 5 tentativas). O item só sai da fila quando GA4 e BigQuery terminaram.
CREATE TABLE IF NOT EXISTS ga4_purge_queue (
  uid TEXT PRIMARY KEY NOT NULL
    CHECK (length(uid) BETWEEN 1 AND 128 AND uid NOT GLOB '*[^0-9A-Za-z]*'),
  ga4_pendente INTEGER NOT NULL DEFAULT 1
    CHECK (ga4_pendente IN (0, 1)),
  sessoes TEXT NOT NULL DEFAULT '[]'
    CHECK (json_valid(sessoes) AND length(sessoes) <= 20000),
  bq_tentativas INTEGER NOT NULL DEFAULT 0
    CHECK (bq_tentativas BETWEEN 0 AND 5),
  proxima_em TEXT NOT NULL
    CHECK (proxima_em GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  criado_em TEXT NOT NULL
    CHECK (criado_em GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')
);

CREATE INDEX IF NOT EXISTS ga4_purge_queue_proxima ON ga4_purge_queue (proxima_em);

CREATE TRIGGER IF NOT EXISTS site_identities_enqueue_purge
AFTER DELETE ON site_identities
BEGIN
  INSERT INTO ga4_purge_queue (uid, ga4_pendente, sessoes, bq_tentativas, proxima_em, criado_em)
  VALUES (
    OLD.uid,
    1,
    '[]',
    0,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
  )
  ON CONFLICT(uid) DO UPDATE SET
    ga4_pendente = 1,
    bq_tentativas = 0,
    proxima_em = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');
END;
