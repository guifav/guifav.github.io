---
title: "MCP aberto ou aplicação: quem garante a resposta da IA na sua empresa"
subtitle: "Ligar os dados da empresa ao Claude e ao ChatGPT via MCP ficou barato. Um critério para decidir quando basta perguntar ao assistente com os dados abertos via MCP e quando o processo pede uma aplicação, e o que dá para garantir na entrada e na saída de cada caminho."
description: "Ligar os dados da empresa ao Claude e ao ChatGPT via MCP ficou barato. Um critério para decidir quando basta perguntar ao assistente e quando o processo pede uma aplicação."
summary: "O conector MCP, que liga os dados da empresa ao Claude ou ao ChatGPT, e a aplicação de processo podem usar o mesmo dado, com a mesma identidade e o mesmo registro de consultas, e se separam em quem garante a resposta: no MCP aberto, a pessoa que perguntou; na aplicação, a empresa. Um processo deve subir para aplicação quando a saída vai para quem não fez a pergunta ou quando o erro é caro ou difícil de desfazer."
date: 2026-11-16T12:00:00Z
slug: mcp-ou-aplicacao-dados-da-empresa-na-ia
cover: img/mcp-ou-aplicacao-dados-da-empresa-na-ia/capa.png
type: newsletter
topics: [ia-sobre-dados-da-empresa, agentes-de-ia]
---

Em março de 2025 escrevi aqui sobre o [MCP, o Model Context Protocol](/blog/mcp-model-context-protocol-revolucao-inteligencia-artificial/). O argumento era que, sem um padrão, cada ligação entre um modelo de linguagem e um sistema da empresa precisava ser construída sob medida, e esse custo travava o uso de IA com dados reais. Na época era uma proposta recente da Anthropic, com poucos servidores disponíveis.

Hoje o Claude, o ChatGPT e o Copilot aceitam conectores MCP, e ligar um banco de dados ou um CRM a esses assistentes leva poucos dias. O custo que eu apontava em 2025 praticamente sumiu. Sobrou para cada empresa decidir quem responde quando o assistente devolver um número errado.

Os dados da empresa chegam à IA por dois caminhos. No primeiro, a empresa liga suas fontes por um conector MCP ao assistente que as pessoas já usam. A pessoa abre o Claude ou o ChatGPT, escreve a pergunta do jeito que quiser, e o assistente decide qual consulta fazer, recebe os dados e redige a resposta. No segundo, a empresa constrói ou contrata uma aplicação para um processo específico, como avaliar ligações ou qualificar leads. Nela, o prompt, o modelo, as consultas e as regras de validação já vêm definidos, e a pessoa recebe um resultado pronto sem escrever prompt nenhum, ao custo de menos flexibilidade. **Os dois caminhos podem usar o mesmo dado, com a mesma identidade e o mesmo registro de consultas, e se separam em quem garante a resposta**: no primeiro, a pessoa que perguntou; no segundo, a empresa.

<figure>
<a href="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/01-dois-caminhos-1456.webp"><img src="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/01-dois-caminhos-1456.webp" srcset="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/01-dois-caminhos-728.webp 728w, /blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/01-dois-caminhos-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="816" alt="Animação: dados de CRM, ERP, financeiro, atendimento e marketing passam por uma camada única, com a identidade de quem pergunta, a permissão no servidor e o registro de cada consulta, e seguem por dois caminhos. Na janela do assistente tipo Claude Desktop, alguém pergunta a receita por região no trimestre, recebe que o Sudeste lidera e o Norte caiu, e o usuário precisa garantir que a resposta esteja correta. Na janela do app interno, a mesma conclusão sai de um filtro de trimestre, pela definição oficial de receita, e a empresa garante o número." loading="lazy" decoding="async"></a>
<a class="baixar" href="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/01-dois-caminhos.gif" download title="Baixar a animação em GIF">↓ Baixar</a>
<figcaption><em>Fig. 1. As fontes passam por uma camada única, com a identidade de quem pergunta e o registro de cada consulta, e servem aos dois caminhos.</em></figcaption>
</figure>

Na tabela abaixo comparo os dois caminhos nos pontos que as seções seguintes detalham.

