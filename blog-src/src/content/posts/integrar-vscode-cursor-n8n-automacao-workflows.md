---
title: "Integre o VS Code (ou Cursor) com o N8N: Simplifique a Criação e Edição de Automações com MCPs"
subtitle: "Versionamento, redundância, controle e compartilhamento de workflows usando ferramentas gratuitas"
description: "Aprenda a integrar o VS Code ou Cursor com o N8N usando MCP Server. Configure versionamento com Git, controle de fluxos com IA e automatize a criação de workflows. Guia completo e gratuito."
date: 2026-01-20T22:18:13.224Z
updated: 2026-01-20T22:23:43.092Z
slug: integrar-vscode-cursor-n8n-automacao-workflows
cover: img/integrar-vscode-cursor-n8n-automacao-workflows/capa.jpg
type: newsletter
---

Se você trabalha com automações no N8N, provavelmente já enfrentou situações onde perdeu um workflow por edição acidental, precisou recuperar uma versão anterior ou quis compartilhar fluxos complexos com sua equipe de forma organizada. A boa notícia é que existe uma solução elegante para todos esses problemas: **integrar seu editor de código favorito diretamente com sua instância N8N.**

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/9dd6b27364-1456.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/9dd6b27364-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/9dd6b27364-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="720" alt="" loading="lazy" decoding="async">
<figcaption>Integração do VS Code com N8N utilizando MCPs</figcaption>
</figure>

Neste artigo, vou te mostrar como conectar o **VS Code** (ou o **Cursor**, que é um fork focado em IA) à sua instância N8N usando o **MCP Server** (Model Context Protocol). Com essa integração, você poderá criar, editar, versionar e gerenciar seus workflows diretamente da sua IDE, aproveitando o poder de assistentes de IA como o Claude para acelerar seu trabalho.

Os benefícios práticos dessa configuração incluem:

- **Versionamento com Git**: Nunca mais perca um workflow. Cada alteração fica registrada no histórico.
- **Redundância**: Seus workflows ficam salvos tanto na instância quanto no repositório local.
- **Controle**: Revise alterações antes de aplicá-las, compare versões e faça rollback quando necessário.
- **Compartilhamento**: Exporte workflows como arquivos JSON e compartilhe facilmente com sua equipe.
- **Produtividade com IA**: Use assistentes como o Claude para criar workflows complexos por meio de linguagem natural.

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/615f45927d-1456.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/615f45927d-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/615f45927d-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="794" alt="Versionamento, Redundância, Controle e Comparitlhamento de Fluxos de automações no N8N" loading="lazy" decoding="async">
<figcaption>Versionamento, Redundância, Controle e Comparitlhamento de Fluxos de automações no N8N</figcaption>
</figure>

***

## Pré-requisitos

Antes de começarmos a configuração, certifique-se de que você possui os seguintes itens:

### Software necessário

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/02fdab65dd-1450.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/02fdab65dd-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/02fdab65dd-1450.webp 1450w" sizes="(max-width: 760px) 100vw, 720px" width="1450" height="538" alt="Requisitos necessários para integração VS Code e N8N via MCP" loading="lazy" decoding="async">
<figcaption>Requisitos necessários para integração VS Code e N8N via MCP</figcaption>
</figure>

### Conhecimentos recomendados

Você não precisa ser um programador experiente para seguir este tutorial, mas é importante ter familiaridade básica com:

- Navegação e edição de arquivos JSON (estrutura de chaves e valores)
- Uso do terminal ou linha de comando (comandos simples como `cd` e `mkdir`)
- Conceitos básicos de API REST e autenticação por tokens

Se você é um gestor ou profissional de negócios que quer aprender a usar python para análise de dados e criação de modelos de machine learning, recomendo o artigo ferramentas de programação com auxílio de IA, recomendo o artigo [Como Criar Modelos de Machine Learning a partir de Planilhas CSV](/blog/como-criar-um-modelo-de-machine-learning/), onde explico um caso real desde a criação de modelos de Machine Learning até a disponibilização deste modelo em ambiente de produção para previsão de resultados.

***

## Passo 1: Habilitar o MCP na Instância N8N

