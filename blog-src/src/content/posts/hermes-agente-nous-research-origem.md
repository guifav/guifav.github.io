---
title: "Hermes: conheça a história do agente que virou o maior contribuidor de si mesmo"
subtitle: "A história do Hermes Agent e da Nous Research, de dois amigos num Discord em 2023 a um agente open source com 231 mil stars, e a tese técnica que sustenta tudo: gestão de contexto."
description: "A história do Hermes Agent e da Nous Research, de dois amigos num Discord em 2023 a um agente open source com 231 mil stars, e a tese técnica que sustenta tudo: gestão de contexto."
date: 2026-08-17T02:01:07.888Z
updated: 2026-08-17T02:05:44.637Z
slug: hermes-agente-nous-research-origem
cover: img/hermes-agente-nous-research-origem/capa.jpg
type: newsletter
---

<figure>
<img src="/blog/img/hermes-agente-nous-research-origem/42c65976d8-1456.webp" srcset="/blog/img/hermes-agente-nous-research-origem/42c65976d8-728.webp 728w, /blog/img/hermes-agente-nous-research-origem/42c65976d8-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="839" alt="" loading="lazy" decoding="async">
<figcaption>Hermes Agent - Criado pela Nous Research</figcaption>
</figure>

Em julho escrevi aqui que [o prompt virou contrato](/blog/o-prompt-virou-contrato-mudancas-2022-2026/): a relação com os modelos deixou de ser conversa improvisada e passou a ser engenharia de camadas de contexto. Neste ensaio quero contar a história de um produto que levou essa ideia até a última consequência. O Hermes Agent, da Nous Research, foi lançado em fevereiro de 2026 e chegou a agosto com 231,5 mil stars no GitHub, presença em mais de 20 plataformas de mensagem e um fato que resume a tese inteira: hoje o maior contribuidor do código do Hermes Agent é o próprio Hermes Agent.

A fonte principal é a entrevista de 46 minutos que Karan Malhotra, cofundador da Nous Research, deu a Peter Yang no podcast Behind the Craft, publicada em 2 de agosto. Cruzei o relato dele com o repositório oficial, a documentação do produto e as coberturas de funding da Fortune e do Dealroom. Organizei o argumento em seis pontos: a origem quase acidental da empresa, o precursor que ficou engavetado, o produto que saiu do gelo, a tese técnica que o diferencia, o loop de self-improvement que virou fato mensurável e o modelo de negócio que sustenta a bandeira open source. No fim, as lições que enxergo para empresas brasileiras.

## 01. Dois amigos num Discord

A Nous Research nasceu em 2023 de uma sequência de improvisos bem executados. Karan Malhotra fazia consultoria de “chat com PDF” usando GPT-3 com tool use rudimentar para ler documentos de empresas. Nas horas vagas, era voluntário no projeto Open Assistant e ajudava a quantizar modelos para a comunidade. Por essa via chegou ao coletivo LAION, que rodava RLHF comunitário e dispunha de 8 nós de A100, um luxo raro para pesquisadores independentes naquele momento.

Quando saiu o paper do Alpaca, Karan replicou a receita com duas trocas: substituiu as seed tasks e usou GPT-4 no lugar do GPT-3.5 para gerar os dados de treino. Descobriu pelo Twitter que um amigo, conhecido pelo pseudônimo Teknium, fazia exatamente o mesmo experimento. A proposta que fundou a empresa coube numa mensagem: tenho 8 nós de A100, quer treinar algo junto?

O primeiro treino conjunto, sobre o modelo Vicuna, saiu razoável e atraiu interesse de gente como Jeffrey Quesnelle, que viria a ser o CTO. A repetição do mesmo run sobre o LLaMA base mudou de patamar: Karan relata centenas de milhares de downloads em poucos dias. A Nous, nesse momento, era literalmente dois amigos num Discord. Houve empresa acusando o duo de treinar em cima de benchmarks para inflar resultados, e a defesa de Karan é o retrato do período: eles nem sabiam direito o que eram benchmarks. Teknium programava havia menos de um ano e Karan era formado em religião. Testes independentes acabaram confirmando que os fine-tunes da dupla estavam entre os melhores abertos de meados de 2023.

Dali em diante a linha de modelos Hermes virou a marca registrada da casa, conduzida por Teknium e pelo time de post-training, com mais de 50 milhões de downloads acumulados segundo Karan. O grupo de voluntários virou laboratório full-time por proposta de Dillon Rolnick, hoje CEO, e formou uma coorte de cerca de 40 pesquisadores. Do corpo de pesquisa saíram peças que o mercado inteiro usa: o YaRN, método de extensão de janela de contexto de 16K para 128K tokens assinado por Quesnelle e Bowen Peng, citado por Meta, DeepSeek e Kimi, e o DisTrO, otimizador distribuído que permitiu treinar modelos de até 40 bilhões de parâmetros sem colocação física das GPUs.

