---
title: "MCP (Model Context Protocol): o que é e como funciona"
description: "Entenda o MCP (Model Context Protocol): arquitetura cliente-servidor, Resources, Tools e Prompts, exemplo de código, riscos e quando usar em empresas."
summary: "O Model Context Protocol (MCP) é um padrão aberto em arquitetura cliente-servidor que conecta modelos e agentes de IA a dados e sistemas externos sem integrações sob medida para cada ferramenta. Um servidor MCP expõe três primitivos (Resources para leitura, Tools para ações e Prompts para fluxos reutilizáveis) e funciona em múltiplos clientes além do Claude."
subtitle: "Arquitetura cliente-servidor, primitivos, exemplo de código em Python e critérios de segurança para conectar LLMs a sistemas corporativos."
date: 2025-03-31T03:00:00.000Z
updated: 2025-11-24T20:06:10.226Z
slug: mcp-model-context-protocol-revolucao-inteligencia-artificial
cover: img/mcp-model-context-protocol-revolucao-inteligencia-artificial/capa.jpg
type: newsletter
topics: [agentes-de-ia]
---

Modelos de linguagem como Claude, ChatGPT e Gemini mudaram a forma como processamos texto e código, mas esbarram em um limite estrutural quando trabalham isolados: eles precisam de um padrão consistente para ler dados externos e acionar ferramentas de negócio. O Model Context Protocol (MCP) surgiu para resolver exatamente essa camada de integração.

<figure>
<img src="/blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/60be518083-1312.webp" srcset="/blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/60be518083-728.webp 728w, /blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/60be518083-1312.webp 1312w" sizes="(max-width: 760px) 100vw, 720px" width="1312" height="736" alt="MCP (Model Context Protocol) representation" loading="lazy" decoding="async">
<figcaption>MCP (Model Context Protocol)</figcaption>
</figure>

Neste artigo, explico os conceitos fundamentais do MCP, o problema que ele resolve em relação ao function calling tradicional, sua arquitetura cliente-servidor, um exemplo prático em Python e os critérios de segurança ao expor dados corporativos por meio do protocolo.

## O que é o MCP (Model Context Protocol) e por que ele importa?

O Model Context Protocol (MCP) é um protocolo aberto que padroniza a forma como aplicações fornecem contexto aos LLMs. Em termos simples, podemos pensar no MCP como uma "porta USB-C para aplicações de IA". Assim como o USB-C fornece uma maneira padronizada de conectar dispositivos a diversos periféricos e acessórios, o MCP proporciona uma forma padronizada de conectar modelos de IA a diferentes fontes de dados e ferramentas.

Criado originalmente pela Anthropic (desenvolvedora do Claude) e hoje adotado por múltiplos clientes, editores de código e provedores de IA, o MCP resolve um limite fundamental dos LLMs: por si só, eles geram texto fluente e demonstram compreensão linguística, mas sem integrações externas continuam incapazes de enviar um e-mail, consultar informações atualizadas ou interagir com APIs corporativas.

O MCP fornece uma camada intermediária padronizada para que modelos e agentes acessem ferramentas e serviços externos de maneira consistente, reutilizável e auditável.

## Qual limitação dos LLMs o MCP resolve que o function calling tradicional não resolvia?

### LLMs são essencialmente preditores de texto

Em sua essência, um LLM é um modelo matemático treinado para prever o próximo token (palavra ou parte de palavra) em uma sequência. Por exemplo, se você digitar "Meu Grande Gordo Casamento", o modelo prevê que a próxima palavra será provavelmente "Grego". Essa capacidade de previsão de texto é útil para linguagem, mas sozinha não executa operações fora da conversa.

### Sem conexão nativa com o mundo externo

Os LLMs ficam restritos ao conhecimento adquirido durante o treinamento e ao contexto da conversa atual. Sem ferramentas externas, ficam impedidos de navegar na web, consultar bancos de dados em tempo real, chamar APIs ou acionar sistemas corporativos.

### A evolução: LLMs + ferramentas e o gargalo da integração ponto a ponto