[O](/blog/mcp-model-context-protocol-revolucao-inteligencia-artificial/) **[MCP (Model Context Protocol)](/blog/mcp-model-context-protocol-revolucao-inteligencia-artificial/)** [é um protocolo que permite que assistentes de IA interajam com sistemas externos de forma padronizada](/blog/mcp-model-context-protocol-revolucao-inteligencia-artificial/). O N8N implementou suporte ao MCP a partir da versão 1.50.0, permitindo que você gerencie workflows por meio de ferramentas externas.

### 1.1. Acessando as configurações

Primeiro, faça login na sua instância N8N e siga estes passos:

1. Clique no ícone de **Settings** (engrenagem) no menu lateral esquerdo
2. No menu de configurações, localize e clique em **Instance-level MCP**

Essa opção permite habilitar o acesso MCP em nível de instância, ou seja, qualquer ferramenta configurada corretamente poderá interagir com seus workflows.

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/2739d82db0-1132.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/2739d82db0-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/2739d82db0-1132.webp 1132w" sizes="(max-width: 760px) 100vw, 720px" width="1132" height="570" alt="Ativação do Instance-level MCP (estou na versão 2.4.4)" loading="lazy" decoding="async">
<figcaption>Ativação do Instance-level MCP (estou na versão 2.4.4)</figcaption>
</figure>

### 1.2. Ativando o MCP Server

Na página de configuração do MCP, você encontrará um toggle (interruptor) chamado **“Enable MCP access”**. Ative-o clicando para que fique na posição verde (habilitado).

Após a ativação, a interface exibirá a **URL do MCP Server**. Essa URL terá um formato semelhante a:

```
https://sua-instancia.com/mcp-server/http
```

**Importante:** Anote essa URL em um local seguro. Você precisará dela no Passo 3 para configurar o VS Code.

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/58bbfd934a-1456.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/58bbfd934a-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/58bbfd934a-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="820" alt="Ativação do Instance-level MCP no N8N via Access Token" loading="lazy" decoding="async">
<figcaption>Ativação do Instance-level MCP no N8N via Access Token</figcaption>
</figure>

### 1.3. Verificando o status

Após habilitar, confirme que as seguintes informações estão visíveis na tela:

- **Status:** Enabled (Habilitado)
- **MCP Server URL:** URL completa do servidor
- **Access Tokens:** Seção para gerenciar tokens de acesso

Se todas essas informações estiverem disponíveis, o MCP foi habilitado com sucesso.

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/cb54481c94-1456.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/cb54481c94-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/cb54481c94-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="820" alt="Confirmação da ativação Instance-level MCP para N8N" loading="lazy" decoding="async">
<figcaption>Confirmação da ativação Instance-level MCP para N8N</figcaption>
</figure>

***

## Passo 2: Gerar Tokens de Acesso

Para que o VS Code possa se comunicar com sua instância N8N, você precisará gerar **dois tokens de autenticação** diferentes. Cada token tem uma finalidade específica:

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/906fbd2c15-1418.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/906fbd2c15-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/906fbd2c15-1418.webp 1418w" sizes="(max-width: 760px) 100vw, 720px" width="1418" height="440" alt="Tokens de acesso do VS Code ao N8N via MCP" loading="lazy" decoding="async">
<figcaption>Tokens de acesso do VS Code ao N8N via MCP</figcaption>
</figure>

Ter dois tokens distintos é uma boa prática de segurança, pois permite revogar um deles sem afetar o outro caso necessário.

### 2.1. Gerando o Token MCP (Access Token)

O Token MCP é utilizado especificamente para a comunicação entre o Claude Code (ou qualquer cliente MCP) e o servidor MCP do N8N.

**Passo a passo:**

1. Ainda na página **Settings \> Instance-level MCP**
2. Role até encontrar a seção **“Access Tokens”**
3. Clique no botão **“Create Access Token”** ou **“Generate New Token”**
4. Clique em **“Create”** para gerar o token (ver imagem do passo 1.3)

⚠️ **ATENÇÃO:** O token será exibido apenas uma vez. Copie-o imediatamente e salve em um local seguro. Se você perder esse token, precisará gerar um novo.

O token terá um formato JWT (JSON Web Token), parecido com este:

```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmNzgxYWJj...
```

Salve este token com o rótulo `MCP_ACCESS_TOKEN` para referência futura.