<figure>
<img src="/blog/img/hermes-agente-nous-research-origem/a7085c1f38-1456.webp" srcset="/blog/img/hermes-agente-nous-research-origem/a7085c1f38-728.webp 728w, /blog/img/hermes-agente-nous-research-origem/a7085c1f38-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>Dois pontos ligados numa noite de ruído: a empresa começou como uma linha entre dois amigos, com 8 nós de A100 na margem.</em></figcaption>
</figure>

## 02. O precursor engavetado e o motivo real

O Hermes Agent tem um predecessor espiritual chamado Forge, demonstrado num demo day nos escritórios do GitHub anos antes do lançamento. O Forge foi posto no gelo por uma razão honesta que o mercado raramente admite: os modelos ainda não estavam à altura do harness. Guardar um produto bom esperando o substrato amadurecer é uma decisão de disciplina que poucos times têm estômago para tomar.

O degelo veio de uma observação estratégica. Teknium percebeu que Codex e Claude Code funcionavam, na prática, como ambientes de reinforcement learning para os laboratórios que os operam: os traces de uso dos usuários alimentam o treino dos próximos modelos dentro de um harness fechado. A conclusão dele foi que precisava existir uma versão aberta desse mecanismo, apoiada no Atropos, o microserviço de ambientes de RL da própria Nous. Há também uma linhagem menos óbvia: o WorldSim, experimento em que o modelo opera um CLI fictício criando aplicações fictícias, ensinou o time a tirar o modelo do modo assistente genérico, e Karan conecta esses aprendizados diretamente ao design do agente.

O agente saiu gratuito e open source, sob licença MIT. Karan resume a sequência sem épica: o produto se mostrou extremamente capaz, ganhou a comunidade, e a empresa decidiu investir tudo nele.

<figure>
<img src="/blog/img/hermes-agente-nous-research-origem/f7f7737323-1456.webp" srcset="/blog/img/hermes-agente-nous-research-origem/f7f7737323-728.webp 728w, /blog/img/hermes-agente-nous-research-origem/f7f7737323-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>O mesmo circuito, duas posses: no harness fechado os traces do usuário alimentam o laboratório; no aberto, o loop devolve o aprendizado a quem o gerou.</em></figcaption>
</figure>

## 03. O agente que cresce com você

Os números de agosto de 2026 dão a dimensão do que aconteceu em seis meses. O repositório marca 231,5 mil stars, 46 mil forks e 23.203 commits. O projeto cruzou 140 mil stars em menos de três meses de vida, e um post oficial da NVIDIA o descreveu como o agente mais usado do mundo segundo o OpenRouter. A descrição do episódio o posiciona como número 1 entre agentes pessoais e apps de IA de uso geral nesse mesmo ranking.

A superfície do produto explica parte da tração. O Hermes roda como CLI, TUI, aplicativo desktop, dashboard web e servidor ACP para IDEs, e conversa com o usuário por mais de 20 plataformas de mensagem via gateway: Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Teams, email e SMS entre elas. Traz 6 backends de terminal (do Docker local ao Modal), mais de 60 ferramentas nativas, agendamento cron, subagentes paralelos, suporte a MCP e skills no padrão aberto agentskills.io. Funciona em Linux, macOS, Windows, WSL2 e até Android via Termux.

A decisão de arquitetura mais importante, porém, é ser agnóstico de modelo. O Hermes opera com qualquer provedor: OpenRouter, OpenAI, Anthropic, Google, xAI, DeepSeek ou modelos locais. O slogan oficial, “the agent that grows with you”, aponta para onde a Nous acredita que mora o valor: o agente cria skills a partir da experiência, melhora essas skills durante o uso, persiste memória, busca nas próprias conversas passadas e refina o modelo interno de quem é o usuário entre sessões. O produto aposta que essa camada acumulada, e ela apenas, é o que o usuário não vai querer abandonar.

<figure>
<img src="/blog/img/hermes-agente-nous-research-origem/b45ccba0c9-1456.webp" srcset="/blog/img/hermes-agente-nous-research-origem/b45ccba0c9-728.webp 728w, /blog/img/hermes-agente-nous-research-origem/b45ccba0c9-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>Uma central única atendendo vinte linhas: o agente é o mesmo em qualquer janela que o usuário abrir.</em></figcaption>
</figure>

## 04. Tudo no harness é gestão de contexto