A primeira resposta a essas limitações foi conectar LLMs a ferramentas específicas via function calling ou plugins proprietários. Cada ferramenta adicionava uma capacidade pontual:

- Pesquisa na web
- Acesso a dados em tempo real
- Leitura de arquivos
- Interação com APIs específicas

Essa abordagem de "colar" ferramentas em cada aplicativo de IA gerou quatro problemas recorrentes:

1. **Complexidade técnica**: cada par cliente-ferramenta exigia código personalizado.
2. **Inconsistência**: cada fornecedor implementava schemas, autenticação e tratamento de erro de um jeito diferente.
3. **Baixa escalabilidade**: manter dezenas de integrações duplicadas entre vários clientes e modelos se tornava inviável.
4. **Manutenção cara**: qualquer mudança em uma API externa quebrava várias integrações ao mesmo tempo.

A tabela abaixo compara onde o MCP entra em relação ao function calling tradicional e ao RAG:

| Critério | MCP | Function Calling / API sob medida | RAG |
|---|---|---|---|
| Papel principal | Protocolo aberto padronizado para expor dados, ferramentas e prompts a qualquer cliente compatível | Definição proprietária de funções dentro de uma aplicação específica | Recuperação de trechos relevantes de documentos para enviar ao contexto do modelo |
| Reuso entre modelos e clientes | Alto: o mesmo servidor MCP conecta Claude, IDEs e outros clientes compatíveis | Baixo: exige adaptar esquema e cola de integração para cada cliente ou app | Médio: o índice é reutilizável, mas a lógica de consulta vive no pipeline |
| Dados em tempo real | Sim, via chamadas diretas a APIs e bancos por meio do servidor MCP | Sim, conforme os endpoints codificados na aplicação | Limitado à frequência de reindexação da base documental |
| Execução de ações | Sim, por meio de Tools com validação e aprovação | Sim, executando funções definidas no código da aplicação | Focado em leitura e recuperação de contexto |
| Esforço de manutenção | Centralizado no servidor MCP de cada sistema | Multiplicado por cada integração e cada cliente que consome a API | Concentrado em ingestão, chunking, embeddings e atualização de índice |

## Como funciona a arquitetura Cliente-Servidor do MCP?

O MCP segue uma arquitetura cliente-servidor que estabelece comunicação padronizada entre aplicações de IA e servidores que expõem recursos ou funcionalidades.

### Componentes essenciais do ecossistema MCP

1. **Hosts**: programas como Claude Desktop, IDEs ou ferramentas de IA que precisam acessar dados por meio do MCP. São os aplicativos com os quais o usuário interage diretamente, como:
   1. O aplicativo Claude Desktop no computador
   2. Um editor de código como VS Code ou Cursor com extensões de IA
   3. Aplicativos de produtividade com assistente integrado
2. **Clientes MCP**: componentes dentro do host que mantêm conexões 1:1 com servidores. Funcionam como adaptadores do protocolo que:
   - Transmitem solicitações para os servidores corretos
   - Recebem as respostas estruturadas e as devolvem ao host
   - Gerenciam negociação de capacidades e ciclo de vida da sessão
3. **Servidores MCP**: programas leves que expõem capacidades específicas pelo protocolo MCP padronizado, como:
   - Um servidor meteorológico que busca previsões do tempo
   - Um servidor para Google Drive que lê documentos autorizados
   - Um servidor GitHub que consulta código, issues e commits
   - Um servidor de e-mail ou CRM que consulta e registra interações
4. **Fontes de dados locais**: arquivos, bancos de dados e serviços do computador que os servidores MCP acessam com segurança, como documentos locais, planilhas ou um banco SQLite.
5. **Serviços remotos**: sistemas externos acessíveis pela internet (via APIs) aos quais os servidores MCP se conectam, como serviços de clima, plataformas de código ou um CRM corporativo na nuvem.

<figure>
<img src="/blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/b70a2ac33f-1020.webp" srcset="/blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/b70a2ac33f-728.webp 728w, /blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/b70a2ac33f-1020.webp 1020w" sizes="(max-width: 760px) 100vw, 720px" width="1020" height="756" alt="Componentes Essenciais do Ecossistema MCP" loading="lazy" decoding="async">
<figcaption>Componentes Essenciais do Ecossistema MCP</figcaption>
</figure>

