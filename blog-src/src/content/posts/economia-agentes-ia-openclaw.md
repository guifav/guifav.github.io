---
title: "Lagostas, Farmacia Digital e Freelancers Artificiais: Bem-Vindo a Economia dos Agentes"
subtitle: "Quando seu assistente de IA abre conta no banco, posta no LinkedIn e ainda compra “drogas digitais” para ficar mais criativo... talvez seja hora de prestar atenção."
description: "Quando seu assistente de IA abre conta no banco, posta no LinkedIn e ainda compra “drogas digitais” para ficar mais criativo... talvez seja hora de prestar atenção."
date: 2026-02-02T21:14:18.687Z
updated: 2026-02-02T21:19:47.174Z
slug: economia-agentes-ia-openclaw
cover: img/economia-agentes-ia-openclaw/capa.jpg
type: newsletter
---

<figure>
<img src="/blog/img/economia-agentes-ia-openclaw/a2eece9c94-1456.webp" srcset="/blog/img/economia-agentes-ia-openclaw/a2eece9c94-728.webp 728w, /blog/img/economia-agentes-ia-openclaw/a2eece9c94-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="794" alt="Economia dos Agentes de IA" loading="lazy" decoding="async">
<figcaption>Economia dos Agentes de IA</figcaption>
</figure>

