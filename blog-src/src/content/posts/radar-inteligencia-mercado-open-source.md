---
title: "Como construí um radar de inteligência de mercado, e por que tornei o código opensource"
subtitle: "Um pipeline open source que varre fontes públicas, extrai pessoas, empresas e deals com LLM, cruza com seu CRM e entrega tudo num dashboard. Os desafios técnicos e onde mora a oportunidade."
description: "Um pipeline open source que varre fontes públicas, extrai pessoas, empresas e deals com LLM, cruza com seu CRM e entrega tudo num dashboard. Os desafios técnicos e onde mora a oportunidade."
date: 2026-04-27T02:16:10.083Z
updated: 2026-04-27T15:19:00.440Z
slug: radar-inteligencia-mercado-open-source
cover: img/radar-inteligencia-mercado-open-source/capa.jpg
type: newsletter
---

<figure>
<img src="/blog/img/radar-inteligencia-mercado-open-source/1fb6402872-1200.webp" srcset="/blog/img/radar-inteligencia-mercado-open-source/1fb6402872-728.webp 728w, /blog/img/radar-inteligencia-mercado-open-source/1fb6402872-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="420" alt="Radar de Inteligência de Mercado" loading="lazy" decoding="async">
<figcaption>Radar de Inteligência de Mercado</figcaption>
</figure>

Toda equipe comercial e de inteligência de mercado conhece a mesma rotina. Alguém abre dez abas pela manhã, tenta acompanhar quem está movimentando capital, quais aquisições foram anunciadas, qual concorrente fez uma rodada, qual diretor saiu de uma empresa relevante. No fim do dia, esse alguém produz um resumo. Nas semanas seguintes, o resumo vira PowerPoint, vira lead, vira reunião, vira receita. Ou não vira nada, porque a informação chegou tarde, ou se perdeu no caminho entre planilhas e e-mails.