### Fluxo de comunicação

O fluxo típico no MCP segue seis passos:

1. O cliente envia a pergunta do usuário ao LLM junto com a lista de ferramentas disponíveis.
2. O LLM analisa as ferramentas e decide qual acionar.
3. O cliente executa a ferramenta escolhida por meio do servidor MCP.
4. Os resultados voltam estruturados ao LLM.
5. O LLM formula a resposta em linguagem natural com base no retorno real.
6. A resposta é exibida ao usuário.

<figure>
<img src="/blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/17e45cb7eb-1127.webp" srcset="/blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/17e45cb7eb-728.webp 728w, /blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/17e45cb7eb-1127.webp 1127w" sizes="(max-width: 760px) 100vw, 720px" width="1127" height="594" alt="Fluxo de Comunicação - MCP" loading="lazy" decoding="async">
<figcaption>Fluxo de Comunicação - MCP</figcaption>
</figure>

### Camadas do protocolo

O protocolo MCP opera em três camadas principais:

- **Camada de Protocolo**: define o formato das mensagens (JSON-RPC), vinculação de solicitação/resposta e padrões de comunicação.
- **Camada de Transporte**: gerencia a comunicação real entre clientes e servidores via `stdio` (entrada/saída padrão para servidores locais) ou HTTP remoto (SSE / Streamable HTTP para servidores hospedados).
- **Tipos de Mensagens**: inclui Solicitações (que esperam resposta), Resultados (respostas bem-sucedidas), Erros (falhas estruturadas) e Notificações (mensagens unidirecionais).

<figure>
<img src="/blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/2799574118-995.webp" srcset="/blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/2799574118-728.webp 728w, /blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/2799574118-995.webp 995w" sizes="(max-width: 760px) 100vw, 720px" width="995" height="347" alt="Camadas do Protocolo MCP" loading="lazy" decoding="async">
<figcaption>Camadas do Protocolo MCP</figcaption>
</figure>

### Ciclo de vida da conexão

Uma conexão MCP segue três etapas:

1. **Inicialização**: o cliente envia versão e capacidades, o servidor responde com as capacidades suportadas e o cliente confirma.
2. **Troca de mensagens**: solicitações, respostas e notificações fluem entre cliente e servidor.
3. **Encerramento**: a conexão é fechada de forma limpa ou interrompida em condição de erro.

## Quais capacidades um servidor MCP expõe (Resources, Tools e Prompts)?

Um servidor MCP organiza tudo o que oferece em três primitivos. Separar quem controla cada primitivo facilita entender onde ficam leitura, execução e padronização de fluxo:

| Primitivo | Quem controla | Para que serve | Exemplo corporativo |
|---|---|---|---|
| Resources | Aplicação / cliente | Expor dados somente leitura identificados por URI para dar contexto ao modelo | Ler política comercial vigente, schema de banco ou registro de cliente no CRM |
| Tools | Modelo (com aprovação do usuário ou política) | Executar funções, consultas parametrizadas ou ações externas | Consultar pedidos do trimestre, abrir ticket de suporte ou gerar relatório |
| Prompts | Usuário | Oferecer templates reutilizáveis que padronizam tarefas recorrentes | Comando pronto de revisão de contrato, fechamento mensal ou triagem de incidente |

### 1. Recursos (Resources)

Dados semelhantes a arquivos que podem ser lidos por clientes, como respostas de API, registros estruturados ou conteúdo de documentos. Os recursos permitem que o LLM consulte informações externas sem alterar estado.

### 2. Ferramentas (Tools)

Funções que podem ser chamadas pelo LLM (com aprovação do usuário ou política explícita). As ferramentas permitem ao modelo realizar consultas e ações concretas, como:

- Pesquisar dados em tempo real
- Escrever em arquivos autorizados
- Enviar e-mails ou mensagens
- Acessar APIs externas
- Realizar cálculos em código
- Criar visualizações

