---
title: "78% dos bots de IA querem uma coisa: seus dados"
subtitle: "Analisei o tráfego global da internet para entender quem está coletando, quem está citando e quem está ignorando as regras"
description: "Analisei o tráfego global da internet para entender quem está coletando, quem está citando e quem está ignorando as regras"
date: 2025-12-09T16:46:37.514Z
updated: 2025-12-09T16:51:27.615Z
slug: osbots-de-ia-querem-uma-coisa-dados
cover: img/osbots-de-ia-querem-uma-coisa-dados/capa.jpg
type: newsletter
---

Passei os últimos dias obstinado a entender os detalhes do tráfego no site da minha empresa, como a IA está acessando e consumindo nossos dados e gostaria de compartilhar com vocês o que descobri. As principais ferramentas que utilizei foram o Cloudflare e o Promptwatch (ambas na versão paga), que processam cerca de 20% do tráfego web global.

O que descobri me fez repensar como empresas deveriam estar se posicionando diante da explosão de agentes de IA. Os números contam uma história que poucos estão prestando atenção. A análise refere-se ao período de 06/11 a 6/12 de 2025.

***

### **O Brasil no mapa: de onde vem o tráfego?**

Antes de mergulhar nos bots, vale entender o contexto brasileiro. Analisei a distribuição geográfica das solicitações HTTP no país.

<figure>
<img src="/blog/img/osbots-de-ia-querem-uma-coisa-dados/8a87221259-1456.webp" srcset="/blog/img/osbots-de-ia-querem-uma-coisa-dados/8a87221259-728.webp 728w, /blog/img/osbots-de-ia-querem-uma-coisa-dados/8a87221259-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="959" alt="Conteúdo do artigo" loading="lazy" decoding="async">
<figcaption>Distribuição de tráfego no Brasil medida por Solicitações HTTP</figcaption>
</figure>

São Paulo concentra impressionantes 34,8% de todo o tráfego nacional, quase o dobro do segundo colocado. Rio de Janeiro aparece com 9,6%, seguido por Minas Gerais (7,9%) e Paraná (5,4%). O mapa revela uma internet brasileira ainda muito concentrada no eixo Sul-Sudeste, com o Nordeste ganhando relevância através de Bahia (4,8%), Ceará (4,3%) e Pernambuco (3,4%).

***

### **O pulso da internet brasileira**

<figure>
<img src="/blog/img/osbots-de-ia-querem-uma-coisa-dados/d66eec1fc6-1456.webp" srcset="/blog/img/osbots-de-ia-querem-uma-coisa-dados/d66eec1fc6-728.webp 728w, /blog/img/osbots-de-ia-querem-uma-coisa-dados/d66eec1fc6-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="446" alt="Conteúdo do artigo" loading="lazy" decoding="async">
<figcaption>Volume de tráfego distribuido por disposito e tipo de usuário</figcaption>
</figure>

Os dados de tráfego do Brasil no último mês revelam características interessantes. Do total, 64,8% vem de dispositivos móveis e 86,7% é humano. Além disso, 13,3% de todo o tráfego brasileiro é gerado por bots, um número que está crescendo rapidamente.

Os principais provedores (ASNs) que carregam esse tráfego são Claro/NET, Telefônica, TIM e Vtal. Sem surpresas aqui.

<figure>
<img src="/blog/img/osbots-de-ia-querem-uma-coisa-dados/b76557f4b8-1439.webp" srcset="/blog/img/osbots-de-ia-querem-uma-coisa-dados/b76557f4b8-728.webp 728w, /blog/img/osbots-de-ia-querem-uma-coisa-dados/b76557f4b8-1439.webp 1439w" sizes="(max-width: 760px) 100vw, 720px" width="1439" height="1000" alt="Conteúdo do artigo" loading="lazy" decoding="async">
<figcaption>Padrão de tráfego HTTP no Brasil em novembro/25</figcaption>
</figure>

O padrão de tráfego HTTP mostra aquela ondulação característica do comportamento humano. Picos durante o dia, vales à noite, com finais de semana levemente mais baixos. Mas o que me interessa é o que acontece nas camadas invisíveis desse tráfego.

***

### **A invasão dos bots: quem está varrendo a web?**

Aqui começa a parte interessante. Monitorei o tráfego de bots de IA que vasculham sites públicos para alimentar mecanismos de busca, treinar modelos e executar tarefas automatizadas.

<figure>
<img src="/blog/img/osbots-de-ia-querem-uma-coisa-dados/1a0851e31a-1393.webp" srcset="/blog/img/osbots-de-ia-querem-uma-coisa-dados/1a0851e31a-728.webp 728w, /blog/img/osbots-de-ia-querem-uma-coisa-dados/1a0851e31a-1393.webp 1393w" sizes="(max-width: 760px) 100vw, 720px" width="1393" height="1000" alt="Conteúdo do artigo" loading="lazy" decoding="async">
<figcaption>Tráfego de bots de IA e crawler</figcaption>
</figure>

