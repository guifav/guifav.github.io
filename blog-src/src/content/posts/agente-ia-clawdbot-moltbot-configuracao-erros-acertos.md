---
title: "Coloquei o agente de IA Clawdbot para rodar na minha máquina principal. Eis o que aprendi."
subtitle: "De Claude Opus 4.5 a modelo local: como configurei um agente de IA que responde no WhatsApp, posta nas redes e gerencia minha rotina - sem custo recorrente. Setup, armadilhas e aprendizados."
description: "Instalei o Clawdbot (Moltbot) para automatizar minha rotina digital. Custos, problemas com RAM, integração via WhatsApp e o que realmente funcionou após semanas de uso real."
date: 2026-01-28T10:09:52.766Z
updated: 2026-01-28T10:11:17.061Z
slug: agente-ia-clawdbot-moltbot-configuracao-erros-acertos
cover: img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/capa.jpg
type: newsletter
---

## **O que é o Clawdbot (em breve Moltbot)**

O Clawdbot é um assistente de IA auto-hospedado que viralizou no início de 2026. Criado por Peter Steinberger (fundador da PSPDFKit), representa um novo paradigma: um agente que roda na sua máquina, com memória persistente, acesso às suas ferramentas e disponível 24/7 via WhatsApp, Telegram ou Slack.

Diferente do ChatGPT ou Siri, você controla tudo. Seus dados ficam com você. E o agente aprende suas preferências ao longo do tempo.

O projeto está mudando de nome para **Moltbot**. Aviso que quando o agente agir em meu nome publicamente (posts no Twitter, Substack), ele usa o prefixo **\[clawdbot\]** ou **\[molty\]** para identificar que o conteúdo foi gerado por ele.

<figure>
<img src="/blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/3225552444-1456.webp" srcset="/blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/3225552444-728.webp 728w, /blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/3225552444-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="856" alt="Interface do clawdbot para gerenciamento de agentes" loading="lazy" decoding="async">
<figcaption>Interface do clawdbot para gerenciamento de agentes</figcaption>
</figure>

***

## **Por que é inovador**

<figure>
<img src="/blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/0be41b9a2f-1230.webp" srcset="/blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/0be41b9a2f-728.webp 728w, /blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/0be41b9a2f-1230.webp 1230w" sizes="(max-width: 760px) 100vw, 720px" width="1230" height="712" alt="Porque o Clawdbot é inovador?" loading="lazy" decoding="async">
<figcaption>Porque o Clawdbot é inovador?</figcaption>
</figure>

A inovação real não está na tecnologia isolada. Está na integração. O Clawdbot usa o [Model Context Protocol (MCP)](/blog/mcp-model-context-protocol-revolucao-inteligencia-artificial/) para conectar com serviços externos: Google Calendar, Gmail, Notion, Todoist, Spotify, dispositivos smart home. Cada integração expande o que ele pode fazer.

### **Clawdbot vs N8N e outros orquestradores**

Você pode estar pensando: “Isso parece um N8N da vida. Qual a diferença?”

A diferença fundamental é a interface. No N8N, você configura fluxos visualmente, arrasta blocos, conecta triggers. É poderoso, mas você está programando.

No Clawdbot, **você configura conversando**. Quer mudar a frequência de posts? Manda mensagem. Quer ajustar o tom das respostas? Explica o que quer. Quer adicionar uma nova automação? Descreve em linguagem natural.

**O agente entende contexto, lembra de decisões anteriores e se adapta.** Não é um fluxo estático que você desenhou uma vez. É um sistema que evolui conforme você usa.

Isso muda completamente a experiência. Em vez de abrir uma interface, pensar em lógica de automação e testar fluxos, você simplesmente conversa pelo WhatsApp como se fosse um colega.

### **Uso pessoal vs profissional**

Uso o Clawdbot para **fins pessoais**: automatizar minha presença digital, organizar minha rotina, gerenciar tarefas repetitivas do dia a dia.

Para contexto profissional (trabalho em equipe, código em produção, projetos corporativos) minha recomendação é outra: **Claude Code** para desenvolvimento e **Claude Cowork** para colaboração. São ferramentas desenhadas para esse contexto, com as proteções e integrações adequadas.

O Clawdbot brilha na integração com a vida pessoal. É ali que a configuração via conversa faz mais sentido. Você não quer abrir um painel de controle para ajustar como seu **assistente pessoal** se comporta. Você quer mandar uma mensagem e pronto.

***

## **O que o Clawdbot pode fazer**

### Agendamento e calendário

“Agende uma reunião para terça à tarde” — ele verifica seus horários livres, encontra slots disponíveis e pode até enviar convites.

