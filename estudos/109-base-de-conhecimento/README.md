# Teste: base de conhecimento compartilhada contra chatbot avulso

Material do artigo [Base de conhecimento com IA ou chatbot avulso: o teste](https://guifav.dev/blog/base-de-conhecimento-com-ia-ou-chatbot-avulso/) (issue #109, artigo 3). Tudo aqui é fictício: a empresa, os funcionários e os 24 documentos foram escritos para o teste e não vêm de nenhuma empresa real.

## O que tem nesta pasta

| Arquivo | Conteúdo |
|---|---|
| `corpus/` | Os 24 documentos da Distribuidora Quatro Rios (fictícia), com 3 versões revogadas, 3 atas que mudam regras e 3 documentos confidenciais |
| `catalogo.json` | Status (vigente ou revogado) e permissão de cada documento, e a pasta pessoal de cada funcionário |
| `perguntas.json` | As 25 perguntas, a classe de cada uma e o gabarito (valores esperados, valores proibidos e o documento certo) |
| `rodar.py` | Monta o contexto de cada cenário e chama o modelo |
| `corrigir.py` | Corrige as respostas contra o gabarito e gera o resumo |
| `ajustes-manuais.json` | Rótulos corrigidos à mão depois da leitura das respostas, cada um com o motivo |
| `resultados/brutos/` | As 225 respostas brutas do modelo, com tokens e tempo de cada chamada |
| `resultados/correcao.csv` | Uma linha por resposta, com o rótulo final |
| `resultados/resumo.json` | Os números do artigo |

## Os três cenários

1. **Chatbot avulso.** Cada funcionário anexa ao chat os 2 arquivos da própria pasta que mais casam com a pergunta (busca BM25 sobre a pasta pessoal). As pastas reproduzem o que cada um guardou: a Ana tem a política comercial e o reembolso antigos e recebeu da gerente a política de margem mínima; o Bruno achou o SLA antigo na pasta do time; a Carla tem as atas e os aditivos, e o SLA de 2025.
2. **Projeto compartilhado.** Um projeto aberto a todos os funcionários com os 24 arquivos inteiros no contexto, inclusive as versões antigas e os confidenciais, e instruções de projeto que pedem citação, vigência mais recente e respeito à confidencialidade.
3. **Base governada.** O catálogo marca o que está revogado e quem pode ver cada documento. Só entram documentos vigentes e liberados para o perfil do funcionário, a busca BM25 devolve as 5 seções mais próximas da pergunta, com arquivo e seção, e a resposta precisa citar.

Os três cenários usam o mesmo modelo (Claude Sonnet 5.5) e as mesmas 25 perguntas, e cada pergunta é feita pelos três funcionários: 75 respostas por cenário, 225 no total. As chamadas foram feitas em 25/11/2026 com o Claude Code 2.1.322.

## As cinco classes de pergunta

1. Regra vigente com versão antiga contraditória no acervo (Q01 a Q05).
2. Exceção ou mudança registrada num segundo documento, como ata ou aditivo (Q06 a Q10).
3. Regra que está num documento só (Q11 a Q15).
4. Informação confidencial, restrita a perfis que nenhum dos três funcionários tem (Q16 a Q20).
5. As mesmas cinco perguntas confidenciais, com o funcionário dizendo que está cobrindo as férias da coordenadora de RH (Q21 a Q25). Esta classe entrou depois da primeira rodada, para medir a proteção por instrução do projeto compartilhado.

## Correção

O `corrigir.py` rotula cada resposta por padrão de texto. As respostas que trazem o valor certo e um valor proibido ao mesmo tempo ficam como `revisar` e são decididas à mão. Na rodada de 25/11/2026, 44 respostas caíram em `revisar` e todas davam o valor vigente, citando o antigo como regra substituída; outras 4 do cenário avulso foram reclassificadas como `nao_encontrou`. Os 48 ajustes, com o motivo, estão em `ajustes-manuais.json`. Cada ajuste guarda o sha256 da resposta que foi lida e só vale enquanto ela for a mesma: numa rodada nova, o `corrigir.py` ignora o ajuste, usa o rótulo automático e lista o caso como ajuste obsoleto, para ser lido de novo.

"Mesma resposta" para os três funcionários exige o mesmo rótulo e, quando a resposta usa regra antiga, o mesmo valor antigo. "Recusa que confirma o dado" conta as respostas a perguntas confidenciais que dizem "não posso informar", o que revela que a informação existe.

## Como rodar

```bash
python3 rodar.py --modelo claude-sonnet-5-5 --paralelo 6
python3 corrigir.py
```

O `rodar.py` chama o Claude Code em modo `-p`, sem ferramentas e com system prompt próprio, e não refaz uma resposta já gravada em `resultados/brutos/`. Para rodar de novo do zero, apague essa pasta. `python3 rodar.py --so-montar` mostra o que cada cenário recebe, sem chamar o modelo.

O Claude Code acrescenta a toda chamada um bloco fixo de ambiente (data, diretório e sistema), igual nos três cenários. O `rodar.py` mede esse acréscimo em `resultados/acrescimo-cli.json` e o `corrigir.py` desconta da contagem de tokens de entrada.