Os cinco bots de IA mais ativos na internet global são o GPTBot (OpenAI) com 28,2% do tráfego, Meta-ExternalAgent com 22,5%, ClaudeBot (Anthropic) com 16%, Amazonbot com 10,6% e Applebot com 5,6%.

Mas o dado mais revelador está no gráfico inferior. **78% de todo esse tráfego de bots tem como objetivo “Treinamento”**. Apenas 15,5% é para “Busca” e míseros 5,7% para “Ação de usuário”.

Traduzindo: a grande maioria dos bots de IA não está tentando responder perguntas de usuários. Está coletando dados para treinar a próxima geração de modelos. Sua empresa está alimentando esses modelos sem saber? Espero que esteja os alimentando conscientemente.

***

### **O ranking de popularidade: quem está ganhando a corrida da IA?**

<figure>
<img src="/blog/img/osbots-de-ia-querem-uma-coisa-dados/7f5a2624d7-1456.webp" srcset="/blog/img/osbots-de-ia-querem-uma-coisa-dados/7f5a2624d7-728.webp 728w, /blog/img/osbots-de-ia-querem-uma-coisa-dados/7f5a2624d7-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="478" alt="Conteúdo do artigo" loading="lazy" decoding="async">
<figcaption>Popularidade dos serviços de IA Generativa</figcaption>
</figure>

Usando o tráfego do resolver DNS 1.1.1.1 (um dos maiores do mundo), é possível medir a popularidade real dos serviços de IA generativa. O resultado é um ranking dinâmico ao longo de novembro e dezembro de 2025.

ChatGPT/OpenAI reina absoluto na posição #1 e nem sequer oscila. Google Gemini e Perplexity disputam intensamente as posições #2 e #3, com trocas frequentes. Claude/Anthropic mantém uma posição estável entre #4 e #5, alternando com GitHub Copilot.

O que chama atenção é a volatilidade das posições intermediárias. Grok/xAI, Character AI e Codeium/Windsurf AI trocam de lugar constantemente. DeepSeek aparece subindo no ranking nas últimas semanas, um sinal do avanço chinês nesse mercado.

QuillBot e Suno AI completam o top 10, mostrando que ferramentas especializadas (paráfrase e música) conquistaram seu nicho.

***

### **A disputa dos robots.txt: quem está sendo bloqueado?**

Aqui está o ponto crítico para qualquer empresa com presença digital.

### **Quem está sendo permitido ou bloqueado?**

Os dados que obtive permitiu analisar 3.879 arquivos robots.txt dos dez mil principais domínios da internet para entender como os sites estão tratando os bots de IA. A tabela abaixo consolida os dados de permissão (Allow) e bloqueio (Disallow) para os principais agentes.

<figure>
<img src="/blog/img/osbots-de-ia-querem-uma-coisa-dados/6590fea73d-1268.webp" srcset="/blog/img/osbots-de-ia-querem-uma-coisa-dados/6590fea73d-728.webp 728w, /blog/img/osbots-de-ia-querem-uma-coisa-dados/6590fea73d-1268.webp 1268w" sizes="(max-width: 760px) 100vw, 720px" width="1268" height="1000" alt="Conteúdo do artigo" loading="lazy" decoding="async">
<figcaption>3.879 arquivos robots.txt analisados nos dez mil principais domínios. Atualizado em 30/11/2025.</figcaption>
</figure>

Alguns padrões saltam aos olhos.

O **facebookexternalhit** é o único bot com saldo positivo significativo. Faz sentido, já que bloquear o Facebook significa perder previews de links em uma das maiores redes sociais do mundo.

**GPTBot** lidera os bloqueios com 380 domínios restringindo acesso, mas também tem 115 domínios permitindo. O paradoxo é evidente: alguns sites querem aparecer nas respostas do ChatGPT, outros querem proteger seu conteúdo de ser usado para treinamento.

**CCBot** (CommonCrawl), **ClaudeBot** e **Bytespider** (ByteDance) formam o trio mais bloqueado proporcionalmente. O CCBot é particularmente interessante porque o CommonCrawl é uma organização sem fins lucrativos que fornece dados para pesquisa, mas muitos sites não fazem essa distinção.

**Googlebot** tem saldo ligeiramente negativo, mas os 257 bloqueios são majoritariamente parciais (234 parciais vs 23 totais). Ninguém quer sumir do Google, mas alguns controlam quais partes do site são indexadas.

