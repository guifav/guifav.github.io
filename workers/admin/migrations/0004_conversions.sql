-- Conversões do site (issue #97, fase 1). Quem grava é o Worker `blog-newsletter`, pelo mesmo D1; o painel só é dono
-- do esquema. A fase 2 leva as linhas com synced_at nulo ao Twenty (crm.guifav.dev) e marca synced_at.
CREATE TABLE IF NOT EXISTS conversions (
  id TEXT PRIMARY KEY,               -- SHA-256 da ação com a assinatura do link: clicar de novo no mesmo link não duplica
  email TEXT NOT NULL,               -- email comprovado pelo link assinado (minúsculas)
  action TEXT NOT NULL,              -- material_download | newsletter_confirm
  material TEXT,                     -- id do material; nulo em newsletter_confirm
  interests TEXT NOT NULL,           -- JSON: [{"id": "CUSTO_IA", "rotulo": "Custo de IA"}], como estava no catálogo na hora da ação
  occurred_at TEXT NOT NULL,         -- ISO UTC
  synced_at TEXT                     -- quando a fase 2 gravou no Twenty (ISO UTC); nulo até lá
);
CREATE INDEX IF NOT EXISTS conversions_email ON conversions (email);
CREATE INDEX IF NOT EXISTS conversions_pending ON conversions (synced_at);

-- Pedidos de material por dia, sem email: o pedido sem clique não comprova o endereço, então só a contagem fica.
CREATE TABLE IF NOT EXISTS material_requests (
  material TEXT NOT NULL,
  day TEXT NOT NULL,                 -- AAAA-MM-DD, UTC
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (material, day)
);
