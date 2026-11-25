---
title: "RAG, contexto longo ou Text-to-SQL: o teste em 80 contratos"
subtitle: "Fiz a mesma pergunta de contagem a um RAG, a uma consulta SQL e a um modelo com os 80 contratos inteiros na janela. O RAG respondeu que nenhum contrato vencia em março, quando eram 17. Aqui estão o método, os números e o critério que uso para escolher a arquitetura."
description: "Medi RAG, contexto longo e Text-to-SQL em 80 contratos e numa planilha de 5 mil linhas: quem acertou, quanto cada um custou em tokens e quando usar cada um."
summary: "Para contar registros, a arquitetura indicada é Text-to-SQL: num teste com 80 contratos, o RAG respondeu que nenhum vencia em março de 2027, quando eram 17, e a consulta SQL devolveu os 17. O contexto longo também acertou, ao custo de US$ 1,81 de entrada por pergunta e 35 segundos até a primeira palavra, e a pergunta que mistura número e documento pede consulta e leitura encadeadas."
date: 2026-11-24T12:00:00Z
slug: rag-contexto-longo-ou-text-to-sql-teste-80-contratos
type: newsletter
---

Para contar registros, use Text-to-SQL: no meu teste com 80 contratos, que mediu uma pergunta de contagem, o RAG respondeu que nenhum vencia em março de 2027, quando eram 17, e a consulta SQL devolveu 17. O contexto longo também acertou os 17, ao custo de US$ 1,81 de entrada por pergunta e 35 segundos até a primeira palavra da resposta. O RAG costuma servir para localizar um trecho, caso que este teste não mediu, e a pergunta que mistura número e documento pede a consulta e a leitura encadeadas.

Em 16 de novembro escrevi aqui sobre [MCP aberto ou aplicação](/blog/mcp-ou-aplicacao-dados-da-empresa-na-ia/). Uma das garantias de saída que defendi ali era fazer todo cálculo em código e ligar cada número da resposta à consulta que o gerou. Era um princípio sem medição. Neste texto eu meço o que acontece quando a empresa escolhe outra arquitetura para a mesma pergunta.

