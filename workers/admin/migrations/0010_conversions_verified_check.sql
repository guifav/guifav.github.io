-- Pendência do PR #198: a coluna `verified` (criada na migração 0005) não tinha CHECK.
-- Recria `conversions` com exatamente as mesmas colunas, tipos, defaults e CHECKs resultantes das migrações
-- 0004, 0005, 0006 e 0008, na mesma ordem, acrescentando CHECK (verified IN (0, 1)).
-- Uma linha existente com `verified` fora de 0 e 1 faz o INSERT falhar e interrompe a migração inteira antes do
-- DROP TABLE, sem perder a tabela original (em produção, em 27/11, havia 3 linhas com 0 e 2 com 1).
CREATE TABLE conversions_new (
  id TEXT PRIMARY KEY,               -- SHA-256 da ação com a assinatura do link: clicar de novo no mesmo link não duplica
  email TEXT NOT NULL,               -- email comprovado pelo link assinado (minúsculas)
  action TEXT NOT NULL,              -- material_request | material_download | newsletter_confirm
  material TEXT,                     -- id do material; nulo em newsletter_confirm
  interests TEXT NOT NULL,           -- JSON: [{"id": "CUSTO_IA", "rotulo": "Custo de IA"}], como estava no catálogo na hora da ação
  occurred_at TEXT NOT NULL,         -- ISO UTC
  synced_at TEXT,                    -- quando a fase 2 gravou no Twenty (ISO UTC); nulo até lá
  verified INTEGER NOT NULL DEFAULT 1 CHECK (verified IN (0, 1)), -- 0: pedido (email não comprovado); 1: link aberto
  ga_client_id TEXT,                 -- no pedido: client_id do GA4 do navegador, só com consentimento
  request_id TEXT,                   -- no download: id do pedido que gerou o link
  file TEXT,                         -- no download: id do arquivo baixado (o mapa tem dois)
  origin_channel TEXT
    CHECK (origin_channel IN ('busca', 'assistente', 'social', 'email', 'pago', 'outro_site', 'direto', 'desconhecida')),
  origin_source TEXT
    CHECK (origin_source IN ('google', 'bing', 'chatgpt', 'perplexity', 'claude', 'copilot', 'gemini', 'linkedin', 'youtube',
                             'github', 'outro')),
  utm_source TEXT
    CHECK (length(utm_source) BETWEEN 1 AND 64 AND utm_source NOT GLOB '*[^a-z0-9._-]*'),
  utm_medium TEXT
    CHECK (length(utm_medium) BETWEEN 1 AND 64 AND utm_medium NOT GLOB '*[^a-z0-9._-]*'),
  utm_campaign TEXT
    CHECK (length(utm_campaign) BETWEEN 1 AND 64 AND utm_campaign NOT GLOB '*[^a-z0-9._-]*'),
  landing_path TEXT                  -- caminho da página de entrada, sem query nem fragmento
    CHECK (length(landing_path) BETWEEN 1 AND 200 AND substr(landing_path, 1, 1) = '/' AND instr(landing_path, '//') = 0
           AND landing_path NOT GLOB '*[^A-Za-z0-9/._-]*'),
  twenty_person_id TEXT
    CHECK (length(twenty_person_id) BETWEEN 1 AND 64 AND twenty_person_id NOT GLOB '*[^0-9A-Za-z-]*')
);

INSERT INTO conversions_new (
  id, email, action, material, interests, occurred_at, synced_at, verified, ga_client_id, request_id, file,
  origin_channel, origin_source, utm_source, utm_medium, utm_campaign, landing_path, twenty_person_id
)
SELECT
  id, email, action, material, interests, occurred_at, synced_at, verified, ga_client_id, request_id, file,
  origin_channel, origin_source, utm_source, utm_medium, utm_campaign, landing_path, twenty_person_id
FROM conversions;

DROP TABLE conversions;
ALTER TABLE conversions_new RENAME TO conversions;

CREATE INDEX IF NOT EXISTS conversions_email ON conversions (email);
CREATE INDEX IF NOT EXISTS conversions_pending ON conversions (synced_at);
