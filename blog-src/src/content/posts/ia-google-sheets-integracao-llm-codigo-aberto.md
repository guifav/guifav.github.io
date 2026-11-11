---
title: "IA no dia a dia corporativo: uma abordagem prática que funciona"
subtitle: "Como integrar GPT, Claude, Grok e Gemini diretamente ao Google Sheets — com código aberto e passo a passo"
description: "Aprenda a usar os principais modelos de IA como fórmulas no Google Sheets. Código aberto, gratuito, com suas próprias API keys. Guia prático com casos de uso reais para empresas."
date: 2025-12-15T08:30:30.984Z
updated: 2025-12-15T08:30:51.564Z
slug: ia-google-sheets-integracao-llm-codigo-aberto
cover: img/ia-google-sheets-integracao-llm-codigo-aberto/capa.jpg
type: newsletter
---

Nos últimos meses, tenho observado um padrão curioso nas empresas: existe um abismo entre o entusiasmo com inteligência artificial e sua aplicação real no trabalho cotidiano.

De um lado, discussões sofisticadas sobre AGI, modelos multimodais e o futuro do trabalho. Do outro, equipes que ainda copiam dados de planilhas para colar no ChatGPT, uma linha por vez.

Esse contraste me levou a uma reflexão sobre onde a IA realmente agrega valor hoje e não em casos de uso futuristas, mas nas tarefas que consomem horas toda semana.

***

## O problema real

A maioria das ferramentas de produtividade com IA segue um modelo: você vai até a interface, digita sua pergunta, recebe uma resposta, e volta ao seu trabalho. Funciona bem para consultas pontuais.

Mas o trabalho corporativo raramente é pontual. É processamento de listas, padronização de dados, análise de centenas de feedbacks, preparação para dezenas de reuniões. Tarefas que se repetem, com pequenas variações.

Para esse tipo de trabalho, a fricção de alternar entre ferramentas mata a produtividade que a IA deveria criar.

***

## Uma solução simples

Desenvolvi um conjunto de funções que integram os principais modelos de linguagem diretamente ao **Google Sheets**. A ideia não é original (existem extensões pagas que fazem isso). A diferença é que este código é aberto, usa suas próprias API keys, e você tem controle total sobre custos e privacidade. Isso se resume a uma enorme redução de custo de utilização de LLMs como o ChatGPT, Gemini, Claude e Grok.

Na prática, funciona assim:

```
=claude(”Classifique esta empresa por setor:”, A2)
```

A célula retorna a resposta do modelo. Arraste a fórmula para baixo e processe 500 linhas em minutos.

<video src="/blog/video/ia-google-sheets-integracao-llm-codigo-aberto.mp4" poster="/blog/img/ia-google-sheets-integracao-llm-codigo-aberto/video-poster.webp" width="1502" height="1080" controls preload="none" playsinline></video>

O código suporta quatro modelos: GPT (OpenAI), Claude (Anthropic), Grok (x.ai) e Gemini (Google). Cada um tem características diferentes, e poder alternar entre eles na mesma planilha permite escolher o mais adequado para cada tarefa.

As funções contemplam os modelos mais atuais:

- Google Gemini 3 Pro
- Claude Sonnet 4.5
- ChatGPT 5.1
- Grok 4.1

Caso queira fazer o upgrade ou downgrade do modelo, basta alterar o nome do modelo diretamente no código abaixo.

***

## Como implementar

A configuração leva menos de dez minutos. Você vai precisar de uma conta Google e das chaves de API dos modelos que quiser usar (todos oferecem créditos iniciais gratuitos).

### Passo 1: Acessar o Apps Script

No Google Sheets, vá até o menu **Extensões** e clique em **Apps Script**.

<figure>
<img src="/blog/img/ia-google-sheets-integracao-llm-codigo-aberto/0a5ee2f8b8-1456.webp" srcset="/blog/img/ia-google-sheets-integracao-llm-codigo-aberto/0a5ee2f8b8-728.webp 728w, /blog/img/ia-google-sheets-integracao-llm-codigo-aberto/0a5ee2f8b8-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="1047" alt="Imagem: Menu Extensões &gt; Apps Script" loading="lazy" decoding="async">
<figcaption>Imagem: Menu Extensões &gt; Apps Script</figcaption>
</figure>