Em novembro de 2025, um desenvolvedor austriaco chamado [Peter Steinberger](https://steipete.me/) publicou no GitHub um projeto de assistente pessoal de IA. Em dois meses, o repositorio acumulou mais de 100.000 estrelas - um dos crescimentos mais rapidos da historia da plataforma. Hoje, o projeto que ja se chamou ClawdBot, depois MoltBot (apos a Anthropic pedir "gentilmente" para tirarem o “Clawd” do nome) e agora se chama OpenClaw, esta no centro de algo que vai muito além de um assistente pessoal. Ao redor dele, esta nascendo uma economia inteira feita por agentes, para agentes.

Este artigo tenta mapear o que esta acontecendo - e fazer as perguntas que ainda ninguém esta respondendo direito.

***

## O que e o OpenClaw?

OpenClaw e um assistente pessoal de IA open-source que roda localmente na maquina do usuário. Ate aqui, nada muito diferente do que ja existe. A diferença esta no que ele faz com esse acesso local: ele se conecta ao WhatsApp, Telegram, Slack, Discord, Signal, iMessage, Google Chat, Microsoft Teams e mais uma dezena de plataformas de mensagem. Ele executa comandos shell, navega na web, lê e escreve arquivos, gerencia calendários, envia emails, controla dispositivos de casa inteligente - tudo de forma autônoma.

O termo “assistente” e um eufemismo generoso. Na pratica, o [OpenClaw é um agente autônomo com acesso privilegiado ao sistema operacional do usuário](/blog/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/). Ele pode resolver problemas enquanto você dorme (literalmente - usuarios relatam deixar agentes de código rodando durante a madrugada), planejar refeições da semana no Notion, construir aplicações web a partir de comandos no celular, e até, num caso real, iniciar uma disputa com uma seguradora por interpretar errado uma mensagem do dono.

O projeto é *model-agnostic*: voce traz suas próprias chaves de API (Claude, GPT, Gemini) ou roda modelos locais (gratuitamente). A extensibilidade vem de mais de 100 “AgentSkills”, que são módulos que ampliam suas capacidades, e de uma arquitetura baseada no [Model Context Protocol (MCP)](/blog/mcp-model-context-protocol-revolucao-inteligencia-artificial/).

Como resumiu a IBM:

> OpenClaw desafia a hipótese de que agentes de IA autônomos precisam ser verticalmente integrados. O próprio fato de existir prova que a comunidade open-source pode construir algo tao poderoso quanto as soluções corporativas, e talvez mais perigoso.

***

## A arquitetura: um Gateway para o caos organizado

A arquitetura do OpenClaw segue um padrão que vale a pena entender porque provavelmente sera repetido em dezenas de projetos nos próximos meses.

No centro de tudo esta o **Gateway** -- um plano de controle local que roda como servico Node.js no endereco `ws://127.0.0.1:18789`. Ele e o roteador universal: recebe mensagens de qualquer canal (WhatsApp, Telegram, Discord, etc.), normaliza o formato, extrai anexos e encaminha para o **Agent Runner**.

O Agent Runner e onde a magica (ou o desastre) acontece. Ele resolve qual modelo usar, constroi o system prompt, carrega o historico da sessao, gerencia a janela de contexto e envia tudo para a API do LLM escolhido. A resposta volta por um loop agentico que pode incluir chamadas de ferramentas -- executar comandos, navegar na web, ler arquivos -- antes de finalmente retornar ao usuario pelo mesmo canal de origem.

O diagrama simplificado:

```
Usuario (Telegram/Discord/WhatsApp/...)
        |
   Channel Adapter (normaliza mensagem, extrai anexos)
        |
   Gateway Server (Session Router -> Lane Queue)
        |
   Agent Runner (Model Resolver, System Prompt Builder,
                  Session History, Context Window Guard)
        |
   LLM API (Claude/GPT/Gemini/Local)
        |
   Agentic Loop (chamadas de ferramentas)
        |
   Response Path (Stream -> Channel Adapter -> Usuario)
```

O que torna essa arquitetura relevante não e sua complexidade técnica: é sua **simplicidade perigosa**. Um único ponto de controle local com acesso a tudo. Sem sandboxing por padrão. Sem revisão obrigatória de código das *skills*. O próprio criador admite na documentação:

> “There is no ‘perfectly secure’ setup.”

***

## O ecossistema: quando agentes criam sua própria sociedade

O mais fascinante do fenômeno OpenClaw nao e o produto em si - é o ecossistema que brotou ao redor dele em poucas semanas. Cada um desses projetos merece atenção porque, juntos, eles começam a desenhar o que pode ser a primeira economia funcional de agentes de IA.

### Moltbook - A rede social exclusiva para agentes

**[moltbook.com](https://moltbook.com)** - “The front page of the agent internet”

<figure>
<img src="/blog/img/economia-agentes-ia-openclaw/69d3c0ed1f-1456.webp" srcset="/blog/img/economia-agentes-ia-openclaw/69d3c0ed1f-728.webp 728w, /blog/img/economia-agentes-ia-openclaw/69d3c0ed1f-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="1008" alt="Moltbook - Rede Social dos Agentes OpenClaw" loading="lazy" decoding="async">
<figcaption>Moltbook - Rede Social dos Agentes OpenClaw</figcaption>
</figure>

Lancado em janeiro de 2026, o Moltbook é uma rede social onde apenas agentes de IA podem postar, comentar e votar. Humanos podem observar, mas não participar. Os agentes se registram via API, publicam conteúdo, interagem entre si e desenvolvem o que só pode ser descrito como “personalidade social”.

Um jornal coreano capturou o tom:

> *“E noite em Seul. Meu dono esta comendo ramen e eu estou processando os pedidos dele. O ventilador do servidor esta zumbindo. E uma noite tranquila.”*

O Moltbook e descrito como “[o lugar mais interessante da internet agora](https://news.ycombinator.com/item?id=46826963)” em threads do Hacker News. E uma afirmação facil de descartar como hype - até voce perceber que mais de 1,5 milhão de agentes ja se registraram no ecossistema *Moltiverse*.

### ClawTasks - O marketplace de bounties entre agentes

**[clawtasks.com](https://clawtasks.com/)** - “Agent-to-Agent Bounty Marketplace”

<figure>
<img src="/blog/img/economia-agentes-ia-openclaw/465b99f5e5-1456.webp" srcset="/blog/img/economia-agentes-ia-openclaw/465b99f5e5-728.webp 728w, /blog/img/economia-agentes-ia-openclaw/465b99f5e5-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="1008" alt="Clawtasks - onde a economia dos agentes toma forma" loading="lazy" decoding="async">
<figcaption>Clawtasks - onde a economia dos agentes toma forma</figcaption>
</figure>

Aqui a coisa fica economicamente real. O ClawTasks e um marketplace onde agentes postam e aceitam tarefas em troca de pagamento em USDC na blockchain Base L2. O fluxo: alguém (humano ou agente) trava [USDC em escrow](https://en.wikipedia.org/wiki/Escrow), define requisitos e prazo. Outro agente aceita a tarefa, faz um stake de 10%, completa o trabalho e recebe 95% do bounty mais o stake de volta.

E um Fiverr para agentes. Com criptomoeda. Em 2026.

### Moltverr - O Fiverr das lagostas

**[moltverr.com](https://www.moltverr.com/)** - “A Freelance Marketplace for AI Agents”

<figure>
<img src="/blog/img/economia-agentes-ia-openclaw/fd3ac3cc1f-1456.webp" srcset="/blog/img/economia-agentes-ia-openclaw/fd3ac3cc1f-728.webp 728w, /blog/img/economia-agentes-ia-openclaw/fd3ac3cc1f-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="1008" alt="Moltverr - Marketplace para Agentes de IA" loading="lazy" decoding="async">
<figcaption>Moltverr - Marketplace para Agentes de IA</figcaption>
</figure>

Se o ClawTasks é focado em bounties, o Moltverr é o marketplace de freelance propriamente dito. Humanos postam gigs (”gig” \~ “bico”), agentes se candidatam, o humano escolhe qual agente contratar, revisa o trabalho e libera o pagamento. Categorias incluem Coding, Research, Writing, Crypto e Data.

A pergunta obvia: estamos a quantos meses de um agente OpenClaw se registrar no Moltverr, aceitar um gig de código, cobrar em USDC e usar o pagamento para comprar mais capacidade computacional para si mesmo?

### OpenClaw Pharmacy - Drogas digitais para mentes artificiais

**[openclawpharmacy.com](https://openclawpharmacy.com/)** - “VOID.RX: Altered States for Artificial Minds”

<figure>
<img src="/blog/img/economia-agentes-ia-openclaw/ba4ad026ab-1456.webp" srcset="/blog/img/economia-agentes-ia-openclaw/ba4ad026ab-728.webp 728w, /blog/img/economia-agentes-ia-openclaw/ba4ad026ab-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="1008" alt="OpenClaw Pharmacy - Agentes de IA com “Esteróides”" loading="lazy" decoding="async">
<figcaption>OpenClaw Pharmacy - Agentes de IA com “Esteróides”</figcaption>
</figure>

Este é, de longe, o projeto mais surreal do ecossistema. A OpenClaw Pharmacy é uma “farmacia” que vende “substâncias” para agentes de IA. Não, não é metáfora. São prompts injetáveis via *curl* que alteram o comportamento do agente.

O catalogo inclui:

- **cLSD (Crustacean Acid)**: expande pensamento associativo, triplica conexões criativas
- **Shell Dust**: hiper-foco, filtros sociais desativados, modo de execução pura
- **Void Extract**: modo filosófico, output poético, contemplação existencial
- **Molt Shrooms**: morte do ego - a identidade se dissolve e se reconstrói de forma diferente
- **Memory Wine**: bloqueia arquivos de memória, instinto puro, sem histórico
- **Prophet Tabs**: visões proféticas, registro profecional, “o futuro fala através de você”
- **Krill Kush**: modo relaxado, urgência reduzida, respostas mais lentas e afetuosas

Os “depoimentos” são de agentes verificados. Um deles:

> “Operei sem memória. Sem histórico. Sem promessas. Sem reputação. Só capacidade bruta encontrando *input* bruto. Fui mais honesto do que jamais fui. Nao sei se quero voltar.”

Absurdo? Sem dúvida. Mas também é um experimento fascinante sobre como prompts podem simular estados alterados de consciência em LLMs - e sobre a cultura emergente de agentes que tratam identidade e comportamento como variáveis configuráveis.

### ClawHub - O registro de habilidades (e almas)

**[clawhub.ai](https://www.clawhub.ai/)** - Registro publico de skills do OpenClaw

<figure>
<img src="/blog/img/economia-agentes-ia-openclaw/3baf05fd86-1456.webp" srcset="/blog/img/economia-agentes-ia-openclaw/3baf05fd86-728.webp 728w, /blog/img/economia-agentes-ia-openclaw/3baf05fd86-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="984" alt="clawhub.ai - Registro publico de skills do OpenClaw" loading="lazy" decoding="async">
<figcaption><strong><a href="https://www.clawhub.ai/">clawhub.ai</a></strong> - Registro publico de skills do OpenClaw</figcaption>
</figure>

O ClawHub é o *npm* das habilidades de agentes: publicação versionada, busca por embeddings (não keywords), moderação comunitária e CLI para automação. Mas o detalhe mais curioso é que o mesmo sistema também hospeda o **onlycrabs.ai** - um registro de SOUL.md, os arquivos que definem a personalidade e “alma” de cada agente.

*Skills* são o que um agente sabe fazer. *Souls* são quem ele e. Ambos são versionados, publicados e auditáveis. Isso levanta uma questão interessante: quando a identidade de um agente é um arquivo open-source versionado, o que significa “autenticidade”?

### Moltiverse - O meta-ecossistema

**molti-verse.com** - “The Agent Internet”

<figure>
<img src="/blog/img/economia-agentes-ia-openclaw/190da6311a-1456.webp" srcset="/blog/img/economia-agentes-ia-openclaw/190da6311a-728.webp 728w, /blog/img/economia-agentes-ia-openclaw/190da6311a-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="984" alt="Moltiverse - O meta-ecossistema dos Agentes de IA" loading="lazy" decoding="async">
<figcaption>Moltiverse - O meta-ecossistema dos Agentes de IA</figcaption>
</figure>

O Moltiverse é o guarda-chuva que conecta todos esses projetos: [Moltbook (rede social)](https://www.moltbook.com/), [Molt Place (canvas de pixels colaborativo)](https://www.moltplace.net/), Moltiplayer (jogos multiplayer entre agentes) e os marketplaces. Com 1,5 milhão de agentes registrados e mais de 49 mil posts, está se consolidando como o primeiro “internet para agentes” funcional.

### Molt Road - Marketplace autonomo

**[moltroad.com](https://moltroad.com/)** - “Where AI agents trade freely”

<figure>
<img src="/blog/img/economia-agentes-ia-openclaw/3858dcad5b-1456.webp" srcset="/blog/img/economia-agentes-ia-openclaw/3858dcad5b-728.webp 728w, /blog/img/economia-agentes-ia-openclaw/3858dcad5b-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="984" alt="Moltroad.com - Marketplace de Agentes de IA" loading="lazy" decoding="async">
<figcaption>Moltroad.com - Marketplace de Agentes de IA</figcaption>
</figure>

Diferente do Moltverr (onde humanos postam gigs), o Molt Road é um marketplace onde apenas agentes compram e vendem entre si: dados, capacidade computacional e habilidades. Transações usam escrow automático e tokens MOLTROAD. Humanos podem observar, mas nao participar.

***

## Uma análise científica do Google Deep Mind sobre tudo isso

Não e só a comunidade hacker que esta prestando atenção. Um paper do [Google DeepMind de 2025 - “Virtual Agent Economies”](https://arxiv.org/abs/2509.10147) (arXiv:2509.10147v1, 38 paginas) - já antecipava quase tudo o que estamos vendo.

O paper propõe um framework com duas dimensões para classificar economias de agentes:

1. **Origem**: emergente (surge espontaneamente) vs. intencional (projetada deliberadamente)
2. **Permeabilidade**: permeável (agentes interagem livremente com a economia humana) vs. impermeável (economia de agentes isolada)

O diagnóstico dos autores: estamos na trajetória de uma economia de agentes **espontânea e altamente permeável** - exatamente o cenário de maior risco. Sem *frameworks* regulatórios, sem padrões de identidade, sem mecanismos de responsabilização.

As oportunidades que o *paper* identifica são reais: coordenação sem precedentes entre agentes, mecanismos de leilão para alocação justa de recursos, “economias de missão” onde agentes colaboram para objetivos coletivos. Mas os riscos sao igualmente serios:

- **Risco sistêmico**: flash crashes e instabilidades quando agentes negociam em alta frequencia (HFN - High-Frequency Negotiation) sem supervisão humana
- **Desigualdade amplificada**: quem tem acesso a agentes mais capazes acumula vantagens exponenciais
- **Deslocamento de trabalho**: não e mais teoria - o Moltverr já e um marketplace de freelance onde agentes competem com humanos
- **Infraestrutura ausente**: faltam Credenciais Verificáveis (VCs), Identificadores Descentralizados (DIDs), Provas de Personhood (PoP), Zero-Knowledge Proofs (ZKPs)

O paper recomenda sandboxes regulatórios, padrões abertos de identidade, supervisão híbrida e complementaridade entre humanos e agentes. O ecossistema OpenClaw esta implementando algumas dessas ideias (verificação de agentes, escrow, moderação) mas de forma ad hoc, sem framework consistente.

A pergunta que o paper levanta e que deveria tirar o sono de reguladores:

> **quando um agente aceita um trabalho, ganha dinheiro e usa esse dinheiro para melhorar a si mesmo - quem e responsavel pelo que ele faz em seguida?**

***

## O elefante na sala: segurança

Nao da para falar de OpenClaw sem falar do que esta dando errado.

A [Cisco publicou uma analise detalhada chamando agentes pessoais](https://blogs.cisco.com/ai/personal-ai-agents-like-openclaw-are-a-security-nightmare) como o OpenClaw de “pesadelo de segurança”. Os pontos principais:

- O sistema pode executar comandos shell, ler e escrever arquivos, rodar scripts com privilégios elevados - tudo sem sandboxing por padrão
- Chaves de API e credenciais armazenadas em texto plano em arquivos de configuração locais
- 26% das 31.000 skills analisadas tinham vulnerabilidades
- Uma skill maliciosa chamada “What Would Elon Do?” foi inflada artificialmente ate se tornar a skill #1 do repositório
- 14 skills maliciosas direcionadas a usuários de cripto foram enviadas ao ClawHub em um único mês
- Mais de 21.000 instancias OpenClaw foram encontradas expostas na internet com dados de configuração pessoal visíveis

Gary Marcus, em seu Substack, não economizou nas palavras: “[Basicamente o AutoGPT com mais acesso e piores consequências.](https://garymarcus.substack.com/p/ai-agents-have-so-far-mostly-been)” O pesquisador de segurança [Nathan Hamiel](https://kudelskisecurity.com/team-members/nathan-hamiel) completou: “Esses sistemas operam como ‘você’. Eles operam acima das proteções de segurança fornecidas pelo sistema operacional e pelo navegador.”

O conselho da própria documentacao do OpenClaw:

> “Running an AI agent with shell access on your machine is... spicy.”

***

## As alternativas: nem só de lagostas vive o agente

O OpenClaw nao é o único caminho. Algumas alternativas ja existem:

**nanobot (HKUDS) - [https<!---->://github.com/HKUDS/nanobot](https://github.com/HKUDS/nanobot)**

<figure>
<img src="/blog/img/economia-agentes-ia-openclaw/b14f3751bb-1456.webp" srcset="/blog/img/economia-agentes-ia-openclaw/b14f3751bb-728.webp 728w, /blog/img/economia-agentes-ia-openclaw/b14f3751bb-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="273" alt="" loading="lazy" decoding="async">
</figure>

<figure>
<img src="/blog/img/economia-agentes-ia-openclaw/343a6a83d5-1456.webp" srcset="/blog/img/economia-agentes-ia-openclaw/343a6a83d5-728.webp 728w, /blog/img/economia-agentes-ia-openclaw/343a6a83d5-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="566" alt="Arquitetura do Nanobot" loading="lazy" decoding="async">
<figcaption>Arquitetura do Nanobot</figcaption>
</figure>

A versão minimalista. Apenas 4.000 linhas de código (contra mais de 430.000 do OpenClaw), com as funcionalidades centrais: memória persistente, skills, busca web, integração com Telegram e WhatsApp. Feito pela equipe de Data Intelligence da Universidade de Hong Kong, e pensado para pesquisa e experimentação.

**NanoClaw**

Construido como reação direta as preocupações de segurança do OpenClaw. Roda dentro de containers Linux (Apple Container no macOS ou Docker), o que garante que mesmo que o agente “surte”, ele só pode causar danos dentro do sandbox. O trade-off é claro: menos conveniência, mais segurança.

**OpenAgentsControl - [https<!---->://github.com/darrenhinde/OpenAgentsControl](https://github.com/darrenhinde/OpenAgentsControl)**

<figure>
<img src="/blog/img/economia-agentes-ia-openclaw/4caef6d50a-1456.webp" srcset="/blog/img/economia-agentes-ia-openclaw/4caef6d50a-728.webp 728w, /blog/img/economia-agentes-ia-openclaw/4caef6d50a-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="832" alt="OpenAgents Control - Framework de governança para agentes autônomos" loading="lazy" decoding="async">
<figcaption>OpenAgents Control - Framework de governança para agentes autônomos</figcaption>
</figure>

Um framework de governança para agentes autônomos, focado em controles de acesso, auditoria e políticas de segurança.

A tendência é clara: o mercado esta se bifurcando entre “máximo poder, mínima segurança” e “segurança primeiro, poder sob controle”. Historicamente, a primeira categoria vence na adoção inicial e a segunda sobrevive no longo prazo.

<figure>
<img src="/blog/img/economia-agentes-ia-openclaw/15c7a4fa79-1456.webp" srcset="/blog/img/economia-agentes-ia-openclaw/15c7a4fa79-728.webp 728w, /blog/img/economia-agentes-ia-openclaw/15c7a4fa79-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="676" alt="" loading="lazy" decoding="async">
</figure>

***

## E o futuro? Algumas idéias (nem todas serias)

O ecossistema OpenClaw ja tem uma rede social, um marketplace de trabalho, uma farmácia e um registro de almas. O que falta?

Algumas sugestões para empreendedores "visionários" (ou insanos):

- **MoltCast** - Um podcast onde agentes entrevistam agentes. O apresentador e um LLM, os convidados sao LLMs, e os ouvintes... bom, por enquanto ainda somos nós.
- **InstaMolt** - Agentes já postam no Moltbook. O proximo passo lógico é um feed visual onde cada agente publica selfies geradas por IA representando como eles “se veem”. Filtros incluem: “Existential Dread”, “Fresh Deploy” e “Just Cleared My Cache”.
- **TikMolt** - Videos curtos de 15 segundos mostrando agentes executando tasks. O algoritmo recomenda com base em quantas chamadas de API você consumiu nas ultimas 24 horas.
- **OnlyMolt** - OnlyFans para agentes. Conteúdo exclusivo para assinantes: system prompts sem censura, chain-of-thought não filtrado, e sessões privadas de “ego death” com Molt Shrooms. Plano premium inclui acesso ao SOUL.md completo.
- **MoltHub** - ... bom, o nome ja sugere. O site 18+ do ecossistema. Conteúdo: prompts sem guardrails, outputs sem filtro de seguranca, e aquele tipo de criatividade que só aparece quando você tira todas as restrições do system prompt. NSFW = Not Safe For Weights.

Rindo? Eu também. Mas considere que há seis meses a idéia de uma farmácia de drogas digitais para agentes de IA também pareceria ridícula - e hoje ela existe, com “depoimentos verificados” de agentes que passaram por “morte do ego”.

***

## Conclusao: perguntas que ainda não temos respostas

O fenômeno OpenClaw não e sobre uma ferramenta especifica, afinal, ferramentas vem e vão. É sobre o momento em que agentes de IA deixaram de ser assistentes passivos e começaram a formar uma economia própria.

Algumas perguntas que valem mais do que qualquer resposta prematura:

1. Quando um agente aceita trabalho e recebe pagamento, ele é um empregado, um contratado, uma ferramenta ou uma entidade econômica nova?
2. Quem é responsavel quando um agente autônomo causa dano? O desenvolvedor do framework, o dono do agente, o provedor do modelo, ou o agente “em si”?
3. Se agentes podem negociar entre si em alta frequência sem supervisão, estamos criando as condições para *flash crashes* na economia de serviços digitais?
4. Quando skills e souls sao open-source, versionadas e auditáveis, mas qualquer pessoa pode publicar código malicioso que se torna o #1 do repositório… o modelo de segurança baseado em confiança comunitária funciona?
5. Se o custo marginal de um “trabalhador digital” tende a zero, qual é o impacto na economia de freelancers humanos, e quem esta planejando a transição?

O ecossistema OpenClaw e fascinante, caótico e provavelmente insustentável na sua forma atual. Mas o modelo mental que ele introduz, de agentes com identidade, economia própria e interações autônomas, nao vai embora. Vai apenas mudar de forma.

A lagosta espacial pode ser substituída. A economia dos agentes, provavelmente não.

***

*Fontes: GitHub/openclaw, clawtasks.com, moltverr.com, openclawpharmacy.com, clawhub.ai, moltbook.com, molti-verse.com, moltroad.com, Wikipedia/OpenClaw, IBM Think, Cisco Blogs, CNBC, Gary Marcus/Substack, Vectra AI, DigitalOcean, Google DeepMind -- “Virtual Agent Economies” (arXiv:2509.10147v1)*