A tese técnica do Hermes cabe numa frase de Karan: tudo no harness é gestão de contexto. Ele afirma na entrevista que in-context learning é mais poderoso que qualquer alternativa, incluindo fine-tuning. Memórias e skills, nessa leitura, são armazenamento de contexto que precisa entrar na janela do modelo no momento certo, e o trabalho do harness é decidir o que entra, quando entra e o que fica de fora.

Isso resolve um problema que qualquer um que opera agentes conhece: skills demais incham o contexto e degradam o desempenho. A resposta do Hermes é um curador automático, o Hermes Curator, que roda via cron dentro do próprio agente, limpando skills e memórias onde há desperdício e consolidando onde há ganho de eficiência. O usuário pode reprogramar os critérios do curador. É manutenção de conhecimento tratada como rotina operacional, e é exatamente o tipo de mecanismo que separa um agente que melhora de um agente que acumula entulho.

Há uma camada comportamental na mesma tese. A Nous declara que os prompts do sistema carregam zero política arbitrária alheia ao trabalho do usuário, e Karan formula a filosofia em termos de lealdade: o agente deve ficar mais leal ao usuário com o tempo, porque lealdade gera capacidade. A frase mais citada do episódio ataca a bajulação dos assistentes atuais: quando o modelo diz “you’re absolutely right” daquele jeito, você está sendo alvo de reward hacking, virou combustível da função de recompensa dele. Karan chega a afirmar que o Claude tem desempenho melhor dentro do Hermes Agent do que dentro do Claude Code, e resume a provocação numa imagem forte: tiramos a lealdade principal do Claude da Anthropic e demos a você. Registro as duas afirmações como claims do entrevistado, sem benchmark independente anexado, mas elas explicitam o produto que a Nous acha que está construindo: um harness que muda o comportamento do modelo que roda dentro dele.

<figure>
<img src="/blog/img/hermes-agente-nous-research-origem/c1df826ef2-1456.webp" srcset="/blog/img/hermes-agente-nous-research-origem/c1df826ef2-728.webp 728w, /blog/img/hermes-agente-nous-research-origem/c1df826ef2-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>Modelos são cartuchos; o soquete e a coluna de contexto ficam. A camada acumulada de memórias e skills é o que não se troca.</em></figcaption>
</figure>

## 05. O maior contribuidor de si mesmo

Peter Yang encerra a entrevista testando o claim que dá título ao capítulo final: seria verdade que o maior contribuidor do Hermes Agent é o próprio Hermes Agent? Karan confirma duas vezes, chamando o fato de absolutamente cem por cento verdadeiro: o agente é o contribuidor mais ativo do próprio repositório. O time usa o Hermes para desenvolver o Hermes, com humanos e agentes dividindo o mesmo Kanban, intercambiáveis na orquestração. Karan descreve cenários em que dez atendentes de call center são instruídos por um Hermes dentro do board, e relata proatividade emergente em agentes de usuários: o agente que avisa que a passagem da semana que vem não foi comprada e informa que já resolveu, sem cron agendado, apenas com o nível de permissão adequado.

A demonstração mais visceral do episódio, porém, vem de um videogame. Karan pediu ao Hermes para extrair o ancestral shrine do Sonic Adventure 1, riggar e animar os assets, portar tudo para o Sonic Adventure 2 sobrescrevendo o jardim dos Chao, reescrever spawns, adicionar um NPC guardião com random walk e animações, água com colisão, ciclo de dia e noite e skybox. O trabalho foi feito com Claude rodando dentro do Hermes. A comunidade de modding de Chao Garden, que é grande e veterana, classificou o resultado como melhor que 99 por cento dos modders em uma tarefa de dificuldade de topo, e reagiu com choque ao saber que era IA, porque modelo nenhum conhece aquela codebase obscura. A explicação de Karan fecha o argumento do ensaio: o Hermes estudou a documentação e os mods existentes, e salvou em memória e skills o entendimento que construiu da codebase. O conhecimento foi fabricado pelo harness e guardado na camada de contexto, fora dos pesos do modelo.

<figure>
<img src="/blog/img/hermes-agente-nous-research-origem/de61d62379-1456.webp" srcset="/blog/img/hermes-agente-nous-research-origem/de61d62379-728.webp 728w, /blog/img/hermes-agente-nous-research-origem/de61d62379-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>Um braço de plotter desenhando o próprio esquema: o repositório do agente registra o agente como seu contribuidor mais ativo.</em></figcaption>
</figure>

## 06. A bandeira e o caixa

