-- Vínculo entre uid do Google Identity Platform e email para o heating de contatos (épico #197, corte 2).
-- Para que serve: associa o `user_id` (uid) enviado ao GA4 nas sessões logadas ao email do contato, somente
-- para quem aceitou os cookies e confirmou o aviso do login no site (ou entrou pelo curso).
-- Quem grava: Worker `blog-newsletter` (`POST /identidade`), após verificar o ID token do Firebase no tenant
-- `guifav-dev-g6dp4` e confirmar que não há oposição em `heating_optout`.
-- Quem lê: job diário `blog-src/scripts/heating_sync.py` (corte 2), ao cruzar eventos do BigQuery (GA4) pelo `user_id`.
-- Remoção: o `--forget` apaga as linhas deste email em `site_identities`.

CREATE TABLE IF NOT EXISTS site_identities (
  uid TEXT PRIMARY KEY NOT NULL
    CHECK (length(uid) BETWEEN 1 AND 128 AND uid NOT GLOB '*[^0-9A-Za-z]*'),
  email TEXT NOT NULL
    CHECK (length(email) BETWEEN 3 AND 254 AND email = lower(email)),
  linked_at TEXT NOT NULL
    CHECK (linked_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')
);

CREATE INDEX IF NOT EXISTS site_identities_email ON site_identities (email);
