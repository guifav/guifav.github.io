-- Id da Pessoa no Twenty a que a Conversão foi ligada (issue #193), usado para montar o link do painel /admin/contatos
-- (`https://crm.guifav.dev/object/person/<id>`).
-- Gravado só pelo `conversoes_sync.py`, ao marcar `synced_at` nas linhas novas e no preenchimento das linhas antigas
-- sincronizadas antes desta migração; para uma Conversão que já existia no Twenty, o id gravado é o `pessoaId` dela.
-- Fica nulo enquanto a linha não foi sincronizada (inclusive no pedido sem Pessoa no CRM e na linha pendente de Pessoa
-- na lixeira), quando o id devolvido pelo Twenty está ausente ou fora do padrão (`pessoa_id_invalido`) e nas linhas
-- sincronizadas sem Conversão no CRM.
-- O Twenty grava UUID, mas o CHECK não exige formato de UUID: garante só o que o link precisa (letras, números e
-- hífen, de 1 a 64 caracteres), evitando espaços, barras ou caracteres de URL. O `--forget` (#134) apaga a coluna
-- junto com a linha em `conversions`.
ALTER TABLE conversions ADD COLUMN twenty_person_id TEXT
  CHECK (length(twenty_person_id) BETWEEN 1 AND 64 AND twenty_person_id NOT GLOB '*[^0-9A-Za-z-]*');
