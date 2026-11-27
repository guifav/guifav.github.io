-- Tabelas do heating de contatos (épico #197, corte 1).
-- `engagement_events` guarda os sinais de engajamento em modo append-only (webhook do Resend, conversões, curso e GA4);
-- a régua do job diário lê só daqui.
-- `heating` guarda o resultado do cálculo diário, substituído inteiro pelo job; o painel /admin/contatos lê daqui.
-- `heating_optout` guarda a oposição permanente ao heating sem email em claro (apenas o HMAC-SHA256 com FORGET_HMAC_KEY
-- e prefixo próprio `heating-optout-v1\n`).

CREATE TABLE IF NOT EXISTS engagement_events (
  id TEXT PRIMARY KEY NOT NULL
    CHECK (length(id) BETWEEN 1 AND 128 AND id NOT GLOB '*[^0-9A-Za-z:_.-]*'),
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
    CHECK (occurred_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  recorded_at TEXT NOT NULL
    CHECK (recorded_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')
);

CREATE INDEX IF NOT EXISTS engagement_events_email_occurred ON engagement_events (email, occurred_at);
CREATE INDEX IF NOT EXISTS engagement_events_signal_ref ON engagement_events (signal, ref);

CREATE TABLE IF NOT EXISTS heating (
  email TEXT PRIMARY KEY NOT NULL
    CHECK (length(email) BETWEEN 3 AND 254 AND email = lower(email)),
  score INTEGER NOT NULL
    CHECK (score BETWEEN 0 AND 100),
  band TEXT NOT NULL
    CHECK (band IN ('frio', 'morno', 'quente')),
  signals TEXT NOT NULL
    CHECK (json_valid(signals) AND length(signals) <= 2000),
  computed_at TEXT NOT NULL
    CHECK (computed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  twenty_person_id TEXT
    CHECK (length(twenty_person_id) BETWEEN 1 AND 64 AND twenty_person_id NOT GLOB '*[^0-9A-Za-z-]*')
);

CREATE INDEX IF NOT EXISTS heating_score_email ON heating (score DESC, email);

CREATE TABLE IF NOT EXISTS heating_optout (
  email_hmac TEXT PRIMARY KEY NOT NULL
    CHECK (length(email_hmac) = 64 AND email_hmac NOT GLOB '*[^0-9a-f]*'),
  opted_out_at TEXT NOT NULL
    CHECK (opted_out_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')
);
