-- O D1 de produção recusa padrão de GLOB/LIKE acima de 50 caracteres ("LIKE or GLOB pattern too complex", medido em
-- 28/11: 50 passa, 51 falha; o SQLite padrão aceita 50000). Os CHECKs de data ISO das migrações 0011 a 0014 usavam um
-- padrão de 92 caracteres, então todo INSERT nestas seis tabelas falhava em produção (épico #197). As seis estavam vazias.
-- Recria cada uma com o mesmo CHECK dividido em comprimento (24) e três GLOB de até 42 caracteres, que aceitam exatamente
-- os mesmos valores (AAAA-MM-DDTHH:MM:SS.mmmZ). Mesmo padrão atômico da 0010: cria a nova, copia, apaga a antiga e
-- renomeia; depois recria índices e o trigger de site_identities (apagar a tabela apaga o trigger).

CREATE TABLE engagement_events_new (
  id TEXT PRIMARY KEY NOT NULL
    CHECK (length(id) BETWEEN 1 AND 128 AND id NOT GLOB '*[^0-9A-Za-z:_.-]*'),
  context TEXT NOT NULL
    CHECK (
      context = 'site'
      OR (
        context GLOB 'curso:[a-z0-9]*'
        AND length(context) BETWEEN 7 AND 70
        AND substr(context, 7) NOT GLOB '*[^a-z0-9-]*'
      )
    ),
  email TEXT NOT NULL
    CHECK (length(email) BETWEEN 3 AND 254 AND email = lower(email)),
  signal TEXT NOT NULL
    CHECK (signal IN (
      'email_entregue', 'email_aberto', 'email_clicado', 'email_spam',
      'newsletter_confirmada', 'material_pedido', 'material_baixado',
      'curso_cadastro', 'curso_aula',
      'site_visita', 'site_engajada', 'site_agende', 'site_contato'
    )),
  source TEXT NOT NULL
    CHECK (source IN ('resend', 'conversoes', 'curso', 'ga4')),
  ref TEXT
    CHECK (length(ref) BETWEEN 1 AND 128 AND ref NOT GLOB '*[^0-9A-Za-z:_.-]*'),
  occurred_at TEXT NOT NULL
    CHECK (
      length(occurred_at) = 24 AND substr(occurred_at, 1, 10) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
      AND substr(occurred_at, 11, 9) GLOB 'T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]' AND substr(occurred_at, 20, 5) GLOB '.[0-9][0-9][0-9]Z'
    ),
  recorded_at TEXT NOT NULL
    CHECK (
      length(recorded_at) = 24 AND substr(recorded_at, 1, 10) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
      AND substr(recorded_at, 11, 9) GLOB 'T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]' AND substr(recorded_at, 20, 5) GLOB '.[0-9][0-9][0-9]Z'
    )
);

INSERT INTO engagement_events_new (id, context, email, signal, source, ref, occurred_at, recorded_at)
SELECT id, context, email, signal, source, ref, occurred_at, recorded_at FROM engagement_events;

DROP TABLE engagement_events;
ALTER TABLE engagement_events_new RENAME TO engagement_events;

CREATE INDEX IF NOT EXISTS engagement_events_context_email_occurred ON engagement_events (context, email, occurred_at);
CREATE INDEX IF NOT EXISTS engagement_events_email ON engagement_events (email);
CREATE INDEX IF NOT EXISTS engagement_events_signal_ref ON engagement_events (signal, ref);

CREATE TABLE heating_new (
  context TEXT NOT NULL
    CHECK (
      context = 'site'
      OR (
        context GLOB 'curso:[a-z0-9]*'
        AND length(context) BETWEEN 7 AND 70
        AND substr(context, 7) NOT GLOB '*[^a-z0-9-]*'
      )
    ),
  email TEXT NOT NULL
    CHECK (length(email) BETWEEN 3 AND 254 AND email = lower(email)),
  score INTEGER NOT NULL
    CHECK (score BETWEEN 0 AND 100),
  band TEXT NOT NULL
    CHECK (band IN ('frio', 'morno', 'quente')),
  signals TEXT NOT NULL
    CHECK (json_valid(signals) AND length(signals) <= 2000),
  computed_at TEXT NOT NULL
    CHECK (
      length(computed_at) = 24 AND substr(computed_at, 1, 10) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
      AND substr(computed_at, 11, 9) GLOB 'T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]' AND substr(computed_at, 20, 5) GLOB '.[0-9][0-9][0-9]Z'
    ),
  twenty_person_id TEXT
    CHECK (length(twenty_person_id) BETWEEN 1 AND 64 AND twenty_person_id NOT GLOB '*[^0-9A-Za-z-]*'),
  PRIMARY KEY (context, email)
);

INSERT INTO heating_new (context, email, score, band, signals, computed_at, twenty_person_id)
SELECT context, email, score, band, signals, computed_at, twenty_person_id FROM heating;

DROP TABLE heating;
ALTER TABLE heating_new RENAME TO heating;

CREATE INDEX IF NOT EXISTS heating_context_score_email ON heating (context, score DESC, email);
CREATE INDEX IF NOT EXISTS heating_email ON heating (email);

CREATE TABLE heating_optout_new (
  email_hmac TEXT PRIMARY KEY NOT NULL
    CHECK (length(email_hmac) = 64 AND email_hmac NOT GLOB '*[^0-9a-f]*'),
  opted_out_at TEXT NOT NULL
    CHECK (
      length(opted_out_at) = 24 AND substr(opted_out_at, 1, 10) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
      AND substr(opted_out_at, 11, 9) GLOB 'T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]' AND substr(opted_out_at, 20, 5) GLOB '.[0-9][0-9][0-9]Z'
    )
);

INSERT INTO heating_optout_new (email_hmac, opted_out_at)
SELECT email_hmac, opted_out_at FROM heating_optout;

DROP TABLE heating_optout;
ALTER TABLE heating_optout_new RENAME TO heating_optout;

CREATE TABLE curso_esquecido_new (
  context TEXT NOT NULL
    CHECK (
      context GLOB 'curso:[a-z0-9]*'
      AND length(context) BETWEEN 7 AND 70
      AND substr(context, 7) NOT GLOB '*[^a-z0-9-]*'
    ),
  email_hmac TEXT NOT NULL
    CHECK (length(email_hmac) = 64 AND email_hmac NOT GLOB '*[^0-9a-f]*'),
  forgotten_at TEXT NOT NULL
    CHECK (
      length(forgotten_at) = 24 AND substr(forgotten_at, 1, 10) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
      AND substr(forgotten_at, 11, 9) GLOB 'T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]' AND substr(forgotten_at, 20, 5) GLOB '.[0-9][0-9][0-9]Z'
    ),
  PRIMARY KEY (context, email_hmac)
);

INSERT INTO curso_esquecido_new (context, email_hmac, forgotten_at)
SELECT context, email_hmac, forgotten_at FROM curso_esquecido;

DROP TABLE curso_esquecido;
ALTER TABLE curso_esquecido_new RENAME TO curso_esquecido;

CREATE TABLE site_identities_new (
  uid TEXT PRIMARY KEY NOT NULL
    CHECK (length(uid) BETWEEN 1 AND 128 AND uid NOT GLOB '*[^0-9A-Za-z]*'),
  email TEXT NOT NULL
    CHECK (length(email) BETWEEN 3 AND 254 AND email = lower(email)),
  linked_at TEXT NOT NULL
    CHECK (
      length(linked_at) = 24 AND substr(linked_at, 1, 10) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
      AND substr(linked_at, 11, 9) GLOB 'T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]' AND substr(linked_at, 20, 5) GLOB '.[0-9][0-9][0-9]Z'
    )
);

INSERT INTO site_identities_new (uid, email, linked_at)
SELECT uid, email, linked_at FROM site_identities;

DROP TABLE site_identities;
ALTER TABLE site_identities_new RENAME TO site_identities;

CREATE INDEX IF NOT EXISTS site_identities_email ON site_identities (email);

CREATE TABLE ga4_purge_queue_new (
  uid TEXT PRIMARY KEY NOT NULL
    CHECK (length(uid) BETWEEN 1 AND 128 AND uid NOT GLOB '*[^0-9A-Za-z]*'),
  ga4_pendente INTEGER NOT NULL DEFAULT 1
    CHECK (ga4_pendente IN (0, 1)),
  sessoes TEXT NOT NULL DEFAULT '[]'
    CHECK (json_valid(sessoes) AND length(sessoes) <= 1000000),
  bq_tentativas INTEGER NOT NULL DEFAULT 0
    CHECK (bq_tentativas BETWEEN 0 AND 5),
  bq_concluido INTEGER NOT NULL DEFAULT 0
    CHECK (bq_concluido IN (0, 1)),
  geracao INTEGER NOT NULL DEFAULT 0
    CHECK (geracao >= 0),
  bq_erro TEXT
    CHECK (bq_erro IS NULL OR bq_erro IN ('falhou', 'sessoes_excedidas')),
  proxima_em TEXT NOT NULL
    CHECK (
      length(proxima_em) = 24 AND substr(proxima_em, 1, 10) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
      AND substr(proxima_em, 11, 9) GLOB 'T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]' AND substr(proxima_em, 20, 5) GLOB '.[0-9][0-9][0-9]Z'
    ),
  criado_em TEXT NOT NULL
    CHECK (
      length(criado_em) = 24 AND substr(criado_em, 1, 10) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
      AND substr(criado_em, 11, 9) GLOB 'T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]' AND substr(criado_em, 20, 5) GLOB '.[0-9][0-9][0-9]Z'
    )
);

INSERT INTO ga4_purge_queue_new (uid, ga4_pendente, sessoes, bq_tentativas, bq_concluido, geracao, bq_erro, proxima_em, criado_em)
SELECT uid, ga4_pendente, sessoes, bq_tentativas, bq_concluido, geracao, bq_erro, proxima_em, criado_em FROM ga4_purge_queue;

DROP TABLE ga4_purge_queue;
ALTER TABLE ga4_purge_queue_new RENAME TO ga4_purge_queue;

CREATE INDEX IF NOT EXISTS ga4_purge_queue_proxima ON ga4_purge_queue (proxima_em);

CREATE TRIGGER IF NOT EXISTS site_identities_enqueue_purge
AFTER DELETE ON site_identities
BEGIN
  INSERT INTO ga4_purge_queue (uid, ga4_pendente, sessoes, bq_tentativas, bq_concluido, proxima_em, criado_em)
  VALUES (
    OLD.uid,
    1,
    '[]',
    0,
    0,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
  )
  ON CONFLICT(uid) DO UPDATE SET
    ga4_pendente = 1,
    bq_tentativas = 0,
    bq_concluido = 0,
    geracao = geracao + 1,
    proxima_em = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');
END;
