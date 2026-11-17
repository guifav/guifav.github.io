-- Disparo da publicação no GitHub (issue #82, fatia 4). A aprovação de um artigo chama o workflow_dispatch do
-- publicar.yml; o resultado da última tentativa fica na própria linha da aprovação, como o aviso por email.
ALTER TABLE draft_approvals ADD COLUMN dispatched_at TEXT NOT NULL DEFAULT '';  -- quando o GitHub aceitou o disparo
ALTER TABLE draft_approvals ADD COLUMN dispatch_error TEXT NOT NULL DEFAULT ''; -- motivo, quando o disparo falhou