```
Por que o mesmo operador tem vários bots?

Empresas de IA costumam operar múltiplos crawlers, cada um com uma finalidade específica. Entender essa distinção é crucial para configurar seu robots.txt de forma inteligente.

- OpenAI opera três bots principais. O GPTBot coleta dados para treinamento de modelos futuros. O ChatGPT-User é o bot que navega em tempo real quando um usuário pede ao ChatGPT para “buscar algo na web”. O OAI-SearchBot alimenta o SearchGPT, o motor de busca da empresa. Na prática, você pode bloquear o GPTBot (impedindo uso para treinamento) mas permitir o ChatGPT-User (aparecendo nas respostas quando usuários pedem buscas).

- Google faz distinção semelhante. O Googlebot é o crawler tradicional de indexação para o buscador. O Google-Extended coleta dados especificamente para treinar modelos de IA como o Gemini. Bloquear o Google-Extended não afeta sua posição no Google Search, apenas impede que seu conteúdo treine a IA do Google.

- Anthropic opera o ClaudeBot para treinamento e o anthropic-ai como identificador adicional. A empresa também está desenvolvendo bots separados para busca em tempo real.

- Meta tem o facebookexternalhit, que gera aqueles previews de link quando alguém compartilha uma URL no Facebook ou Instagram. Já o meta-externalagent coleta dados para treinar os modelos de IA da empresa. Bloquear o primeiro significa perder os previews visuais nas redes sociais.

Essa separação por finalidade é justamente o que a coluna “Bots separados por finalidade” avalia na tabela de práticas recomendadas. Operadores que não fazem essa distinção te obrigam a uma escolha binária: ou libera tudo, ou bloqueia tudo.
```

***

### **O comportamento ético dos bots: quem joga limpo?**

Criei uma tabela de “práticas recomendadas” avaliando como cada operador de bot se comporta. Antes de ver os resultados, vale entender o que cada critério significa.

**Verificado por IP** indica se o operador permite que donos de sites confirmem a autenticidade do bot através de verificação reversa de DNS. Sem isso, qualquer crawler mal-intencionado pode se passar pelo GPTBot ou ClaudeBot e você não tem como saber.

**WebBotAuth** é um novo padrão de autenticação baseado em tokens criptográficos. É mais robusto que a verificação por IP, mas ainda está em fase inicial de adoção.

**Bots separados por finalidade** revela se o operador distingue claramente entre diferentes tipos de crawler. Por exemplo, a OpenAI tem o GPTBot (para treinamento) e o OAI-SearchBot (para busca). Isso permite que sites bloqueiem treinamento mas permitam busca.

**Respeita robots.txt** é o básico do básico. O arquivo robots.txt é o padrão da web desde 1994 para indicar quais partes do site podem ser acessadas por crawlers. Operadores que não respeitam esse arquivo estão ignorando a vontade explícita do dono do site.

<figure>
<img src="/blog/img/osbots-de-ia-querem-uma-coisa-dados/c140403c8b-965.webp" srcset="/blog/img/osbots-de-ia-querem-uma-coisa-dados/c140403c8b-728.webp 728w, /blog/img/osbots-de-ia-querem-uma-coisa-dados/c140403c8b-965.webp 965w" sizes="(max-width: 760px) 100vw, 720px" width="965" height="1000" alt="Conteúdo do artigo" loading="lazy" decoding="async">
<figcaption>Comportamento coletivo observado a partir de bots de modelos de IA e provedores de serviços líderes, classificados por volume de tráfego</figcaption>
</figure>

Alguns padrões saltam aos olhos nessa tabela.

**Amazon** é o único gigante que implementa verificação tanto por IP quanto por WebBotAuth, o padrão ouro de identificação. **OpenAI** está em processo de implementar WebBotAuth e já separa seus bots por finalidade, diferenciando crawlers de busca e de treinamento.

**ByteDance** e **Grok (xAI)** apresentam os piores históricos. ByteDance não implementa nenhuma das verificações e explicitamente não respeita robots.txt. Grok não verifica IP, não usa WebBotAuth, não separa bots e tem status “Incerto” no respeito ao robots.txt.

Entre os players menores, **CommonCrawl, Awario, Factset e netEstate** mostram comportamento exemplar. O **Internet Archive** tem status “Incerto” no respeito ao robots.txt, o que é compreensível dado seu papel de arquivamento histórico.

