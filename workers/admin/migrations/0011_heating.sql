-- Tabelas do heating de contatos (épico #197, corte 1).
-- Contexto (decisão do Gui, 2026-11-27): o engajamento de cada curso é separado do engajamento no site.
--   - `site`: conversões, emails da newsletter e dos materiais, blog e site.
--   - `curso:<slug>`: engajamento próprio de cada curso (cadastro, aulas e emails do próprio curso), com slug de 1 a 64
--     caracteres em `[a-z0-9-]` começando por letra ou número.
-- `engagement_events` guarda os sinais de engajamento por contexto em modo append-only (webhook do Resend, conversões,
-- curso e GA4); a régua do job diário lê só daqui, por contexto.
-- `heating` guarda o resultado do cálculo diário por `(context, email)`, substituído inteiro pelo job; o painel
-- /admin/contatos lê daqui.
-- `heating_optout` guarda a oposição permanente ao heating sem email em claro (apenas o HMAC-SHA256 com FORGET_HMAC_KEY
-- e prefixo próprio `heating-optout-v1\n`). A oposição vale para todos os contextos: quem se opõe não é pontuado em
-- lugar nenhum (nem no site nem em curso algum).

CREATE TABLE IF NOT EXISTS engagement_events (
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
    CHECK (occurred_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  recorded_at TEXT NOT NULL
    CHECK (recorded_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')
);

CREATE INDEX IF NOT EXISTS engagement_events_context_email_occurred ON engagement_events (context, email, occurred_at);
CREATE INDEX IF NOT EXISTS engagement_events_email ON engagement_events (email);
CREATE INDEX IF NOT EXISTS engagement_events_signal_ref ON engagement_events (signal, ref);

CREATE TABLE IF NOT EXISTS heating (
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
    CHECK (computed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  twenty_person_id TEXT
    CHECK (length(twenty_person_id) BETWEEN 1 AND 64 AND twenty_person_id NOT GLOB '*[^0-9A-Za-z-]*'),
  PRIMARY KEY (context, email)
);

CREATE INDEX IF NOT EXISTS heating_context_score_email ON heating (context, score DESC, email);
CREATE INDEX IF NOT EXISTS heating_email ON heating (email);

CREATE TABLE IF NOT EXISTS heating_optout (
  email_hmac TEXT PRIMARY KEY NOT NULL
    CHECK (length(email_hmac) = 64 AND email_hmac NOT GLOB '*[^0-9a-f]*'),
  opted_out_at TEXT NOT NULL
    CHECK (opted_out_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')
);