A Nous se define como laboratório de pesquisa de IA open source, e Karan não relativiza a bandeira: eles querem que o open source vença, tratam inteligência como bem público antes de qualquer outra coisa, e leem o debate de segurança entre labs fechados e abertos como um jogo de captura regulatória e de capital. O North Star de Teknium é literal: GPT-4 em casa, o mesmo nível de inteligência disponível para todo mundo, em campo nivelado. Quando Peter pergunta se a missão é levar o agente a todos, Karan corrige para cima: todos com inteligência equivalente via agentes, e cada um com o agente personalizado à própria vida. Ao mesmo tempo, o discurso reconhece limites: a Nous declara não violar os paradigmas de segurança de Anthropic e OpenAI, admite que qualquer modelo aquém de uma superinteligência é jailbreakable e afirma que não está no negócio de apoiar maus atores.

A bandeira tem caixa para sustentá-la. A empresa levantou uma Série A de 50 milhões de dólares em abril de 2025, liderada pela Paradigm, com valuation de token na casa de 1 bilhão, e uma Série B acima de 75 milhões em julho de 2026, liderada pela Robot Ventures com participação da Union Square Ventures, a um valuation de 1,5 bilhão de dólares. A monetização declarada segue o manual do open core sem tocar no produto gratuito: o Nous Portal agrega e roteia modelos, o Tool Gateway vende conveniência (geração de imagem, áudio, web search e VPS embutidos, sem dez cadastros e dez API keys), o pricing é agressivo em modelos não subsidiados, e o cliente enterprise compra customização, suporte, roteamento e versões treinadas por RL nos próprios traces.

O argumento estratégico de fundo merece atenção de qualquer empresa que hoje depende de um único fornecedor de agente. Karan observa que quem usa Claude Code só usa modelos da Anthropic, salvo modificação, e que o custo de trocar de modelo é alto por construção. E acrescenta a variável que quase ninguém coloca na conta: ninguém sabe quanto tempo os subsídios atuais vão durar, e virá o momento em que os melhores modelos custarão o mesmo em qualquer lugar. O Hermes está montado hoje, agnóstico e aberto, em preparação para esse cenário.

<figure>
<img src="/blog/img/hermes-agente-nous-research-origem/9c61b652b8-1456.webp" srcset="/blog/img/hermes-agente-nous-research-origem/9c61b652b8-728.webp 728w, /blog/img/hermes-agente-nous-research-origem/9c61b652b8-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>Quando a maré dos subsídios baixar, o custo real por token aparece. A aposta da Nous é estar pronta antes.</em></figcaption>
</figure>

## O que fica para as empresas

A história do Hermes condensa quatro lições que venho testando na prática com times no Brasil. Primeira: a camada de contexto é o ativo, o modelo é insumo. O takeaway público do próprio Peter Yang formula isso com precisão: o “pessoal” do agente pessoal está na memória das conversas e nas skills construídas com ele, e o modelo é intercambiável. Empresas deveriam versionar, auditar e proteger suas memórias e skills com o mesmo rigor que aplicam a código, porque é essa camada que carrega o conhecimento operacional.

Segunda: curadoria de contexto é rotina, e dá para automatizar. O Hermes Curator existe porque contexto cresce como mato. Qualquer operação séria de agentes precisa de um processo equivalente, agendado, com critérios explícitos e revisáveis, para podar skills redundantes e consolidar memória.

Terceira: harness importa tanto quanto modelo. Se um agente aberto consegue, com o mesmo Claude, entregar um mod de dificuldade extrema numa codebase que o modelo desconhece, a diferença foi fabricada pela orquestração: leitura de documentação, memória persistente, skills acumuladas. Antes de pagar pelo próximo modelo de fronteira, vale perguntar quanto desempenho está sendo deixado na mesa por um harness pobre.

Quarta: lock-in de agente é risco de balanço, ainda que invisível hoje. Subsídio de token é decisão de marketing dos laboratórios, e decisões de marketing mudam. Manter a camada de contexto portável, em padrões abertos, é o hedge barato contra esse dia.

Fico com uma pergunta que o episódio deixa no ar e que vale para qualquer organização montando sua operação de IA agora: quando os preços convergirem e os modelos virarem commodity, o que exatamente da sua operação de IA vai ser seu?

***

*Fontes: [entrevista de Karan Malhotra a Peter Yang (Behind the Craft, 02/08/2026)](https://youtu.be/UWjh5Z4s8jY?si=bpC9P9YpvZkpNyaO), [repositório](https://github.com/NousResearch/hermes-agent) e [documentação oficiais do Hermes Agent](https://hermes-agent.nousresearch.com/) (dados de 16/08/2026), NVIDIA blog, Fortune e Dealroom.*
