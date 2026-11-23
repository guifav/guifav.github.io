-- Pedido e download de material por pessoa (issue #99). O pedido passa a ter linha própria em `conversions`, com o
-- email ainda não comprovado; a contagem sem email de `material_requests` (0004) deixa de existir.
ALTER TABLE conversions ADD COLUMN verified INTEGER NOT NULL DEFAULT 1; -- 0: pedido (email não comprovado); 1: link aberto
ALTER TABLE conversions ADD COLUMN ga_client_id TEXT;  -- no pedido: client_id do GA4 do navegador, só com consentimento
ALTER TABLE conversions ADD COLUMN request_id TEXT;    -- no download: id do pedido que gerou o link
ALTER TABLE conversions ADD COLUMN file TEXT;          -- no download: id do arquivo baixado (o mapa tem dois)
DROP TABLE IF EXISTS material_requests;