### Triagem de emails

“Resuma meus emails não lidos e rascunhe respostas para os urgentes” — deixe o agente lidar com a caixa de entrada enquanto você trabalha no que importa.

### Controle de smart home

“Apague todas as luzes e coloque o ar em 22°C” — controla Philips Hue, HomeKit, Home Assistant via conversa natural.

### Notas e pesquisa

“Adicione este artigo à minha lista de leitura no Notion com um resumo” — integra com Notion, Obsidian, Apple Notes.

### Briefings diários

Configure jobs agendados para receber resumos matinais com clima, eventos do calendário e lembretes importantes.

### Mídia e entretenimento

“Toque minha playlist de foco no Spotify” — controle de reprodução e recomendações baseadas no seu humor ou atividade.

### Automação de conteúdo (meu caso)

“faça um post no twitter sobre \[link\]. Advoque em favor do seguinte posicionamento \[posicionamento\]".

***

## **Como instalar**

Várias opções disponíveis:

<figure>
<img src="/blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/4440a441e7-916.webp" srcset="/blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/4440a441e7-728.webp 728w, /blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/4440a441e7-916.webp 916w" sizes="(max-width: 760px) 100vw, 720px" width="916" height="482" alt="Métodos de instalação do clawdbot" loading="lazy" decoding="async">
<figcaption>Métodos de instalação do clawdbot</figcaption>
</figure>

### Passo a passo básico (5 minutos)

1. **Escolha o método de instalação** — Docker para servidores, Homebrew para terminal, macOS App para setup mais fácil
2. **Configure o provedor de IA** — Adicione sua API key (Claude/Anthropic, GPT-4/OpenAI, ou modelo local)
3. **Conecte seu canal de chat** — WhatsApp, Telegram, Slack ou Discord para conversar de qualquer lugar
4. **Adicione integrações MCP** — Calendário, email, Notion, Todoist, smart home, conforme sua necessidade
5. **Comece a conversar** — Envie sua primeira mensagem e veja funcionar