<figure>
<img src="/blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/0957937bf8-960.webp" srcset="/blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/0957937bf8-728.webp 728w, /blog/img/mcp-model-context-protocol-revolucao-inteligencia-artificial/0957937bf8-960.webp 960w" sizes="(max-width: 760px) 100vw, 720px" width="960" height="767" alt="Capacidade aprimoradas do MCP - Expansão das funcionalidades, ferramentas, tools" loading="lazy" decoding="async">
<figcaption>Capacidade aprimoradas do MCP - Expansão das funcionalidades, ferramentas, tools</figcaption>
</figure>

### 3. Prompts

Templates pré-escritos expostos pelo próprio servidor para ajudar usuários a disparar fluxos recorrentes com consistência, evitando que cada pessoa reinvente instruções do zero.

## Como implementar um servidor MCP na prática?

Para ilustrar como o MCP funciona na prática, veja o exemplo de um servidor MCP em Python que fornece informações meteorológicas.

### Exemplo: servidor meteorológico MCP

Este servidor expõe duas ferramentas principais:

1. **get-alerts**: obtém alertas meteorológicos para um estado dos EUA.
2. **get-forecast**: obtém previsão do tempo para uma localização específica.

```python
from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP

# Inicializa servidor FastMCP
mcp = FastMCP("weather")

# Constantes
NWS_API_BASE = "https://api.weather.gov"
USER_AGENT = "weather-app/1.0"

@mcp.tool()
async def get_alerts(state: str) -> str:
    """Obtém alertas meteorológicos para um estado dos EUA.
    
    Args:
        state: Código de duas letras do estado dos EUA (ex: CA, NY)
    """
    url = f"{NWS_API_BASE}/alerts/active/area/{state}"
    data = await make_nws_request(url)
    # Processar e formatar os dados...
    return formatted_alerts

@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """Obtém previsão do tempo para uma localização.
    
    Args:
        latitude: Latitude da localização
        longitude: Longitude da localização
    """
    # Obter e processar dados de previsão do tempo...
    return formatted_forecast

# Inicializa e executa o servidor
mcp.run(transport='stdio')
```

### Conectando ao cliente