### 2.2. Gerando o Token API (N8N\_API\_KEY)

O Token API permite fazer chamadas diretas à API REST do N8N, o que é útil para operações avançadas e testes via terminal.

**Passo a passo:**

1. Ainda em **Settings**, navegue para a seção **API** ou **Public API**
2. Clique em **“Create API Key”** ou **“Generate API Key”**
3. Dê um nome descritivo (exemplo: `vscode-api-key`)
4. Configure as permissões. Recomendo **Full Access** para ter flexibilidade completa
5. Clique em **“Create”** para gerar a chave

Novamente, copie o token gerado imediatamente. O formato será similar ao do Token MCP.

Salve este token com o rótulo `N8N_API_KEY`.

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/f8e1750f5f-1456.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/f8e1750f5f-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/f8e1750f5f-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="1021" alt="Criação do N8N API Key" loading="lazy" decoding="async">
<figcaption>Criação do N8N API Key</figcaption>
</figure>

### Resumo dos tokens gerados

Ao final desta etapa, você deve ter dois tokens salvos em local seguro:

```
MCP_ACCESS_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
N8N_API_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

> **Dica de segurança:** Nunca compartilhe esses tokens publicamente nem os inclua em repositórios Git públicos. No próximo passo, vamos configurar o projeto para proteger essas credenciais.

***

## Passo 3: Configurar o Projeto no VS Code

Agora vamos criar a estrutura de arquivos necessária para que o VS Code (ou Cursor) se conecte à sua instância N8N. Essa configuração utiliza o padrão de projeto que permite tanto a integração MCP quanto o versionamento com Git.

### 3.1. Criando a estrutura de diretórios

Abra o VS Code e crie 3 arquivos:

```
.vscode
.claude
workflows (pasta)
```

Você pode criar a estrutura básica do projeto diretamente executando os seguintes comandos no terminal, dentro da pasta:

```
# Criar a pasta principal do projeto
mkdir -p meu-projeto-n8n/.vscode
mkdir -p meu-projeto-n8n/.claude
mkdir -p meu-projeto-n8n/workflows

# Entrar na pasta do projeto
cd meu-projeto-n8n
```

Essa estrutura organiza o projeto da seguinte forma:

- `.vscode/` - Configurações específicas do VS Code, incluindo o MCP
- `.claude/` - Instruções opcionais para o assistente de IA
- `workflows/` - Pasta para armazenar workflows exportados em JSON

### 3.2. Criando o arquivo de configuração MCP

O arquivo `mcp.json` é o coração da integração. Ele informa ao VS Code como se conectar ao servidor MCP do N8N.

**Crie o arquivo:** `.vscode/mcp.json`

```
{
  "mcpServers": {
    "n8n-mcp": {
      "type": "http",
      "url": "https://SUA-INSTANCIA.com/mcp-server/http",
      "headers": {
        "Authorization": "Bearer SEU_MCP_ACCESS_TOKEN_AQUI"
      },
      "description": "n8n MCP Server for workflow automation"
    }
  }
}
```

**Personalize o arquivo:**

Substitua os valores destacados pelos seus dados reais:

Placeholder Substitua por

https<!---->://SUA-INSTANCIA.com

A URL da sua instância N8N `SEU_MCP_ACCESS_TOKEN_AQUI` O Token MCP gerado no Passo 2.1

**Exemplo simulando valores reais:**

```
{
  "mcpServers": {
    "n8n-mcp": {
      "type": "http",
      "url": "https://automations.minhaempresa.com/mcp-server/http",
      "headers": {
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmNzgxYWJjNS1lODllLTRhOTUtYmMyMi1lMzQwMDYxZTI0MjEi..."
      },
      "description": "n8n MCP Server for workflow automation"
    }
  }
}
 
```

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/b282de5252-1456.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/b282de5252-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/b282de5252-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="625" alt="Estrutura do Projeto usando IDE Antigravity do Google" loading="lazy" decoding="async">
<figcaption>Estrutura do Projeto usando IDE Antigravity do Google</figcaption>
</figure>

### 3.3. Criando o arquivo de variáveis de ambiente

O arquivo `.env` armazena todas as credenciais sensíveis em um único local. Esse arquivo **nunca deve ser versionado no Git**.

**Crie o arquivo:** `.env`

```
# Token de API para chamadas REST diretas ao n8n
N8N_API_KEY=SEU_N8N_API_KEY_AQUI