Isso abre o editor de scripts do Google, onde você pode adicionar código personalizado à sua planilha.

### Passo 2: Adicionar o código

No editor que abrir, você verá um arquivo chamado `Código.gs` na barra lateral. Delete qualquer conteúdo existente e cole o código do repositório.

<figure>
<img src="/blog/img/ia-google-sheets-integracao-llm-codigo-aberto/6d330c29a0-1456.webp" srcset="/blog/img/ia-google-sheets-integracao-llm-codigo-aberto/6d330c29a0-728.webp 728w, /blog/img/ia-google-sheets-integracao-llm-codigo-aberto/6d330c29a0-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="928" alt="Imagem: Editor Apps Script com código" loading="lazy" decoding="async">
<figcaption>Imagem: Editor Apps Script com código</figcaption>
</figure>

As primeiras linhas do código contêm as variáveis para suas chaves de API:

```
const OPENAI_API_KEY = “sua_chave_aqui”;
const ANTHROPIC_API_KEY = “sua_chave_aqui”;
const GROK_API_KEY = “sua_chave_aqui”;
const GEMINI_API_KEY = “sua_chave_aqui”;
```

Substitua pelos valores reais das suas chaves. Você não precisa configurar todos os modelos — apenas os que pretende usar. Você pode obter sua API Key para qualquer dos provedores de serviços conforme abaixo:

<figure>
<img src="/blog/img/ia-google-sheets-integracao-llm-codigo-aberto/e9a492d007-571.webp" width="571" height="206" alt="" loading="lazy" decoding="async">
<figcaption>Imagem: LLMs e onde encontrar as chave de API</figcaption>
</figure>

Link clicável:

- [OpenAI](https://platform.openai.com/api-keys)
- [Anthropic](https://console.anthropic.com)
- [x.ai](https://console.x.ai)
- [Google](https://aistudio.google.com/apikey)

### Passo 3: Salvar e autorizar

Clique em **Salvar** (Ctrl+S). Na primeira vez que usar uma das funções, o Google pedirá autorização para executar o script. Siga o fluxo de permissões — é um processo padrão para scripts personalizados.

### Passo 4: Usar as funções

Volte para sua planilha. Agora você pode usar as funções como qualquer fórmula nativa:

```
=claude(”Traduza para inglês:”, A2)
=grok(”Classifique o setor desta empresa:”, A2, “ - “, B2)
=chatgpt(”Escreva um email de follow-up para”, A2, “da empresa”, B2, “sobre o tema”, C2)
=geminy(”Resuma os principais pontos desta tabela:”, A2:D10)
```

As funções aceitam múltiplas células como contexto — útil quando você precisa combinar informações de várias colunas em um único prompt.

<div class="pullquote">
<p>Você pode pedir para seu time de tecnologia implementar essas funções para todos os colaboradores da empresa de uma só vez.</p>
<p>Encaminhe esse artigo para seu time técnico.</p>
</div>

***

## Casos de uso por área

Depois de semanas de uso, organizei os casos que mais funcionam por área de atuação. Cada exemplo pode ser adaptado para seu contexto específico.

### Operações e Dados

**Padronização de nomes** — Quando listas chegam com variações de grafia para a mesma empresa ou pessoa.

```
=grok(”Padronize este nome de empresa:”, A2)
```

**Categorização de leads** — Classificar automaticamente por setor, porte ou potencial.

```
=claude(”Classifique este lead por setor:”, A2)
```

**Limpeza de dados** — Corrigir inconsistências de formatação em massa.

```
=chatgpt(”Corrija erros de formatação:”, A2)
```

**Deduplicação inteligente** — Identificar registros que parecem duplicados mas têm grafias diferentes.

```
=geminy(”Esta empresa é a mesma que”, A2, “?”, B2, “Responda sim ou não”)
```

### Marketing e Conteúdo

**Criação de títulos** — Gerar variações para testes A/B ou brainstorming.

```
=chatgpt(”Crie 3 títulos para um artigo sobre:”, A2)
```

**Resumo de artigos** — Condensar textos longos em pontos principais.

```
=claude(”Resuma os pontos principais:”, A2)
```

**Tradução** — Traduzir conteúdo diretamente na planilha.

```
=geminy(”Traduza para espanhol:”, A2)
```

**Análise de feedback** — Extrair insights de respostas abertas de pesquisas.

```
=grok(”Extraia os 3 principais pontos deste feedback:”, A2)
```

### Vendas e CRM

**Pesquisa de empresas** — Preparar briefings antes de reuniões ou ligações.

```
=geminy(”Faça uma busca na internet e traga as últimas notícias sobre a empresa”, A2, “e seus principais executivos”)
```

**Qualificação de leads** — Avaliar fit com seu ICP de forma automatizada.

```
=chatgpt(”Este lead tem fit com B2B SaaS? Dados:”, A2, B2, C2)
```

**Geração de emails** — Criar rascunhos personalizados em escala.

```
=grok(”Escreva um email de prospecção para”, A2, “da empresa”, B2)
```

**Enriquecimento de dados** — Inferir informações faltantes com base no contexto.

```
=geminy(”Qual o provável cargo de”, A2, “baseado na empresa”, B2)
```

### Análise e BI

**Interpretação de métricas** — Traduzir números em narrativas compreensíveis.

```
=claude(”Interprete esta variação de”, A2, “para”, B2, “em vendas”)
```

**Geração de insights** — Identificar padrões em conjuntos de dados.

```
=chatgpt(”Que insights podemos tirar destes dados:”, A2:D2)
```

**Explicação de anomalias** — Entender por que valores fogem do esperado.

```
=grok(”Por que este valor pode estar fora do padrão:”, A2)
```

***

## O que não funciona bem

Não substitui análise crítica. Os modelos erram, especialmente em dados numéricos ou informações que exigem precisão factual. O resultado é um primeiro rascunho que precisa de revisão, não uma resposta final.

Não é adequado para dados sensíveis. Os prompts são processados por servidores externos. Informações confidenciais de clientes ou dados regulados não devem passar por esse fluxo.

Não escala infinitamente. APIs têm limites de requisição e custos por token. Para volumes muito grandes, outras arquiteturas fazem mais sentido.

***

## Qual modelo usar

Após testar extensivamente, algumas orientações práticas:

**Claude** funciona bem para análises que exigem raciocínio mais elaborado e textos longos. É o que uso para resumos e categorizações complexas.

**Grok** tem boa velocidade e custo-benefício para tarefas simples e diretas. Útil para classificações binárias ou respostas curtas.

**GPT** continua sendo versátil para a maioria dos casos, especialmente geração de texto criativo.

**Geminy** faz sentido se você já está no ecossistema Google e quer minimizar dependências externas.

Mas a recomendação real é testar. O mesmo prompt pode ter resultados diferentes em cada modelo, e o melhor para sua aplicação específica só se descobre experimentando.

<figure>
<img src="/blog/img/ia-google-sheets-integracao-llm-codigo-aberto/8e8468d0f8-650.webp" width="650" height="207" alt="Imagem: Modelos e respectivas funções disponíveis" loading="lazy" decoding="async">
<figcaption>Imagem: Modelos e respectivas funções disponíveis</figcaption>
</figure>

***

## Considerações finais

A parte mais difícil dessa implementação não é técnica — é cultural. Convencer equipes a confiar em outputs de IA, estabelecer processos de verificação, e identificar quais tarefas realmente se beneficiam dessa automação.

Minha sugestão é começar pequeno: uma tarefa específica, com volume suficiente para justificar o setup, e onde erros não tenham consequências graves. A partir daí, o uso se expande naturalmente conforme as pessoas descobrem aplicações.

\*<em>Eu precisei chamar a função que utiliza o Gemini do Google de Geminy com “Y” pois há uma função já no Google Sheets com este nome.</em>

***

## Código disponível

Disponibilizei o código no GitHub para quem quiser experimentar ou adaptar. O repositório inclui documentação completa de cada função, exemplos de uso e orientações de troubleshooting.

[Todos os detalhes, aqui no repositório do Github](https://github.com/guifav/google_sheets_with_ai).

Se você implementar algo interessante ou tiver sugestões de melhoria, fico feliz em saber.