Para usar este servidor, um cliente MCP (como o [Claude Desktop](https://claude.ai/download) ou um editor compatível) precisa apontar para o comando no arquivo de configuração:

```json
{
  "mcpServers": {
    "weather": {
      "command": "python",
      "args": ["/caminho/para/weather.py"]
    }
  }
}
```

### Interação com o usuário

Uma vez configurado, o usuário pode fazer perguntas como:

- "Qual é o clima no Rio de Janeiro?"
- "Existem alertas de clima severo em São Sebastião?"

O cliente analisa a pergunta, seleciona a ferramenta adequada (`get-forecast` ou `get-alerts`), chama o servidor MCP e devolve a resposta em linguagem natural usando os dados reais retornados.

### Como começar com o MCP

- **Para usuários**: o caminho mais rápido é testar servidores MCP prontos em clientes com suporte integrado, como Claude Desktop, Claude Code, Cursor ou VS Code, começando por conectores somente leitura.
- **Para desenvolvedores**: comece pela documentação oficial do protocolo (`modelcontextprotocol.io`), escolha um SDK oficial (Python, TypeScript/Node.js, Java, Kotlin ou C#) e construa primeiro um servidor pequeno com uma ou duas ferramentas de leitura antes de expor ações de escrita.

## Quais são os ganhos e os riscos de segurança ao expor dados corporativos via MCP?

O ecossistema MCP amadureceu rápido desde a especificação inicial e hoje oferece conectores oficiais e comunitários para bancos de dados, GitHub, sistemas de CRM, ferramentas de observabilidade e suítes corporativas. Na empresa, porém, ganho de integração e risco operacional andam juntos.

### Principais ganhos na operação

1. **Acesso a dados em tempo real**: modelos passam a consultar cotações, pedidos, chamados e bases internas atualizadas sem esperar reindexações.
2. **Automação de fluxos entre sistemas**: um mesmo assistente pode cruzar informações de múltiplas fontes, analisar e-mails ou repositórios e preparar entregas integradas.
3. **Interoperabilidade**: um servidor MCP construído para um sistema interno pode ser reaproveitado por diferentes clientes e modelos compatíveis, reduzindo retrabalho de integração.
4. **Aplicações especializadas por área**: times de engenharia (análise de código, debugging, CI/CD), dados (consultas e visualizações) e operações ganham interfaces padronizadas sobre ferramentas que já existem.

### Riscos de segurança e limites em ambiente corporativo

Expor dados da empresa por MCP exige os mesmos rigores de qualquer API interna, somados ao comportamento probabilístico do modelo:

1. **Excesso de permissão e exposição de banco de produção**: ligar um servidor MCP com credenciais amplas direto ao banco transacional abre margem para consultas pesadas, vazamento de colunas sensíveis e alterações indevidas.
2. **Controle de acesso por usuário**: o servidor MCP precisa respeitar a permissão real de quem faz a pergunta (RBAC/ACL), operando com credenciais por perfil em vez de uma chave única que enxerga dados de toda a organização.
3. **Injeção de prompt via dados externos**: textos lidos de e-mails, tickets ou documentos podem conter instruções maliciosas tentando induzir o modelo a chamar outra ferramenta MCP de escrita ou exfiltração.
4. **Complexidade de manutenção e auditoria**: sistemas com cliente, servidor MCP e APIs externas exigem logs centralizados de cada chamada, versionamento de contrato e limites claros de taxa e timeout.

Quando a empresa precisa de regras fechadas de negócio, cálculos auditáveis e garantias rígidas de saída para diretoria, uma aplicação controlada atende melhor do que expor ferramentas soltas ao modelo. Analiso essa fronteira em detalhes no artigo [MCP aberto ou aplicação: como colocar dados da empresa na IA](/blog/mcp-ou-aplicacao-dados-da-empresa-na-ia/).

## Perguntas frequentes sobre o Model Context Protocol (MCP)

### O MCP funciona só com o Claude ou com outros modelos e IDEs?

O MCP é um protocolo aberto. Embora tenha sido criado pela Anthropic e popularizado no Claude Desktop e no Claude Code, hoje conta com suporte em múltiplos clientes, editores de código (como VS Code e Cursor) e ecossistemas de agentes de diferentes fornecedores. Um servidor MCP bem escrito atende vários hosts sem reescrever a integração.

### MCP substitui RAG ou eles funcionam juntos?

Eles cumprem papéis complementares. O RAG busca trechos relevantes em coleções de documentos não estruturados (PDFs, manuais, contratos), enquanto o MCP padroniza como o modelo acessa ferramentas, APIs e dados vivos. Em arquiteturas corporativas, é comum expor a própria busca RAG como uma ferramenta dentro de um servidor MCP, ao lado de consultas estruturadas a sistemas internos.

### É seguro ligar um servidor MCP direto ao banco de dados de produção da empresa?

Conectar direto ao banco transacional de produção com credenciais amplas traz risco operacional e de segurança. O desenho seguro usa uma réplica de leitura ou camada semântica, usuário estritamente `Read-Only`, views que ocultam colunas sensíveis sob a LGPD, limite de linhas, timeout curto e auditoria de todas as consultas executadas.

### Quando construir um servidor MCP próprio e quando construir uma aplicação com regras fechadas?

Construa um servidor MCP quando o objetivo for dar a usuários técnicos ou agentes flexíveis acesso reutilizável a ferramentas e dados em múltiplos clientes (IDEs, assistentes corporativos, fluxos exploratórios). Construa uma aplicação própria com regras fechadas quando a operação exigir fluxo determinístico, cálculos auditáveis em código, permissão granular por tela e controle rígido sobre cada número entregue ao usuário. Veja a comparação completa em [MCP aberto ou aplicação](/blog/mcp-ou-aplicacao-dados-da-empresa-na-ia/).

***

*Este artigo foi originalmente produzido em março de 2025 com base na especificação do Model Context Protocol e atualizado em estrutura e tabelas comparativas para refletir a adoção do protocolo em ambientes corporativos.*

Link para Referência: [https<!---->://docs.anthropic.com/en/docs/agents-and-tools/mcp](https://docs.anthropic.com/en/docs/agents-and-tools/mcp)