# URL do MCP Server
SERVER_URL=https://SUA-INSTANCIA.com/mcp-server/http

# Token de acesso MCP (mesmo do mcp.json)
ACCESS_TOKEN=SEU_MCP_ACCESS_TOKEN_AQUI
```

**Personalize com seus valores reais**, substituindo os placeholders pelos tokens gerados no Passo 2.

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/126e79b50f-1456.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/126e79b50f-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/126e79b50f-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="676" alt="Configuração do arquivo .env para variáveis de ambiente" loading="lazy" decoding="async">
<figcaption>Configuração do arquivo .env para variáveis de ambiente</figcaption>
</figure>

### 3.4. Criando o arquivo .gitignore

O arquivo `.gitignore` é essencial para garantir que credenciais sensíveis não sejam acidentalmente enviadas para repositórios Git.

**Crie o arquivo:** `.gitignore`

```
# Credenciais sensíveis - NUNCA versionar
.env
.env.local
.env.*.local

# Configurações pessoais do VSCode (opcional)
.vscode/settings.json

# Node modules (se usar npm)
node_modules/

# Backups temporários de workflows
workflows/backup/
workflows/*.backup.json

# Arquivos do sistema operacional
.DS_Store
Thumbs.db
Desktop.ini

# Logs
*.log
npm-debug.log*
yarn-debug.log*
yarn-error.log*

# Arquivos temporários
*.tmp
*.temp
.cache/
```

Como fica no VS Code (Cursor, Antigravity, Trae…)

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/f6aa5f4206-1456.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/f6aa5f4206-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/f6aa5f4206-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="1158" alt="Configuração do .gitignore para integração do VS Code com N8N via MCP" loading="lazy" decoding="async">
<figcaption>Configuração do .gitignore para integração do VS Code com N8N via MCP</figcaption>
</figure>

### 3.5. Criando instruções para o assistente de IA (opcional)

Se você utiliza o Claude Code ou outra extensão de IA, pode criar um arquivo de instruções que ajuda o assistente a entender melhor o contexto do projeto.

**Crie o arquivo:** `.claude/instructions.md`

```
# n8n Workflow Development with Claude Code

## Contexto
Este projeto contém workflows do n8n gerenciados via MCP Server.
Use as ferramentas MCP disponíveis para criar, editar e gerenciar workflows.

## Processo de Desenvolvimento

### Fase 1: Discovery (Descoberta)
1. Use ferramentas MCP para buscar nodes relevantes para a tarefa
2. Revise as propriedades e operações disponíveis em cada node

### Fase 2: Validation (Validação)
1. Valide configurações ANTES de construir o workflow
2. Verifique campos obrigatórios e tipos de dados esperados

### Fase 3: Building (Construção)
1. Crie a estrutura do workflow com os nodes necessários
2. Conecte os nodes na ordem correta
3. Prefira nodes nativos do n8n ao invés de Code nodes quando possível

### Fase 4: Deployment (Implantação)
1. Salve o workflow na instância n8n
2. Teste a execução manualmente
3. Verifique os outputs de cada node

## Melhores Práticas
- Sempre valide a estrutura antes de construir
- Documente lógica complexa com comentários
- Nomeie workflows e nodes de forma descritiva
- Teste workflows antes de ativar em produção
```

Como fica na IDE:

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/38e9c88082-1456.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/38e9c88082-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/38e9c88082-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="987" alt="Instruções para LLM generativa compreender suas atribuições / skills no diretório" loading="lazy" decoding="async">
<figcaption>Instruções para LLM generativa compreender suas atribuições / skills no diretório</figcaption>
</figure>

### 3.6. Estrutura final do projeto

Após criar todos os arquivos, seu projeto deve ter esta estrutura:

```
meu-projeto-n8n/
├── .vscode/
│   └── mcp.json              # Configuração do MCP Server
├── .claude/
│   └── instructions.md       # Instruções para IA (opcional)
├── workflows/                # Workflows exportados em JSON
├── .env                      # Credenciais (NÃO versionar)
├── .gitignore                # Arquivos a ignorar no Git
└── README.md                 # Documentação do projeto
 
```

Para acessar o template de uma estrutura final de projeto, acesse abaixo:

[Baixar Template (códigos)](https://github.com/guifav/vscode-n8n-template)

***

## Passo 4: Validar a Conexão

Com todos os arquivos configurados, é hora de testar se a integração está funcionando corretamente.

### 4.1. Reiniciar o VS Code

Para que as configurações do MCP sejam carregadas, você precisa reiniciar completamente o VS Code:

1. Feche **todas** as janelas do VS Code
2. Abra o VS Code novamente
3. Navegue até a pasta do projeto que você criou (`meu-projeto-n8n`)
4. Aguarde alguns segundos para que as extensões carreguem

### 4.2. Verificar o MCP Server no Claude Code

Abra o painel do Claude Code (ou a extensão de IA que você utiliza) e digite o seguinte comando:

```
Liste as ferramentas MCP disponíveis
```

Se a configuração estiver correta, você verá uma lista de ferramentas relacionadas ao N8N, como:

- Buscar nodes disponíveis
- Criar workflows
- Editar workflows existentes
- Validar configurações
- Listar workflows da instância

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/95adebe384-1110.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/95adebe384-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/95adebe384-1110.webp 1110w" sizes="(max-width: 760px) 100vw, 720px" width="1110" height="640" alt="Listagem de ferramentas disponíveis no VS Code via MCP para N8N" loading="lazy" decoding="async">
<figcaption>Listagem de ferramentas disponíveis no VS Code via MCP para N8N</figcaption>
</figure>

### 4.3. Testando a listagem de workflows

Para confirmar que a conexão com sua instância está funcionando, peça ao assistente:

```
Liste todos os workflows da minha instância n8n
```

O Claude (ou outro assistente) deve retornar uma lista dos workflows existentes na sua instância, incluindo seus nomes e IDs.

### 4.4. Testando a criação de um workflow simples

Para um teste mais completo, peça ao assistente para criar um workflow de teste:

```
Crie um workflow de teste simples no n8n com:
1. Um Manual Trigger (gatilho manual)
2. Um Set node que adicione o campo "mensagem" com valor "Hello from VS Code!"
```

Se tudo estiver funcionando, o assistente criará o workflow e confirmará que ele foi salvo na instância. Você pode verificar diretamente no N8N acessando a lista de workflows.

### 4.5. Testando via API REST (terminal)

Para validar que a API REST também está funcionando, você pode fazer um teste rápido via terminal:

```
# No Linux/Mac, carregue as variáveis do .env
source .env

# Faça uma requisição para listar workflows
curl -s -X GET "https://SUA-INSTANCIA.com/api/v1/workflows" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -H "Content-Type: application/json" | jq
```

Substitua `SUA-INSTANCIA.com` pela URL real da sua instância. O comando retornará um JSON com a lista de workflows.

> **Nota:** Se você não tem o `jq` instalado, pode remover `| jq` do comando. O `jq` apenas formata o JSON para facilitar a leitura.

***

## Exemplos Práticos de Uso

Agora que sua integração está funcionando, veja alguns exemplos de como você pode usar o Claude Code (ou outro assistente) para gerenciar seus workflows.

### Exemplo 1: Listar workflows ativos

```
Claude, liste todos os workflows ativos na minha instância n8n, 
mostrando o nome e a data da última modificação
```

### Exemplo 2: Criar um workflow de integração

```
Crie um workflow no n8n que:
1. Receba dados via webhook HTTP (método POST)
2. Use um Set node para adicionar um timestamp automático
3. Valide se o campo "email" existe no payload
4. Se válido, salve os dados em uma planilha Google Sheets
5. Se inválido, envie uma notificação para um canal do Slack
```

### Exemplo 3: Editar um workflow existente

```
Encontre o workflow chamado "Processamento de Leads" e faça as seguintes alterações:
- Adicione validação de email antes do envio
- Inclua um node de notificação no Slack em caso de erro
- Adicione um log de execução no final do fluxo
```

### Exemplo 4: Buscar nodes disponíveis

```
Quais nodes do n8n estão disponíveis para integração com:
- Google Sheets
- Slack
- Notion
- Airtable

Liste as principais operações de cada um.
```

### Exemplo 5: Validar configuração de workflow

```
Analise o workflow "Sincronização de Contatos" e verifique:
- Se todos os nodes têm as credenciais necessárias configuradas
- Se há tratamento de erros adequado
- Se os campos obrigatórios estão preenchidos
- Sugira melhorias de performance
```

### Exemplo 6: Exportar workflow para versionamento

```
Exporte o workflow "Automação de Emails" para um arquivo JSON 
na pasta workflows/ do projeto, com o nome automacao-emails.json
```

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/8e69c408be-1456.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/8e69c408be-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/8e69c408be-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="720" alt="Edição e formatação de json objects pela IA" loading="lazy" decoding="async">
<figcaption>Edição e formatação de json objects pela IA</figcaption>
</figure>

***

## Resolução de Problemas Comuns

Durante a configuração, você pode encontrar alguns problemas. Aqui estão as soluções para os mais comuns:

### Problema 1: Claude Code não encontra o MCP Server

**Sintomas:** O assistente não lista ferramentas MCP ou exibe erro “MCP server not found”.

**Soluções:**

1. Verifique se o arquivo `.vscode/mcp.json` existe e está com JSON válido
2. Confirme que a URL e o token estão corretos (sem espaços extras)
3. Reinicie completamente o VS Code (feche todas as janelas)
4. Verifique se a extensão Claude Code está atualizada
5. Teste a URL do MCP Server manualmente no terminal:

```
curl -s -X GET "https://SUA-INSTANCIA.com/mcp-server/http" \
  -H "Authorization: Bearer SEU_TOKEN_MCP"
```

### Problema 2: Erro de autenticação (401 ou 403)

**Sintomas:** Mensagens como “401 Unauthorized”, “403 Forbidden” ou “Invalid token”.

**Soluções:**

1. **Token expirado:** Gere um novo token em Settings \> Instance-level MCP
2. **Token incompleto:** Verifique se copiou o token inteiro, sem espaços
3. **MCP desabilitado:** Confirme que o toggle está na posição “Enabled”
4. **Permissões:** Verifique se seu usuário tem permissões de administrador
5. **Regenere ambos os tokens** e atualize os arquivos de configuração

### Problema 3: Workflows não aparecem na listagem

**Sintomas:** O Claude retorna lista vazia ou erro ao listar workflows.

**Soluções:**

1. Confirme que existem workflows criados na instância
2. Verifique as permissões do usuário associado ao token
3. Teste a API REST diretamente:

```
curl -X GET "https://SUA-INSTANCIA.com/api/v1/workflows" \
  -H "X-N8N-API-KEY: SEU_API_KEY"
```

4. Se a API funcionar mas o MCP não, regenere especificamente o token MCP

### Problema 4: Erro ao criar workflows

**Sintomas:** Mensagens como “request/body/active is read-only” ou “Invalid workflow schema”.

**Soluções:**

1. **Campo** `active`**:** Não inclua este campo ao criar workflows via API
2. **Campos obrigatórios:** Garanta que `name`, `nodes` e `connections` existam
3. **IDs únicos:** Cada node precisa ter um `id` único no workflow
4. **Formato correto:** Use o formato `n8n-nodes-base.nodeType` para tipos de nodes
5. Peça ao assistente para validar a estrutura antes de criar

***

## Checklist de Configuração Completa

Use este checklist para garantir que tudo está configurado corretamente:

<figure>
<img src="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/34e93dc9d4-1406.webp" srcset="/blog/img/integrar-vscode-cursor-n8n-automacao-workflows/34e93dc9d4-728.webp 728w, /blog/img/integrar-vscode-cursor-n8n-automacao-workflows/34e93dc9d4-1406.webp 1406w" sizes="(max-width: 760px) 100vw, 720px" width="1406" height="752" alt="Check list para verificação se integração VS Code e N8N via MCP está funcionando" loading="lazy" decoding="async">
<figcaption>Check list para verificação se integração VS Code e N8N via MCP está funcionando</figcaption>
</figure>

***

## Boas Práticas de Segurança

Ao trabalhar com tokens de API e automações, é fundamental seguir boas práticas de segurança:

### Proteção de credenciais

1. **Nunca versione o** `.env`**:** Sempre inclua no `.gitignore`
2. **Use variáveis de ambiente:** Não coloque tokens diretamente no código
3. **Rotacione tokens periodicamente:** Regenere a cada 3-6 meses
4. **Tokens dedicados:** Use um token diferente para cada projeto ou ambiente
5. **Permissões mínimas:** Conceda apenas as permissões necessárias

### Controle de acesso

1. **Usuários dedicados:** Crie usuários específicos para integrações via API
2. **Auditoria:** Monitore logs de acesso via API regularmente
3. **Revogação imediata:** Revogue tokens comprometidos imediatamente
4. **2FA habilitado:** Use autenticação de dois fatores na instância N8N

### Backup e versionamento

1. **Versione workflows:** Salve workflows na pasta `workflows/` como JSON
2. **Use Git:** Rastreie mudanças nos workflows com commits descritivos
3. **Backup regular:** Exporte workflows importantes periodicamente
4. **Documentação:** Mantenha um README.md atualizado com descrição dos workflows

> **Leitura complementar:** Para entender melhor como implementar IA de forma segura e estratégica em processos corporativos, recomendo o livro [Desbloqueando a Inteligência Artificial nas Empresas](/blog/livro-ia-inteligencia-artificial-portugues-gratuito/).

***

**Prompt para imagem ilustrativa (segurança):**

> Minimalist security concept on white background, black outline padlock icon with #ffbe00 shield badge, connected to document and cloud icons by dotted lines, clean vector illustration style

***

## Conclusão

Integrar o VS Code (ou Cursor) com o N8N abre um novo mundo de possibilidades para gerenciar suas automações. Com essa configuração, você ganha:

- **Produtividade:** Crie workflows complexos usando linguagem natural com assistentes de IA
- **Controle de versão:** Histórico completo de alterações com Git
- **Colaboração:** Compartilhe workflows facilmente com sua equipe
- **Segurança:** Backup redundante e rastreabilidade de mudanças
- **Flexibilidade:** Trabalhe no ambiente que você já conhece e domina

Essa abordagem é especialmente relevante para equipes que gerenciam múltiplos workflows ou que precisam manter padrões de qualidade e documentação em suas automações.

O [MCP (Model Context Protocol)](/blog/mcp-model-context-protocol-revolucao-inteligencia-artificial/) está se tornando um padrão importante para integração entre ferramentas de IA e sistemas externos. Ao dominar essa tecnologia agora, você estará preparado para aproveitar as evoluções futuras do ecossistema, sem configurações adicionais.

Se você quer se aprofundar mais no universo da automação e inteligência artificial aplicada aos negócios, convido você a conhecer o [MindApps.ai](https://mindapps.ai/index.html), meu laboratório de inovação em IA, onde desenvolvo aplicativos práticos que demonstram casos de uso reais dessa tecnologia.

***

## Referências e Recursos

### Documentação oficial

- [N8N MCP Server Documentation](https://docs.n8n.io/advanced-ai/accessing-n8n-mcp-server/)
- [N8N Public API Documentation](https://docs.n8n.io/api/api-reference/)
- [N8N Workflow JSON Structure](https://docs.n8n.io/workflows/export-import/)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)

### Repositórios GitHub

- [n8n-io/n8n](https://github.com/n8n-io/n8n) - Repositório oficial do N8N

### Tutoriais adicionais

- [N8N MCP Workflow Template](https://n8n.io/workflows/3770-build-your-own-n8n-workflows-mcp-server/)
- [Fórum da Comunidade N8N](https://community.n8n.io/)

***

## Links Internos Sugeridos

Para complementar seu aprendizado, confira estes outros artigos do blog:

- [Guia Completo de Inteligência Artificial para Iniciantes](/blog/inteligencia-artificial-guia-completo-para-iniciantes-e-entusiastas/)
- [O Papel dos Analistas de Negócios na Era da IA Generativa](/blog/inteligencia-artificial-trabalho/)
- [Tutorial de Análise de Dados com IA para Gestores](/blog/tutorial-analise-de-dados-colab-python-ia/)

***

*Última atualização: Janeiro de 2026*

*Compatibilidade testada: N8N v1.50.0+, VS Code 1.85+, Cursor (fork VS Code), Claude Code (versão mais recente)*
