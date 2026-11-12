-- Cópia do segmento "IA Aplicada" do Resend para o painel (issue #40). O Resend é a fonte; o cron reescreve tudo.
CREATE TABLE IF NOT EXISTS subscribers (
  id TEXT PRIMARY KEY,                 -- id do contato no Resend
  email TEXT NOT NULL,
  unsubscribed INTEGER NOT NULL,       -- 0 inscrito, 1 descadastrado
  origin TEXT NOT NULL,                -- substack | site | outra
  joined_at TEXT NOT NULL,             -- entrada: Substack, confirmação pelo site ou criação no Resend
  paid_until TEXT NOT NULL DEFAULT '', -- vencimento da assinatura paga ativa no Substack
  site_confirmed_at TEXT NOT NULL DEFAULT '',
  admin_unsubscribed_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  run TEXT NOT NULL,                   -- sincronização que gravou a linha
  read_at TEXT NOT NULL DEFAULT '',    -- quando o cron começou a ler este contato no Resend
  local_changed_at TEXT NOT NULL DEFAULT '' -- quando o painel gravou um descadastro já confirmado no Resend
);
CREATE INDEX IF NOT EXISTS subscribers_email ON subscribers (email);

CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