Documentação completa em [docs.clawd.bot](https://docs.clawd.bot/).

***

## Meu setup (diferente do recomendado)

A recomendação oficial é criar conta de email e número de WhatsApp separados para o bot. Eu não fiz isso.

Instalei o Clawdbot diretamente na minha máquina de trabalho principal (MacBook Pro M2, 16GB RAM). Me comunico com ele pelo meu WhatsApp pessoal.

### O problema inicial

Quando ativei, o bot tentava responder todas as mensagens que chegavam no meu WhatsApp. Qualquer pessoa que me mandava mensagem recebia uma tentativa de resposta (que falhava porque não estava configurado). Caos.

### A solução: permissões granulares

Configurei assim:

**Google Workspace:** somente leitura

- Gmail: lê emails, não envia
- Calendar: lê eventos, não cria
- Contacts: lê contatos
- Drive: lê arquivos

**WhatsApp:**

- Lê todas as mensagens
- Só responde às que EU envio para mim mesmo
- Não responde em grupos
- Não responde a desconhecidos

**Twitter:** liberdade total

- É uma rede que desconsidero no meu dia a dia
- Uso apenas para me informar sobre a bolha tech
- Se o bot fizer besteira ali, não me afeta

Tudo via projeto no Google Cloud. Consigo medir custos e desplugar imediatamente se algo der errado.

***

## **Evolução dos modelos e custos**

<figure>
<img src="/blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/179bc8c682-1456.webp" srcset="/blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/179bc8c682-728.webp 728w, /blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/179bc8c682-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="403" alt="Modelos que utilizei nas configurações do clawdbot" loading="lazy" decoding="async">
<figcaption>Modelos que utilizei nas configurações</figcaption>
</figure>

O modelo local é viável para a maioria das tarefas. Mas encontrei um problema: **sobrecarga de memória RAM**. Quando rodo scripts pesados ou apps pesados simultaneamente, a máquina trava. O modelo de 20B parâmetros compete por recursos com o resto do sistema.

Para quem tem 32GB+ de RAM, provavelmente não é problema. Com 16GB, precisa gerenciar.

***

## **Parametrização**

O Clawdbot cria e mantém automaticamente uma pasta de configuração (`clawd/` no meu caso). Essa pasta contém:

```
clawd/
├── SOUL.md          # Princípios operacionais do agente
├── IDENTITY.md      # Persona pública [molty]
├── MEMORY.md        # Memória de longo prazo curada
├── TOOLS.md         # Configurações do ambiente
├── HEARTBEAT.md     # Checklist de verificações periódicas
├── USER.md          # Contexto sobre mim
├── scripts/         # Scripts Python (OAuth, posting, etc.)
└── memory/          # Logs diários (YYYY-MM-DD.md)
```

O sistema cria e atualiza esses arquivos automaticamente conforme você conversa com o agente. Ele aprende suas preferências, registra decisões, ajusta comportamentos.

### **Versionamento e refinamento com Claude Code**

Eu versiono essa pasta `clawd/` no GitHub. Por quê?

1. **Backup**: se algo der errado, volto para uma versão anterior
2. **Histórico**: vejo como o agente evoluiu ao longo do tempo
3. **Portabilidade**: posso replicar em outra máquina

Mas o passo que fez diferença real: **usei o Claude Code (com Opus 4.5) para revisar e melhorar a configuração** dos arquivos dessa pasta.

Abri a pasta no Claude Code e pedi para analisar inconsistências, redundâncias, oportunidades de melhoria. Ele encontrou:

- Arquivos referenciados que não existiam
- Duplicação de informação entre arquivos
- Configurações espalhadas em lugares errados
- Nomenclatura inconsistente

Corrigimos tudo em uma sessão. O resultado foi um agente mais inteligente, com memória melhor organizada e comportamento mais previsível. Esse tipo de otimização é chave para conseguir utilizar modelos de LLMs localmente.

É uma combinação poderosa: o Clawdbot (com LLMs Locais) gerencia a rotina diária, o Claude Code refina a arquitetura do sistema. Cada um no seu papel. Um ótimo custo benefício.

***

## **Quando NÃO usar o Clawdbot**

### **Tarefas que exigem julgamento crítico**

O agente é bom em executar. Não é bom em decidir coisas importantes. Emails sensíveis, decisões de negócio, comunicação com stakeholders — faça você mesmo.

### **Quando a latência importa**

Respostas levam alguns segundos. Se você precisa de resposta instantânea, não é o caso de uso.

### **Dados altamente sensíveis**

Mesmo auto-hospedado, você está dando acesso a um modelo de IA. Senhas, dados financeiros, informações confidenciais de clientes — mantenha separado.

### **Se você não tem tolerância para ajustes**

O setup inicial funciona. Mas para extrair valor real, você vai precisar ajustar configurações, corrigir comportamentos, refinar permissões. Se isso te irrita, talvez não seja para você.

### **Hardware limitado (para modelo local)**

Com 16GB de RAM, rodar modelo local + apps pesados simultaneamente causa problemas. Avalie se vale o trade-off ou se prefere pagar pela API.

***

## **O real poder: integração genuína na rotina**

Depois de algumas semanas usando, percebi que o valor não está nas features individuais. Está na integração.

O Clawdbot não é uma ferramenta que abro quando preciso. É um colega que está ali, acessível pelo WhatsApp, assumindo tarefas repetitivas:

- Verificar emails urgentes
- Lembrar de eventos próximos
- Postar conteúdo nas redes
- Organizar informações

Tarefas que eu faria de qualquer jeito, mas que consomem tempo e atenção. O agente libera esses recursos para o que realmente importa.

E quando ele age em meu nome publicamente, usa o prefixo \[molty\] ou \[clawdbot\]. Acredito na necessidade de transparência sobre o que é gerado por IA neste momento.

<figure>
<img src="/blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/6e2c231e53-954.webp" srcset="/blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/6e2c231e53-728.webp 728w, /blog/img/agente-ia-clawdbot-moltbot-configuracao-erros-acertos/6e2c231e53-954.webp 954w" sizes="(max-width: 760px) 100vw, 720px" width="954" height="1478" alt="Postagem do clawdbot em meu nome no x.com" loading="lazy" decoding="async">
<figcaption>Postagem do clawdbot em meu nome no x.com</figcaption>
</figure>

***

## **Conclusão**

O Clawdbot (em breve Moltbot) representa o que assistentes de IA deveriam ser: auto-hospedados, integrados à sua vida digital, sob seu controle.

Não é perfeito. Exige configuração. Tem custos (de API ou de hardware). Mas para quem quer automatizar tarefas repetitivas sem entregar seus dados para big tech, é a melhor opção disponível hoje.

Meu setup quebra várias recomendações oficiais. Funciona. Mas sei dos riscos. Faça suas escolhas conscientemente.

***

Aos meus amigos nerds, uma das coisas que mais me impressionou foi o quanto tudo isso me lembra a série de anime Ghost in the Shell.

***

*Guilherme Favaron é VP de Tecnologia e escreve sobre IA aplicada em [ia-aplicada.substack.com](https://ia-aplicada.substack.com/)*