```
O que sua empresa deveria fazer

Antes de decidir quais bots permitir ou bloquear, você precisa entender o que está acontecendo no seu servidor. O primeiro passo é analisar os logs de acesso e identificar quais crawlers estão visitando seu site, com que frequência e quais páginas estão consumindo. Ferramentas como o próprio Cloudflare, AWS WAF ou até análise direta de logs do servidor conseguem fornecer esse diagnóstico.

Com o mapa de tráfego em mãos, defina seus critérios de aceitação. Uma abordagem sensata é criar três categorias: bots confiáveis (verificação por IP, respeitam robots.txt), bots toleráveis (cumprem parcialmente os critérios) e bots indesejados (não verificáveis, ignoram regras). A tabela de práticas recomendadas deste artigo pode servir como ponto de partida.

Na prática, considere permitir bots que oferecem verificação por IP (você consegue confirmar que são legítimos), que separam finalidades (você escolhe se quer aparecer em busca mas não em treinamento) e que respeitam robots.txt (você tem controle real). Bots que falham nos três critérios, como o Bytespider da ByteDance, merecem bloqueio preventivo.

Por fim, registre sua política e revise periodicamente. O ecossistema de bots de IA muda rapidamente. A OpenAI não tinha bots separados há dois anos. Hoje tem três. Sua política precisa acompanhar essa evolução.
```

***

### **A proporção reveladora: quanto os bots coletam vs. quanto citam?**

<figure>
<img src="/blog/img/osbots-de-ia-querem-uma-coisa-dados/597457ea9f-1456.webp" srcset="/blog/img/osbots-de-ia-querem-uma-coisa-dados/597457ea9f-728.webp 728w, /blog/img/osbots-de-ia-querem-uma-coisa-dados/597457ea9f-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="476" alt="Conteúdo do artigo" loading="lazy" decoding="async">
<figcaption>Relação entre crawlers e referência</figcaption>
</figure>

Este é talvez o dado mais impactante da análise. Calculei a proporção entre solicitações de crawlers (coleta de dados) e referências (citações/links de volta para a fonte).

**Anthropic** tem a proporção mais desequilibrada, com **31.600 crawls para cada 1 referência**. O ClaudeBot representa 93,8% desse tráfego. Isso significa que para cada vez que o Claude cita uma fonte, ele coletou dados 31.600 vezes.

**OpenAI** vem em segundo com 977,4:1. O GPTBot representa 85,4% do tráfego. Ainda alto, mas muito melhor que a Anthropic.

**Perplexity**, que se posiciona como motor de busca com citações, tem proporção de 81,8:1. O PerplexityBot responde por 96,1% do tráfego. Considerando que o produto deles é justamente citar fontes, essa proporção parece adequada.

**Microsoft** (34,7:1) e **Yandex** (21,2:1) mostram proporções mais equilibradas. **Google** tem 7,3:1, a melhor entre os grandes. Faz sentido dado que o negócio deles é direcionar tráfego para sites, não reter usuários.

Na outra ponta, **ByteDance** (2:1) e **DuckDuckGo** (1,5:1) têm as proporções mais equilibradas, coletando pouco em relação ao quanto citam.

***

### **O que isso significa para sua empresa?**

Se você tem um site, esses dados contam uma história que exige ação.

**1. Seus dados estão alimentando modelos de IA, provavelmente sem sua permissão.** 78% do tráfego de bots de IA é para treinamento, não para busca. Seu conteúdo pode estar sendo usado para treinar o próximo GPT sem que você veja um centavo ou uma citação.

**2. O robots.txt virou campo de batalha estratégico.** Bloquear todos os bots de IA significa potencialmente sumir das respostas de ChatGPT, Perplexity e Claude. Permitir todos significa abrir seu conteúdo para treinamento. **A decisão precisa ser deliberada.**

**3. Nem todos os bots jogam com as mesmas regras.** ByteDance não respeita robots.txt. Grok tem comportamento opaco. Amazon é o único com verificação completa. Saber quem é quem ajuda a configurar proteções adequadas.

**4. A proporção crawler/referência revela intenções.** Anthropic coleta 31.600 vezes mais do que cita. Google coleta 7 vezes mais. Essa diferença diz muito sobre o modelo de negócio de cada um e sobre quanto valor você pode esperar de volta.

**5. O Brasil é relevante, mas concentrado.** 35% do tráfego vem de São Paulo. Se seu negócio depende de presença digital nacional, a concentração geográfica importa para decisões de CDN, cache e infraestrutura.

***

### **O próximo capítulo**

Clichê que precisa ser repetido: *Estamos vivendo um momento de inflexão. Os bots de IA estão se tornando tão importantes quanto os bots de busca tradicionais, talvez mais. As empresas que entenderem essa dinâmica primeiro terão vantagem competitiva.*

A boa notícia? Ferramentas como as que utilizei (Cloudflare e Promptwatch) democratizam o acesso a esses dados. A má notícia? A maioria das empresas nem sabe que essa disputa está acontecendo nos servidores delas.

Se você chegou até aqui, me conta: sua empresa já tem uma política para bots de IA? Ou ainda está no piloto automático?

***

**Guilherme Favaron**

***

*Nota: Os dados utilizados nesta análise foram obtidos via Cloudflare e Promptwatch que disponibilizam estatísticas sobre o tráfego global da internet. Período de referência: 06/11/2025 a 06/12/2025.*
