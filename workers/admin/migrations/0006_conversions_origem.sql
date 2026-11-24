-- Origem da visita em que a pessoa converteu (issue #114): um rótulo, sem identificador, gravado pelo Worker
-- `blog-newsletter` a partir dos campos ocultos que o assets/track.js põe nos formulários. Nulo nas linhas anteriores
-- a esta migração ("sem medição"); depois dela, o canal é sempre gravado e vale "desconhecida" quando o navegador não
-- mandou a origem.
-- Os CHECK repetem as listas e os padrões de workers/newsletter/src/origem.ts: a URL completa de onde a pessoa veio, o
-- termo buscado (nenhum "?", ":", "=", "&", "%" ou espaço passa) e qualquer outro texto livre não entram na tabela,
-- mesmo que o Worker tenha um defeito. Mudar uma lista é mudar o Worker, o track.js, o sync e uma migração nova.
ALTER TABLE conversions ADD COLUMN origin_channel TEXT
  CHECK (origin_channel IN ('busca', 'assistente', 'social', 'email', 'pago', 'outro_site', 'direto', 'desconhecida'));
ALTER TABLE conversions ADD COLUMN origin_source TEXT
  CHECK (origin_source IN ('google', 'bing', 'chatgpt', 'perplexity', 'claude', 'copilot', 'gemini', 'linkedin', 'youtube',
                           'github', 'outro'));
ALTER TABLE conversions ADD COLUMN utm_source TEXT
  CHECK (length(utm_source) BETWEEN 1 AND 64 AND utm_source NOT GLOB '*[^a-z0-9._-]*');
ALTER TABLE conversions ADD COLUMN utm_medium TEXT
  CHECK (length(utm_medium) BETWEEN 1 AND 64 AND utm_medium NOT GLOB '*[^a-z0-9._-]*');
ALTER TABLE conversions ADD COLUMN utm_campaign TEXT
  CHECK (length(utm_campaign) BETWEEN 1 AND 64 AND utm_campaign NOT GLOB '*[^a-z0-9._-]*');
ALTER TABLE conversions ADD COLUMN landing_path TEXT  -- caminho da página de entrada, sem query nem fragmento
  CHECK (length(landing_path) BETWEEN 1 AND 200 AND substr(landing_path, 1, 1) = '/' AND instr(landing_path, '//') = 0
         AND landing_path NOT GLOB '*[^A-Za-z0-9/._-]*');
