-- Aprovações de rascunhos do blog (issue #62). Uma linha por clique em "Aprovar publicação"; nunca apagada.
-- html_sha256 é o hash do HTML que o Gui viu; source_sha256 identifica o Markdown e as imagens que o geraram.
CREATE TABLE IF NOT EXISTS draft_approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL,
  title TEXT NOT NULL,
  html_sha256 TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  approved_at TEXT NOT NULL,
  approved_by TEXT NOT NULL,
  notified_at TEXT NOT NULL DEFAULT '',  -- aviso por email ao Roger entregue ao Resend
  notify_error TEXT NOT NULL DEFAULT ''  -- motivo, quando o aviso falhou
);
CREATE INDEX IF NOT EXISTS draft_approvals_slug ON draft_approvals (slug, id);