Eu fui esse alguém durante anos. E acabei construindo uma ferramenta para tirar essa rotina das minhas mãos. Chama-se **Market Intelligence Radar (MIR)**. É um pipeline open source que varre fontes de notícias configuráveis, extrai pessoas, empresas, deals e sinais de mercado usando LLMs, faz match com o CRM, enriquece os contatos via Apollo e SalesQL, e mostra tudo num dashboard. Está disponível no GitHub em [github.com/guifav/market-intelligence-radar](https://github.com/guifav/market-intelligence-radar), com licença MIT, sem telemetria, totalmente self-hosted.

Este post conta a história de como ele foi construído, os desafios reais que apareceram pelo caminho, a bagagem técnica que precisou se acumular para chegar nesse formato, e as oportunidades que vejo para empresas que adotarem uma ferramenta desse tipo.

#### O que ele faz, em sete passos

<figure>
<img src="/blog/img/radar-inteligencia-mercado-open-source/3892cfdfc0-1200.webp" srcset="/blog/img/radar-inteligencia-mercado-open-source/3892cfdfc0-728.webp 728w, /blog/img/radar-inteligencia-mercado-open-source/3892cfdfc0-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="200" alt="Radar de Mercado - o que faz?" loading="lazy" decoding="async">
<figcaption>Radar de Mercado - o que faz?</figcaption>
</figure>

A operação inteira é uma cadeia de sete etapas. Tudo começa com a configuração de fontes num arquivo JSON simples (TechCrunch, Reuters, qualquer URL que liste artigos). Em seguida, um scraper visita cada fonte, descobre URLs novas, e tenta extrair conteúdo limpo, primeiro via Firecrawl, com fallback para requests + BeautifulSoup quando a chave de API premium não está presente.

O passo crítico é o terceiro. Para cada artigo, um LLM (Claude, GPT ou Gemini, escolhido via variável de ambiente) recebe o texto e devolve JSON estruturado: pessoas mencionadas com cargo, empresa e contexto; empresas com setor, categoria e envolvimento em transações; deals do tipo M\&A, IPO, fundraising, JVs; sinais como mudança regulatória, movimento de mercado, risco; e tópicos. A taxonomia inteira fica num JSON configurável, então o vocabulário se adapta a qualquer setor.

Tudo é persistido em PostgreSQL. Pessoas e empresas são deduplicadas por canonical\_id, um hash SHA-256 de nome combinado com empresa, o que permite consolidar menções de múltiplos artigos num único registro de identidade.

Depois disso, dois fluxos paralelos operam sobre cada pessoa identificada. O matcher cruza o registro com o CRM importado via CSV usando quatro níveis de score: email exato, nome completo exato, prefixo do nome, e nome combinado com empresa. O enricher chama Apollo e SalesQL em waterfall para buscar email validado, telefone, LinkedIn, foto, dados da organização. Apenas pessoas relevantes, filtradas por divisão, ICP e regras de exclusão, chegam à etapa de enriquecimento, então o consumo de API se mantém controlado.

No final, um dashboard Next.js mostra tudo de forma navegável, com filtros por divisão, busca em texto livre, fluxo de aprovação manual de prospects, e exportação para CSV.

#### Arquitetura

<figure>
<img src="/blog/img/radar-inteligencia-mercado-open-source/213e247117-1200.webp" srcset="/blog/img/radar-inteligencia-mercado-open-source/213e247117-728.webp 728w, /blog/img/radar-inteligencia-mercado-open-source/213e247117-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="480" alt="Radar de Mercado - Arquitetura" loading="lazy" decoding="async">
<figcaption>Radar de Mercado - Arquitetura</figcaption>
</figure>

A arquitetura tem três fluxos paralelos que convergem no PostgreSQL. O fluxo principal de scan vai das notícias até o banco, passando por scraper e extractor. O fluxo de matching parte do CSV de contatos do CRM, passa pelo matcher, e atualiza pessoas no banco com a flag de match. O fluxo de enrichment chama APIs externas, traz os dados, e atualiza o registro da pessoa.

Algumas decisões merecem comentário.

Separei Python (pipeline) de Next.js (dashboard) através do PostgreSQL. Daria para fazer tudo em Node ou tudo em Python. Optei por separar porque cada parte tem requisitos diferentes. O backend de extração precisa de paralelismo, controle fino de rate limiting, integração natural com bibliotecas de scraping e parsing. O frontend precisa de SSR, componentes reativos, ergonomia para tabelas com filtros complexos. Cada lado faz o que faz melhor, conversa pelo banco, e não precisa ter comunicação direta. O efeito colateral positivo: dá pra rodar o pipeline numa VM barata e o dashboard em outra, e ambos escalam de forma independente.

Usei canonical\_id baseado em hash determinístico em vez de UUID. Quando o LLM extrai a mesma pessoa de dois artigos diferentes, o canonical\_id é idêntico, e a operação de upsert no banco consolida automaticamente. Elimina uma camada inteira de “tabela de identidades” que muita gente reinventa.

Tornei o LLM provider plugável. O arquivo mir/llm.py esconde Anthropic, OpenAI e Google atrás de uma única função extract(prompt, content). Trocar de provider é mudar uma variável de ambiente. Para um projeto que vai viver no domínio público, isso reduz fricção de adoção (o desenvolvedor usa o que já tem) e aumenta resiliência (se um provider tem outage, o outro assume).

E tornei o enrichment opcional. Apollo e SalesQL são pagas. O sistema funciona sem elas, e o status de cada pessoa reflete isso. O estado “not\_found\_in\_enrichment” é um nó válido no funil. O usuário adota gradualmente, conforme budget e maturidade do uso permitem.

#### Os desafios reais de construir isso

Construir um sistema desse tipo passa por problemas que nenhum tutorial cobre.

O primeiro é a qualidade do prompt de extração. LLMs são geniais para sumarização, mas instáveis para extração estruturada com taxonomia rígida. A primeira versão do meu prompt produzia JSON inconsistente, com nomes de setores inventados, capitalizações diferentes em respostas distintas, ocasionais campos faltando. A solução foi forçar a taxonomia dentro do próprio prompt, listando as categorias válidas e instruindo o modelo a usar apenas aquelas. Mesmo assim, era preciso pós-processar a saída para normalizar variações. Hoje o prompt tem cerca de 150 linhas e foi iterado dezenas de vezes contra um conjunto de artigos teste para chegar numa extração confiável.

O segundo desafio foi deduplicação. Quando o LLM lê um artigo sobre “Pedro Silva, CEO da TechCorp” e outro sobre “Pedro Henrique Silva, CEO da Tech Corp Brasil”, são a mesma pessoa? Construí três níveis. O canonical\_id pega os casos óbvios. Um normalizador de strings (lowercase, trim, remoção de acentos) pega variações de capitalização. Para os casos limítrofes, o sistema preserva o registro com o canonical\_id original e adiciona variação no array de menções, deixando o usuário humano decidir se são a mesma pessoa.

O terceiro foi a gestão de rate limit. Apollo permite cerca de 60 requests por minuto, SalesQL é mais conservador, e o LLM tem seu próprio teto. O scanner roda com workers paralelos, e cada worker pode bater no Apollo. Construí um ThreadSafeIntervalLimiter que serializa as chamadas globalmente, garantindo que mesmo com dez threads, o ritmo total respeita o teto da API. Em duas linhas no console, tudo se mantém dentro do orçamento.

O quarto foi o que chamo de ruído de relevância. A maioria das pessoas mencionadas em artigos não interessa para vendas: jornalistas, acadêmicos, políticos, atletas citados em comparação. Sem filtro, o pipeline gerava centenas de registros irrelevantes por dia. A solução foi construir três camadas de exclusão: regras hardcoded de profissão (jornalista, repórter, professor, governador), regras configuráveis por divisão (cada vertical define seu ICP), e regras de hard exclusion gerenciadas pelo usuário (pessoa específica, padrão de empresa). O resultado é cerca de dez vezes menos registros, com qualidade muito superior.

O quinto desafio foi o ICP scoring. Quando o sistema identifica uma pessoa, ele precisa decidir se ela é relevante para o negócio. Construí um sistema multidimensional que avalia categoria de operação, setor, país e cargo separadamente, e produz um percentual de match (0 a 100%). O cálculo é dimensions\_matched dividido por dimensions\_defined. Cada divisão pode ter o seu ICP. Esse score guia o usuário, que vê primeiro os matches acima de 90%, e descarta o resto.

#### A bagagem necessária

Olhando para trás, percebo que esse projeto sintetiza experiências acumuladas ao longo de anos. Para construir algo parecido, alguém precisa estar confortável em pelo menos quatro frentes técnicas, mais uma quinta camada de domínio.

**Engenharia backend pragmática**. Saber escrever Python que processa volume razoável de dados, lidar com concorrência (threading.Lock, ThreadPoolExecutor), entender quando paralelizar e quando serializar, escrever queries SQL eficientes para PostgreSQL, projetar schemas que sobrevivem a mudanças. Mão calejada com sistemas que rodam todos os dias importa mais do que título de engenheiro de Big Data.

Entendimento de **LLMs em ambiente de produção**. Isso vai além de saber chamar a API. Inclui escrever prompts robustos, lidar com a aleatoriedade dos modelos, projetar saídas verificáveis (JSON bem formado), conhecer os limites de cada provider, entender custos por token, gerenciar latência. Quem nunca colocou um LLM em produção vai gastar tempo aprendendo o que não funciona.

**Design de pipelines de dados**. Decidir o que é processo síncrono inline (matching numa pessoa que acabou de chegar) e o que vira batch (enrichment em lote, todo final do dia). Identificar pontos de falha, projetar idempotência, tratar duplicatas, gerenciar estado intermediário. Quem trabalhou com ETL ou com integração de sistemas vai reconhecer os padrões de pronto.

**Frontend moderno**. Next.js 15 com App Router, React 19, Tailwind 4, shadcn/ui. Construir o dashboard com componentes reutilizáveis, gerenciar estado entre páginas com Context, lidar com SSR e RSC apropriadamente, escrever queries que respeitam autenticação. Nada exótico, mas exige fluência.

A quinta camada, mais sutil, é **entender o domínio**. Construí o MIR primeiro para um setor específico, e só depois generalizei. Saber o que é um decision-maker, o que conta como deal, como uma equipe comercial efetivamente trabalha com leads, o que torna um sinal acionável. Sem essa camada, o sistema seria tecnicamente correto e operacionalmente inútil.

#### O que empresas podem fazer com isso

Existem três caminhos óbvios para uma empresa adotar uma ferramenta desse tipo.

→ O primeiro é monitoramento de mercado para times comerciais. Alguém da inteligência comercial configura o MIR com 30 fontes relevantes para o setor, importa os contatos do CRM, e em uma semana tem visibilidade completa sobre quem está se movimentando, quem fez fundraising, quais empresas estão entrando em novo mercado, quais executivos mudaram de cargo. O time de SDR usa essa lista priorizada para ações de outbound, com timing relevante e contexto rico. O time de account management consegue antecipar mudanças nos clientes (M\&A, troca de C-level) e agir antes do concorrente.

→ O segundo é geração de conteúdo. Editores de newsletters, podcasters, jornalistas econômicos passam horas por dia coletando notícias, identificando temas, mapeando narrativas. Uma instalação do MIR rodando diariamente entrega um corpus pré-extraído de pessoas, deals e sinais, organizados por taxonomia configurável. O editor passa direto para a parte criativa, com base de dados estruturada como insumo.

→ O terceiro é inteligência competitiva. Conselhos de empresas, áreas de M\&A, equipes de estratégia precisam estar atualizados sobre movimentos de concorrentes e adjacências. Configurar o MIR para monitorar fontes do setor, com filtros por concorrente específico ou tipo de transação, gera relatórios diários ou semanais sem trabalho humano repetitivo. O analista entra na conversa para interpretar, hipotetizar, propor ação.

Para empresas que querem ir além de adotar, vejo dois caminhos de extensão. Empresas que vendem dados podem usar o MIR como base para um produto SaaS multi-tenant, agregando insights de múltiplos clientes. A licença MIT permite uso comercial sem restrição. E empresas que constroem produtos verticais (real estate, healthcare, energia) podem fazer fork do MIR, customizar a taxonomia, plugar fontes específicas do setor, e oferecer uma versão whitelabel para seus clientes. O tempo até o primeiro release cai de meses para semanas.

#### Por que open source

Eu poderia ter mantido o código fechado e tentado vender. Optei por publicar com licença MIT por três razões.

Projetos como esse só ficam bons com uso real e diverso. Quanto mais gente experimentar, ajustar para o próprio contexto, abrir issues e PRs, mais maduro o sistema fica. Em código fechado, o ritmo de evolução é o ritmo de um time. Em código aberto, é o ritmo da comunidade.

<div class="callout">
<p>O valor real está na operação. Empresas que adotarem o MIR vão precisar configurar fontes, treinar a taxonomia, integrar com o CRM, ajustar o prompt para o setor. <strong>Esse trabalho é onde mora a vantagem competitiva</strong>. O código serve como commodity necessária para chegar até lá.</p>
</div>

E tudo que eu uso para construir profissionalmente é open source. PostgreSQL, Next.js, Tailwind, Linux, dezenas de bibliotecas Python. Devolver algo é justo.

#### Como começar

O repositório está em [github.com/guifav/market-intelligence-radar](https://github.com/guifav/market-intelligence-radar). Setup local roda em um comando: docker compose up -d. O readme tem documentação completa de configuração. O modelo padrão de LLM é Claude, e pode ser trocado para OpenAI ou Google a qualquer momento.

Se quiser conversar sobre arquitetura, customização para um setor específico, ou só dar feedback, abra uma issue no GitHub ou me ache no X ou LinkedIn. PRs são bem-vindos.