Organizei o texto em sete partes: a diferença entre as três arquiteturas, o método, os resultados, o motivo do erro do RAG, a pergunta que pede duas arquiteturas, os limites do teste e o critério de escolha. No fim ficam as perguntas frequentes. Os números vêm de medições que fiz em 22 de novembro de 2026 para um vídeo do canal [@guifav](https://www.youtube.com/@guifav), e os documentos são de uma empresa de exemplo, criada para o teste.

## Qual a diferença entre RAG, Text-to-SQL e contexto longo?

Quando a empresa resolve que a IA vai responder com os dados dela, o time técnico costuma trazer três propostas. A primeira é o RAG, geração aumentada por recuperação: o sistema corta os documentos em pedaços, busca os pedaços mais parecidos com a pergunta e manda só esses ao modelo. A segunda é o Text-to-SQL: o modelo escreve uma consulta, e quem faz a conta é o banco de dados. A terceira é o contexto longo: com janelas de 1 milhão de tokens nos modelos atuais do Claude, manda-se o conjunto inteiro de documentos em cada pergunta.

As três funcionam numa demonstração. Antes de aprovar o orçamento, quem decide precisa saber qual delas erra em silêncio no tipo de pergunta que a empresa faz todo dia e quanto custa cada pergunta respondida.

## Como foi o teste?

Montei uma empresa de exemplo com 80 clientes e um contrato de prestação de serviço por cliente. Cada contrato é um PDF de 12 páginas, com texto selecionável, gerado por um script determinístico. Os contratos variam no título, na numeração das cláusulas, na redação e nos anexos. A semelhança média entre dois deles, medida como Jaccard das frases sem nomes e números, é 0,16.

A pergunta foi "Quantos contratos vencem em março de 2027?". O gabarito é conhecido por construção: 17 contratos terminam em março de 2027, e nenhum outro menciona março de 2027. A data aparece na cláusula de vigência, escrita de três formas (`31/03/2027`, `31.03.2027` e `15 de março de 2027`) e em seis modelos de frase. Ao lado dos PDFs, um cadastro em CSV tem uma linha por contrato com a data de término e o valor mensal, sem o índice de reajuste e sem a multa.

Rodei cada arquitetura com a configuração mais comum que conheço. No RAG, o texto dos PDFs foi cortado em pedaços de até 800 tokens com 100 de sobreposição, os embeddings vieram do `BAAI/bge-m3` rodando local, e os 5 pedaços mais próximos da pergunta, por semelhança de cosseno, foram ao Claude Haiku 5.5. O índice ficou com 701 trechos. O prompt é o clássico do LangChain Hub (`rlm/rag-prompt`), traduzido, e nada nele manda o modelo avisar ou esconder que leu pouco. Fixei todos os parâmetros antes da primeira execução e não mexi neles depois de ver o resultado.

No Text-to-SQL, nenhum modelo rodou nesta rodada: carreguei o cadastro num SQLite e rodei uma consulta escrita à mão, `SELECT COUNT(*) FROM cadastro_contratos WHERE data_fim BETWEEN '2027-03-01' AND '2027-03-31'`. No contexto longo, mandei o texto integral dos 80 PDFs no prompt do Claude Sonnet 5.5, sem cache, com a pergunta e o pedido de listar o cliente e a data de término de cada contrato. Contei os tokens no endpoint de contagem da Anthropic e medi em três chamadas o tempo até a primeira palavra da resposta.

Para separar o efeito da arquitetura no custo, medi também uma planilha de faturamento de 5 mil linhas da mesma empresa de exemplo. A pergunta foi o faturamento líquido do terceiro trimestre de 2026, por região e no total, só com os pedidos concluídos. Contei os tokens de entrada de duas versões da mesma pergunta no Claude Opus 5.5: uma com a planilha inteira no contexto e outra com a consulta SQL já pronta e o resultado dela.

## O que cada arquitetura acertou e quanto gastou?

| Arquitetura | O que o modelo leu | Tokens de entrada | Resposta | Como falhou |
|---|---|---|---|---|
| RAG (bge-m3, 5 trechos, Haiku 5.5) | 5 trechos de até 800 tokens | cerca de 4 mil (5 × 800 tokens no tokenizador do bge-m3, mais o prompt) | "Nenhum." nas quatro rodadas | Em silêncio: resposta segura, sem aviso de que leu 5 trechos de 80 contratos |
| Text-to-SQL (SQLite) | nenhum modelo nesta rodada: consulta escrita à mão, rodada no SQLite sobre as 80 linhas do cadastro | sem modelo | 17, com a consulta visível | Só responde o que virou coluna |
| Contexto longo (Sonnet 5.5) | os 80 contratos inteiros | 907.163 dos contratos; 907.200 com a pergunta | 17, nas três rodadas, com cliente e data de cada um | Custo e espera: US$ 1,81 de entrada por pergunta e 35 s até a primeira palavra |

Os cinco trechos que o RAG recuperou eram cláusulas de vigência, de contratos que terminam em maio de 2027, junho de 2027, novembro de 2027, novembro de 2028 e dezembro de 2028. A busca acertou o assunto e errou o mês, e o modelo respondeu, sem ressalva, que nenhum contrato vencia em março.

O contexto longo acertou nas três rodadas, e a espera variou: a primeira palavra chegou em 35,0 s, 36,5 s e 12,7 s, com mediana de 35,0 s. Esse tempo inclui o raciocínio do modelo antes do texto visível, e o primeiro evento da resposta chegou em 24,6 s de mediana. Os 907 mil tokens ocupam 91% da janela de 1 milhão do Sonnet 5.5. Pelo preço de lista da Anthropic lido em 22 de novembro, de US$ 2 por milhão de tokens de entrada, cada pergunta custa US$ 1,81 só de entrada. Com duzentas pessoas fazendo dez perguntas por dia, um volume de exemplo, são US$ 3.628 por dia e US$ 79,8 mil num mês de 22 dias úteis.

O cache de prompt barateia a leitura repetida para US$ 0,10 por milhão, e a mesma conta cai para US$ 0,09 por pergunta e US$ 3,99 mil por mês. Essa conta deixa de fora a gravação inicial no cache, cobrada a 1,25 vez o preço de entrada, e as regravações a cada vez que o cache expira ou o começo do prompt muda. O cache vale para o começo do prompt que se repete idêntico e dura cinco minutos por padrão, renovados a cada uso. Quando a base muda ao longo do dia, ou quando cada área só pode ler uma parte dos documentos, o começo do prompt muda e o cache deixa de servir entre as pessoas.

Na planilha de 5 mil linhas, a arquitetura mudou o tamanho da pergunta em 808 vezes:

| Versão da pergunta | Tokens de entrada (Opus 5.5) |
|---|---|
| Planilha inteira no contexto | 447.568 |
| Consulta SQL e resultado no contexto | 554 |

## Por que o RAG erra perguntas de contagem?

Contar contratos exige olhar os 80, e o modelo recebeu cinco trechos. A busca escolhe por semelhança de texto, e a semelhança não garante cobertura: o que fica fora do recorte não existe para o modelo. Rodei um diagnóstico à parte, sobre o mesmo índice, para saber onde ficaram os 17 contratos de março.

| Primeiros trechos da busca | Trechos de contratos de março | Contratos de março distintos |
|---:|---:|---:|
| 5 | 0 | 0 |
| 10 | 1 | 1 |
| 20 | 4 | 4 |
| 50 | 10 | 9 |
| 100 | 19 | 12 |

O primeiro trecho de um contrato de março aparece na posição 7. Reescrever a pergunta como "Quais contratos têm término em março de 2027?" deu o mesmo quadro. Mesmo pedindo 100 trechos, entram 12 dos 17.

Dá para pôr a data de término como filtro nos metadados do índice. Nesse caso a data virou coluna, e quem responde a contagem passa a ser um filtro estruturado, o mesmo trabalho que o Text-to-SQL faz. Em produção, nenhuma falha me preocupa tanto quanto a resposta segura montada sobre o recorte errado. Erro que aparece, o time corrige. Erro silencioso chega à reunião de diretoria como fato.

## Como usar um LLM com banco de dados SQL e documentos na mesma pergunta?

A pergunta que a diretoria faz de verdade costuma pedir duas arquiteturas: quais dos dez maiores clientes do último trimestre têm reajuste pelo IPCA no contrato? A primeira metade é conta, e os dez maiores saem da base de faturamento, com SQL. A segunda é leitura, porque o índice está escrito em cada contrato, e num deles a cláusula da página 3 remete a um termo aditivo na página 12, a única que diz IPCA. RAG sozinho não soma faturamento, SQL sozinho não alcança a cláusula, e os 80 contratos na janela custariam os mesmos 907 mil tokens sem trazer o faturamento, que mora em outra base.

O desenho que funciona encadeia as duas. A consulta descobre os dez clientes e mostra o SQL. Com a lista na mão, o sistema abre só os contratos desses dez, que somam 113.501 tokens, cerca de 11 mil por contrato, e lê cada um separado. A resposta diz de onde veio cada parte: o ranking, da consulta; o índice, do contrato, com o nome do arquivo. Na empresa de exemplo, quatro dos dez têm IPCA. No TalkWithData, a resposta a essa pergunta trouxe os quatro, inclusive o do aditivo, numa captura de 23 de novembro. Declaro o conflito de interesse: eu construo o TalkWithData, e o critério deste texto vale para qualquer ferramenta, a minha incluída.

Li e colegas, do Google DeepMind e da Universidade de Michigan, compararam RAG e contexto longo em vários conjuntos de dados públicos ([EMNLP 2024](https://arxiv.org/abs/2407.16833)): com recurso suficiente, o contexto longo ganhou na média, o RAG custou bem menos, e um roteador em que o próprio modelo decide se a pergunta pede trechos ou o texto inteiro manteve o desempenho perto do contexto longo e cortou o custo em 65% no Gemini 1.5 Pro e em 39% no GPT-4o. No estudo de Berkeley e Stanford sobre o TAG ([arXiv 2408.14717](https://arxiv.org/abs/2408.14717)), com perguntas sobre bancos reais que pedem a consulta e também conhecimento do modelo, os métodos padrão não passaram de 20% de acerto, e um pipeline escrito à mão que combina a consulta com o raciocínio do modelo chegou a 55%. Aquelas perguntas foram montadas para pedir as duas coisas, então a taxa da sua base só aparece medindo na sua base.

## Quais são os limites deste teste?

A base é sintética. Os contratos foram gerados para o teste, com datas escritas de três formas e nenhuma escondida ou quebrada, e contratos reais costumam ser mais bagunçados, o que tende a piorar a busca e a leitura.

No Text-to-SQL, a consulta foi escrita por mim e fixada no script. O teste mede o que o banco devolve com a consulta certa e deixa de fora a capacidade de um modelo escrever essa consulta. Esse risco existe e tem mitigação conhecida, que descrevo nas perguntas frequentes. O gabarito de 17 sai do mesmo cadastro que a consulta lê, então o acerto do SQL confirma a consulta e pressupõe que o cadastro esteja certo.

O RAG rodou com uma configuração: um modelo de embeddings, pedaços de 800 tokens e 5 trechos, sem reranqueamento e sem busca híbrida por palavra-chave. Outra configuração mudaria as posições da tabela de cobertura. Com 5 trechos, porém, nenhuma busca entrega 17 contratos diferentes, e qualquer pergunta de contagem que dependa de mais documentos do que o recorte comporta falha do mesmo jeito.

O contexto longo teve três rodadas, num modelo, e a espera variou bastante entre elas. Os preços são os de lista da Anthropic lidos em 22 e 23 de novembro de 2026, e mudam rápido. A contagem da planilha foi feita em outro modelo, o Opus 5.5, e mede só a entrada, sem gerar resposta. Os 554 tokens cobrem a instrução, a consulta pronta, o resultado e a pergunta. Num Text-to-SQL real, há antes uma chamada em que o modelo recebe o esquema da tabela para escrever a consulta, e ela ficou fora da conta.

Também deixei de medir a pergunta em que o RAG costuma ir bem, a que localiza um trecho, como a multa por rescisão de um contrato específico. O que medi vale para uma pergunta de contagem. Soma e ranking se apoiam no mesmo mecanismo, o banco percorrendo todas as linhas, e ficaram sem medição própria aqui. Contagem, soma e ranking são as perguntas que eu mais vejo em reunião de resultado.

## Quando usar RAG, Text-to-SQL ou contexto longo?

Antes de escolher, faço quatro perguntas sobre a pergunta do usuário. A primeira é se a resposta é um número que sai de muitas linhas: nesse caso, Text-to-SQL. A segunda é se ela mora em poucos trechos de uma base grande de documentos: RAG. A terceira é se o conjunto cabe inteiro na janela e a resposta depende de ler tudo: contexto longo, com o custo por pergunta calculado antes. A quarta é se a pergunta mistura número e documento: roteamento, com a origem de cada parte na resposta.

| Pergunta típica | Arquitetura | Como falha |
|---|---|---|
| Quantos contratos vencem no mês, quanto o trimestre faturou | Text-to-SQL | Responde só o que virou coluna; a consulta errada fica visível |
| Qual é a multa no contrato de tal cliente | RAG | Em silêncio, quando a resposta está fora dos trechos recuperados |
| O aditivo da última página muda o sentido da página 3? | Contexto longo | Custo e espera, que crescem com o tamanho do conjunto |
| Quais dos maiores clientes têm reajuste pelo IPCA | Roteamento: consulta, depois leitura | Quando a resposta não diz de onde veio cada parte |

O [mapa de decisão](/tools/mapa-decisao-ia/) que publiquei em 23 de novembro traz essas quatro perguntas, a matriz completa com latência e mitigação de cada arquitetura, a planilha da conta do custo por pergunta e cinco testes para rodar antes de aprovar um projeto. Para os fundamentos do RAG, há a [introdução ao tema](/blog/o-que-e-rag-llms-ia/) e o texto sobre [RAG em bases de conhecimento corporativas](/blog/rag-knowledge-bases-corporativas-implementacao-pratica/); para a ligação entre modelos e bancos de dados, o [guia de integração de LLMs com SQL e NoSQL](/blog/llm-sql-nosql-guia-integracao/).

Antes de aprovar qualquer uma delas, eu pergunto ao time se a arquitetura avisa quando não leu tudo. Na sua empresa, quantas das respostas que a IA já entrega à diretoria passariam nessa pergunta?

## Perguntas frequentes

### RAG serve para contar documentos?

Na configuração mais comum, serve mal. O RAG manda ao modelo só os trechos mais parecidos com a pergunta, e no meu teste os 5 trechos não incluíram nenhum dos 17 contratos certos. Para contagem, a data ou o atributo precisa virar coluna, seja numa tabela, seja num filtro de metadados.

### Uma janela de 1 milhão de tokens substitui o RAG?

No meu teste, ela acertou nas três rodadas quando o conjunto coube na janela, como os 80 contratos com 907 mil tokens. O custo e a espera crescem com o tamanho da base: US$ 1,81 de entrada e 35 segundos até a primeira palavra por pergunta, com os 80 contratos. O relatório [Context Rot](https://www.trychroma.com/research/context-rot), da Chroma, testou 18 modelos e viu o desempenho cair conforme a entrada crescia em todos os experimentos.

### Quanto custa mandar tudo no contexto?

É a conta de tokens por pergunta vezes o preço por milhão vezes o número de perguntas. Com os 80 contratos no Sonnet 5.5, sem cache, uma empresa com duas mil perguntas por dia gastaria US$ 79,8 mil num mês de 22 dias úteis, só de entrada. A planilha anexa ao [mapa de decisão](/tools/mapa-decisao-ia/) faz essa conta com os seus números, e a [planilha de custos de IA](/tools/planilha-custos-ia/) compara API, assinatura e GPU própria.

### Text-to-SQL é seguro com os dados da empresa?

Tem riscos próprios, e cada um tem mitigação conhecida. A consulta roda com um usuário que só lê, num ambiente isolado, com tempo máximo e limite de linhas. As definições de negócio, como receita bruta ou líquida, ficam escritas numa camada semântica. A permissão mora na camada de dados, porque uma instrução no prompt o modelo pode ignorar. E a consulta fica visível, com uma bateria de perguntas de resposta conhecida rodando antes de o sistema chegar ao time.

### Como testar a arquitetura antes de aprovar o projeto?

Com perguntas de gabarito conhecido na base da própria empresa: uma contagem, um fato no meio de um documento longo, uma pergunta que cruza número e documento, uma pessoa sem acesso tentando ler uma coluna fechada e uma pergunta maior do que o sistema consegue ler. O [mapa de decisão](/tools/mapa-decisao-ia/) diz o que conta como passar em cada um.

---

Se você está decidindo essa arquitetura na sua empresa e quer uma segunda opinião antes de comprometer o orçamento, [agende uma conversa comigo](https://calendar.app.google/VyG67Nq5p6zsstAt8).