| Critério | MCP aberto no assistente | Aplicação de processo |
|---|---|---|
| Quem escreve a pergunta | A pessoa, do jeito que quiser | A aplicação, com a pergunta fixada (um filtro faz o papel da pergunta) |
| Quem escolhe o modelo | O usuário, a cada conversa | A aplicação, que registra o modelo e a versão de prompt de cada resultado |
| Quando o dado não serve | O resultado depende do modelo do dia e de como a pessoa pediu | A recusa é regra escrita e vale com qualquer modelo |
| Quem garante a resposta | A pessoa que perguntou | A empresa, na pessoa que responde pelo processo |
| Base comum | Mesmo dado, mesma identidade e mesmo registro de consultas | Mesmo dado, mesma identidade e mesmo registro de consultas |
| Melhor uso | Descoberta e análise consumida por quem perguntou | Saída que vai para terceiros ou erro caro e difícil de desfazer |

Organizei o argumento em seis seções. As duas primeiras mostram o que cada caminho entrega, com duas aplicações que construímos internamente como exemplo: uma que centraliza as fontes da empresa e as serve com a identidade de quem pergunta, e outra que avalia as ligações do time comercial. A seção 03 traz o critério que uso para escolher o caminho de cada processo, a 04 mapeia as alternativas que ficam entre os dois extremos, a 05 trata do que dá para garantir na entrada e na saída, e a 06 descreve a ordem de investimento que recomendo.

## 01. Por que o MCP aberto é o laboratório mais barato que uma empresa pode montar?

Quem tem uma pergunta nova chega à resposta no mesmo dia, sem fila de desenvolvimento e sem especificação. E cada consulta que o assistente faz fica registrada no servidor MCP, com quem perguntou e o que foi consultado.

Esse registro mostra a demanda antes de qualquer investimento em produto. As consultas que voltam toda semana, sobre as mesmas tabelas e com os mesmos filtros, são processos que já existem na prática e ainda não têm dono. Descobrir esses processos costuma exigir rodadas de entrevistas e levantamento de requisitos; com o MCP aberto e um registro bem feito, a lista sai de uma consulta.

