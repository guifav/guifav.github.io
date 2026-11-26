-- Marcador mínimo de remoção a pedido do titular (issue #134).
-- Gravado pelo `conversoes_sync.py --forget --apply` antes de apagar as conversões no Twenty e no D1, e consultado pelo
-- Worker `blog-newsletter` no /confirm e no /material/download para recusar links assinados emitidos antes (ou no instante)
-- da remoção. Não guarda o email em claro: só o HMAC-SHA256 (64 caracteres hexadecimais minúsculos) com FORGET_HMAC_KEY
-- e a data em ISO UTC com milissegundos. Mantido por 8 dias (o link mais longo vale 7 dias, mais 1 dia de folga) e apagado
-- pelo sync e pelo --forget com --apply.
CREATE TABLE IF NOT EXISTS forgotten (
  email_hmac TEXT PRIMARY KEY NOT NULL
    CHECK (length(email_hmac) = 64 AND email_hmac NOT GLOB '*[^0-9a-f]*'),
  forgotten_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS forgotten_forgotten_at ON forgotten (forgotten_at);