Antes de abrir qualquer fonte, exijo que a identidade de quem pergunta chegue até o dado. Na aplicação de centralização que construímos internamente, cada pessoa entra com a própria credencial e enxerga só o que o perfil dela permite, e a permissão é aplicada no servidor, antes da consulta. Um servidor MCP que consulta tudo com uma conta técnica de acesso amplo entrega a cada usuário os dados de todos, e entrega o mesmo acesso a quem conseguir pôr uma instrução num texto que o modelo vá ler. Em julho de 2025 a General Analysis [demonstrou esse risco](https://generalanalysis.com/blog/supabase-mcp-blog) com um assistente de programação ligado a um banco Supabase pela chave de serviço, que ignora as regras de acesso por linha. Um atacante abriu um chamado de suporte com instruções endereçadas ao assistente. Quando o desenvolvedor pediu para revisar os chamados abertos, o assistente leu a tabela de tokens de integração e publicou o conteúdo no próprio chamado, e o atacante só precisou recarregar a página.

Um servidor MCP pode servir, além das tabelas, manuais curados que explicam o que cada campo significa para o negócio, e o modelo para de errar com convicção nos campos cujo nome sugere uma coisa enquanto o processo diz outra.

A garantia do caminho aberto acaba quando o dado sai do servidor. A resposta é escrita no aplicativo do fornecedor, onde o modelo é escolhido pelo usuário a cada conversa e a empresa não tem como saber se o assistente usou fielmente o dado que recebeu. O servidor devolve o dado com origem e data, e o assistente pode ignorar as duas. Por isso trato a saída do caminho aberto como rascunho de quem perguntou. A pessoa é dona da conclusão e responde por ela, o que funciona bem enquanto ela também é quem vai usar o resultado.

<figure>
<a href="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/02-duas-telas-1456.webp"><img src="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/02-duas-telas-1456.webp" srcset="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/02-duas-telas-728.webp 728w, /blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/02-duas-telas-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="816" alt="Animação com duas janelas lado a lado respondendo a mesma pergunta, receita por região no trimestre, cada uma com três passos numerados. Na janela MCP aberto, Claude Desktop: 1, a pessoa escreve a pergunta no chat; 2, o Claude decide a consulta e busca os dados pelo MCP; 3, o Claude redige, e a pessoa garante que a resposta esteja correta. Na janela Aplicação de processo, App interno: 1, a pessoa escolhe o trimestre no filtro; 2, o app roda a consulta fixa, com a receita oficial; 3, o resultado sai pronto, e a empresa garante o número. As duas chegam à mesma resposta: Sudeste e Sul sobem, o Norte cai." loading="lazy" decoding="async"></a>
<a class="baixar" href="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/02-duas-telas.gif" download title="Baixar a animação em GIF">↓ Baixar</a>
<figcaption><em>Fig. 2. A mesma pergunta pelos dois caminhos. No Claude Desktop, quem pergunta escreve o prompt e precisa garantir que a resposta esteja correta. No app interno, o filtro faz o papel da pergunta, a receita sai pela definição oficial e a empresa garante o número.</em></figcaption>
</figure>

## 02. Por que a aplicação pode se recusar a responder?

A aplicação que construímos internamente para avaliar as ligações do time comercial começa decidindo se a ligação pode ser avaliada. Quando a gravação é uma caixa postal, um silêncio, só uma saudação, um atendimento automático ou uma conversa interna entre colegas, ela devolve o motivo e nenhuma nota. Num assistente aberto via MCP, o destino dessas gravações depende do modelo do dia e de como a pessoa pediu: quem pede uma nota tende a receber uma nota, com justificativa. Na aplicação, a recusa é regra escrita e vale para toda ligação, com qualquer modelo. De tudo que essa aplicação faz, considero a recusa o que mais a separa de um assistente de uso livre.

<figure>
<a href="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/03-aplicacao-recusa-1456.webp"><img src="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/03-aplicacao-recusa-1456.webp" srcset="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/03-aplicacao-recusa-728.webp 728w, /blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/03-aplicacao-recusa-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="816" alt="Animação: ligações chegam a uma triagem que pergunta se há conversa avaliável. As de clientes seguem para a rubrica fixa e saem com nota 7,5, com modelo e prompt registrados e contestação registrada. Caixa postal, atendimento automático, conversa interna e silêncio são desviados e aparecem numa caixa de recusa com o motivo e nenhuma nota. Na mesma caixa: no chat, o resultado depende do modelo e do pedido; aqui, a recusa é regra escrita, com qualquer modelo." loading="lazy" decoding="async"></a>
<a class="baixar" href="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/03-aplicacao-recusa.gif" download title="Baixar a animação em GIF">↓ Baixar</a>
<figcaption><em>Fig. 3. A triagem vem antes da rubrica: caixa postal, silêncio, atendimento automático e conversa interna saem com o motivo e nenhuma nota.</em></figcaption>
</figure>

A nota orienta o desenvolvimento de cada vendedor, e quem a recebe não tem como refazer a avaliação. Por isso a rubrica e os critérios de recusa estão escritos, e cada avaliação guarda o modelo e a versão de prompt que a produziram. Esse registro permitiu atravessar várias trocas de modelo comparando o antes e o depois sobre as mesmas ligações. Quando alguém contesta uma avaliação e a correção é aceita, a nota anterior e a nova ficam guardadas com o motivo da correção, e essas correções viram material para a rubrica seguinte.

O vendedor vê uma nota e um comentário. A aplicação decidiu por ele quando avaliar e com qual régua. Se a nota estiver errada, responde a empresa, na pessoa que assina a rubrica. Toda aplicação de processo precisa desse nome: alguém que responde pelo processo e pelos critérios de aceite, e que decide o que muda quando uma correção é aceita.

## 03. Qual critério usar para escolher o caminho de cada processo?

Uso dois eixos para decidir o destino de cada processo. O primeiro é quem consome a saída. Se é a própria pessoa que perguntou, e ela sabe contestar o número e voltar à fonte, o caminho aberto serve. Se a saída vai para um cliente, para o conselho, para um regulador, para a avaliação de alguém ou para outro sistema, quem a recebe não tem como auditar como a pergunta foi feita, e a empresa precisa fixar a pergunta por ele.

O segundo é o custo do erro, somado à dificuldade de percebê-lo e de desfazê-lo. Um relatório regulatório anual pede o mesmo rigor de um pagamento mensal, mesmo acontecendo doze vezes menos.

Os dois eixos dão quatro destinos. Com a saída nas mãos de quem perguntou e erro barato, o MCP aberto basta. Saída para terceiros com erro barato pede instruções curadas, que fixam definição e formato. Erro caro nas mãos de quem perguntou pede aprovação humana antes da decisão. As duas coisas juntas pedem aplicação. A frequência entra depois e decide se vale investir em construir, e a maturidade decide quando um processo muda de destino.

| Quem consome a saída | Custo do erro | Destino | Exemplos |
|---|---|---|---|
| Quem perguntou | Baixo | MCP aberto | Diretor explorando o pipeline antes de uma reunião interna; analista investigando uma queda de receita |
| Terceiros | Baixo | MCP aberto com instruções curadas | Relatório recorrente para a liderança |
| Quem perguntou | Alto | MCP aberto com aprovação humana antes da decisão | Gerente que vai usar os números para renegociar um contrato grande |
| Terceiros | Alto | Aplicação de processo | Avaliação de ligações, qualificação de leads que grava no CRM, cálculo de comissão, proposta ao cliente |

<figure>
<a href="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/04-criterio-1456.webp"><img src="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/04-criterio-1456.webp" srcset="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/04-criterio-728.webp 728w, /blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/04-criterio-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="816" alt="Animação de uma matriz de decisão. Eixo horizontal: quem consome a saída, da própria pessoa a cliente, conselho ou sistema. Eixo vertical: custo do erro, de baixo a alto. Quadrantes: MCP aberto, com explorar o pipeline e investigar uma queda; MCP aberto mais instruções curadas; MCP aberto mais aprovação humana, com renegociar contrato grande; e aplicação de processo, com proposta ao cliente, avaliação de ligações e cálculo de comissão. Uma pergunta avulsa sobre a semana sai do MCP aberto e chega ao quadrante das instruções curadas como relatório semanal à liderança, quando a mesma consulta aparece pela quarta semana seguida." loading="lazy" decoding="async"></a>
<a class="baixar" href="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/04-criterio.gif" download title="Baixar a animação em GIF">↓ Baixar</a>
<figcaption><em>Fig. 4. Quem consome a saída e o custo do erro definem o destino de cada processo. Quando a mesma consulta volta toda semana, a pergunta avulsa vira relatório e muda de quadrante.</em></figcaption>
</figure>

Um diretor explorando o pipeline antes de uma reunião interna e um analista investigando uma queda de receita ficam no MCP aberto, porque consomem a própria resposta e um erro custa pouco. O relatório recorrente para a liderança vai para terceiros e ainda custa pouco errar, e pede instruções curadas. A gerente que vai usar os números para renegociar um contrato grande consome a própria análise, mas o erro sai caro, e ali cabe o MCP aberto com aprovação humana antes da decisão. A avaliação de ligações, a qualificação de leads que grava no CRM, o cálculo de comissão e a proposta que vai para o cliente juntam os dois riscos e pedem aplicação.

Não dá para codificar um processo que ninguém entende ainda, e construir a aplicação cedo demais congela um fluxo ruim. O caminho aberto é onde o processo aparece, e o registro de consultas mostra quando ele estabilizou: o relatório para a liderança deveria deixar de ser pergunta livre no assistente no dia em que a mesma consulta aparece pela quarta semana seguida.

Os dois caminhos convivem na mesma empresa e muitas vezes no mesmo processo. O MCP é a camada de acesso e a aplicação é a camada de processo, e uma boa aplicação consome o mesmo MCP, com a mesma identidade e o mesmo registro, acrescentando as regras do processo. Quem investiu na fundação de dados e no acesso aberto já pagou boa parte da aplicação.

## 04. Quais caminhos existem entre o MCP aberto e a aplicação?

Entre o chat aberto e a aplicação completa existem caminhos que custam menos do que uma aplicação e garantem mais do que a pergunta livre no assistente. Instruções curadas dentro dos próprios assistentes, com prompts e arquivos de exemplo compartilhados por um time, são o meio-termo mais barato e o mais subestimado. Padronizam o modo de perguntar sem tirar as pessoas do assistente que elas já usam. Em [O prompt virou contrato](/blog/o-prompt-virou-contrato-mudancas-2022-2026/) mostrei que a instrução virou cláusula, e cláusula sem dono envelhece: sem alguém que mantenha e teste essas instruções, elas viram folclore em poucos meses.

O passo seguinte mantém o chat e põe uma pessoa entre a análise e a decisão. No MCP aberto com aprovação humana, a conclusão só vira ação depois que alguém confere a evidência e assina.

Mais perto da aplicação fica o workflow determinístico com passos de LLM: o código decide a ordem, consulta, calcula e valida, e o modelo entra só nos passos que pedem linguagem, como classificar, extrair, resumir ou redigir. É o que eu mais uso, e é também o desenho por dentro da maioria das aplicações de processo, porque é mais simples de testar do que qualquer arranjo de agentes.

Por baixo de todos esses caminhos vale uma camada de métricas certificadas, em que receita, cliente ativo, churn e conversão têm uma definição única. É o investimento que mais melhora as respostas sobre números. Sem ela, o MCP devolve a tabela crua e cada pessoa calcula receita do seu jeito, com a mesma confiança.

Fine-tuning e modelos dedicados ficam fora dessa linha e entram por último. Em [O alpha estava no arquivo](/blog/o-alpha-estava-no-arquivo/), o modelo aberto especializado superou os de fronteira porque havia uma tarefa estreita com volume e um conjunto de avaliação pronto antes do treino. Começar por aí, antes de ter avaliação e processo definido, sai caro.

<figure>
<a href="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/05-espectro-1456.webp"><img src="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/05-espectro-1456.webp" srcset="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/05-espectro-728.webp 728w, /blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/05-espectro-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="816" alt="Animação de uma linha que vai da flexibilidade ao controle, com cinco pontos em ordem: MCP aberto, instruções curadas, aprovação humana, workflow com LLM e aplicação. Por baixo de todos, uma faixa de métricas certificadas, com uma definição de receita, churn e conversão. À parte, fora da linha, o modelo dedicado, só com tarefa estreita e avaliação pronta." loading="lazy" decoding="async"></a>
<a class="baixar" href="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/05-espectro.gif" download title="Baixar a animação em GIF">↓ Baixar</a>
<figcaption><em>Fig. 5. Cada passo para a direita troca flexibilidade por controle. As métricas certificadas valem para todos, e o modelo dedicado fica de fora até existirem tarefa estreita e avaliação pronta.</em></figcaption>
</figure>

## 05. Como garantir a entrada e a resposta?

Na entrada, o controle tem quatro partes. As fontes precisam ser certificadas, com dono e data de atualização. A permissão por identidade, que descrevi na seção 01, precisa ser aplicada no servidor antes da consulta. Cada resposta precisa declarar de quando é o dado, porque uma réplica pode continuar respondendo normalmente com semanas de atraso. E documentos, emails, chamados e páginas da web que entram no contexto precisam ser tratados como conteúdo não confiável.

O servidor merece o mesmo teste de qualquer sistema que separa clientes. Em junho de 2025 a Asana tirou do ar por quase duas semanas o servidor MCP que tinha lançado em maio, depois de achar um bug que podia expor dados de uma organização a usuários de outras ([The Register](https://www.theregister.com/2025/06/18/asana_mcp_server_bug/)). Além do Supabase, em maio de 2025 a [Invariant Labs](https://invariantlabs.ai/blog/mcp-github-vulnerability) mostrou o mesmo padrão de conteúdo não confiável no servidor MCP oficial do GitHub, em que uma issue maliciosa num repositório público levou o agente do usuário a copiar dados de repositórios privados para um pull request público.

Na saída, as garantias vêm em camadas, e cada uma custa mais do que a anterior. A primeira é exigir a resposta num formato fixo e validá-lo em código, o que confere a forma e deixa a verdade para as camadas seguintes. A segunda é fazer todo cálculo em código e ligar cada número da resposta à consulta que o gerou, recusando número sem origem. A terceira é um segundo modelo avaliando o que não dá para conferir em código, com uma rubrica escrita e calibrada contra avaliações humanas. A quarta é a aprovação humana, reservada para onde o erro é caro e irreversível, com a evidência e as diferenças na tela de quem aprova.

<figure>
<a href="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/06-cadeia-1456.webp"><img src="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/06-cadeia-1456.webp" srcset="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/06-cadeia-728.webp 728w, /blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/06-cadeia-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="816" alt="Animação de uma cadeia de controles. A resposta A atravessa os portões de formato validado, número com origem, juiz com rubrica e aprovação humana e é entregue com evidência. A resposta B é barrada no segundo portão por ter número sem origem. Por baixo, a avaliação contínua roda doze casos reais com o modelo atual e depois com o modelo novo, um caso regride e a troca espera." loading="lazy" decoding="async"></a>
<a class="baixar" href="/blog/img/mcp-ou-aplicacao-dados-da-empresa-na-ia/06-cadeia.gif" download title="Baixar a animação em GIF">↓ Baixar</a>
<figcaption><em>Fig. 6. Quatro portões na saída de uma aplicação. A resposta B, com um número sem origem, para no segundo. Por baixo, os mesmos casos reais rodam a cada troca de modelo.</em></figcaption>
</figure>

Por baixo das duas pontas fica a avaliação contínua: um conjunto de casos reais com a resposta esperada, rodado a cada troca de modelo ou de prompt e a cada fonte nova. Considero esse conjunto o ativo mais valioso de qualquer processo com IA, acima do prompt e do modelo, porque ele sobrevive a todas as trocas. Sem ele, a próxima versão do fornecedor muda o processo da empresa sem que ninguém perceba, e a aplicação passa a espalhar o erro com a mesma eficiência com que espalhava o acerto. As correções aceitas, como as das avaliações de ligações, são a melhor matéria-prima para esse conjunto.

## 06. Em que ordem recomendo investir?

A fundação de dados vem primeiro, porque nenhum dos dois caminhos funciona sem ela. Em março deste ano descrevi [como consolidamos fontes fragmentadas numa plataforma pronta para IA](/blog/arquitetura-consolidacao-dados-ia-sem-reescrever-sistemas-legados/) sem reescrever nenhum sistema legado. Essa camada única, com identidade por usuário e registro de cada consulta, permite abrir o acesso com segurança e construir aplicações sobre o mesmo dado.

Depois vem o caminho do MCP, aberto cedo e com permissão individual, com o registro das consultas lido como backlog de produto. Medir o que as pessoas perguntam custa quase nada.

Um processo sobe para aplicação quando a resposta passa a ser usada por alguém que não fez a pergunta, ou quando o erro é caro ou difícil de desfazer. Antes de construir, recomendo montar o conjunto de casos reais que vai dizer se a aplicação acerta e nomear a pessoa que responde pelo processo e pelos critérios de aceite.

Esse conjunto continua rodando depois da entrega, a cada troca de modelo, porque é ele que avisa quando o fornecedor mudou o processo da empresa sem pedir licença.

Na sua empresa, quantas das respostas que hoje saem de um chat já viraram entrega para alguém que não tem como conferi-las?

## Perguntas frequentes sobre MCP aberto, RAG e aplicações de IA

### RAG e MCP são concorrentes?

Eles resolvem partes diferentes do problema. RAG é a técnica de buscar os trechos relevantes de uma coleção de documentos e colocá-los no contexto do modelo. O MCP é o protocolo que padroniza como o assistente chega a ferramentas e dados. Uma busca RAG pode ser exposta como ferramenta num servidor MCP, e uma aplicação de processo pode usar RAG por dentro. A decisão deste texto, sobre quem garante a resposta, vale para os dois. Para escolher entre RAG e consulta direta ao banco, comparei os dois em [RAG, contexto longo ou Text-to-SQL](/blog/rag-contexto-longo-ou-text-to-sql-teste-80-contratos/).

### Quando vale começar pelo MCP aberto antes de construir uma aplicação?

Quando o processo ainda não está claro. Codificar um fluxo que ninguém entende congela um fluxo ruim, e o MCP aberto, com permissão por identidade e registro de cada consulta, mostra quais perguntas voltam toda semana sobre as mesmas tabelas e com os mesmos filtros. Essas consultas recorrentes são os processos que já existem na prática e ainda não têm dono.

### Como impedir que o assistente acesse dados fora do perfil de quem pergunta?

A identidade de quem pergunta precisa chegar até o dado, e a permissão precisa ser aplicada no servidor, antes da consulta. Um servidor MCP que consulta tudo com uma conta técnica de acesso amplo entrega a cada usuário os dados de todos, e entrega o mesmo acesso a quem conseguir pôr uma instrução num texto que o modelo vá ler. Por isso documentos, emails, chamados e páginas da web que entram no contexto devem ser tratados como conteúdo não confiável.

### Qual é o sinal de que um processo deve sair do MCP aberto e virar aplicação?

O processo sobe para aplicação quando a resposta passa a ser usada por alguém que não fez a pergunta, ou quando o erro é caro ou difícil de desfazer. Antes de construir, monte o conjunto de casos reais que vai dizer se a aplicação acerta e nomeie a pessoa que responde pelo processo e pelos critérios de aceite.
