---
title: "Estudo de Caso: Recuperação de VPS Comprometida por Cryptojacking"
subtitle: "Como identificamos um minerador de criptomoedas escondido, reinstalamos o servidor do zero e colocamos a aplicação no ar em 4 horas"
description: "Como identificamos um minerador de criptomoedas escondido, reinstalamos o servidor do zero e colocamos a aplicação no ar em 4 horas"
date: 2025-12-15T18:59:06.251Z
updated: 2026-01-07T19:58:07.610Z
slug: como-recuperar-servidor-hackeado-minerador-criptomoedas
cover: img/como-recuperar-servidor-hackeado-minerador-criptomoedas/capa.jpg
type: newsletter
---

## O Problema

Era uma manhã de domingo quando percebi que algo estava errado. Ao tentar acessar o site de um cliente hospedado em uma VPS na Hostinger, fui recebido por uma mensagem fria: **502 Bad Gateway**. O nginx estava rodando, mas não conseguia processar as requisições.

Ao verificar o painel da Hostinger, o motivo ficou claro: a CPU estava travada em 100% de uso constante. Mais preocupante ainda, o detector de malware da Hostinger havia identificado dois arquivos suspeitos e os movido para quarentena.

Os arquivos tinham um nome que qualquer profissional de segurança reconheceria imediatamente: **XMRig** — um dos mineradores de Monero mais populares do mundo, frequentemente usado em ataques de cryptojacking.

***

## O Que é Cryptojacking?

Antes de continuar, vale explicar o que estava acontecendo. Cryptojacking é um tipo de ataque onde criminosos invadem servidores para minerar criptomoedas usando os recursos computacionais da vítima. Diferente de ransomware, que bloqueia seus dados e exige resgate, o cryptojacking é silencioso — o atacante quer que você não perceba enquanto ele lucra com sua eletricidade e processamento.

Monero (XMR) é a criptomoeda favorita desses ataques porque foi projetada para privacidade. Transações são praticamente impossíveis de rastrear, o que torna o dinheiro “limpo” desde a origem. O XMRig é o software que faz esse trabalho de mineração.

O sintoma clássico é exatamente o que eu estava vendo: CPU em 100%, servidor lento ou inacessível, e custos de cloud disparando.

***

## A Investigação: Pior do que Parecia

Meu primeiro instinto foi acessar o servidor via SSH e investigar. O scanner da Hostinger havia encontrado o XMRig, mas eu precisava entender a extensão do comprometimento antes de decidir os próximos passos.

### Processos Suspeitos

Ao listar os processos que mais consumiam CPU, encontrei algo que não deveria existir:

```
/usr/local/rsyslo/rsyslo → 84.8% CPU
```

Note o nome: **rsyslo**, não **rsyslog**. O atacante havia criado um processo com nome quase idêntico ao serviço legítimo de logs do Linux, esperando que passasse despercebido. Esse é um truque comum que se você olha rapidamente uma lista de processos, “rsyslo” parece “rsyslog” e você segue em frente.

### Mecanismos de Persistência

O que realmente me preocupou foi o que encontrei nos crontabs (agendador de tarefas do Linux):

```
@reboot /usr/bin/python3 /opt/.bd8888.py &
*/2 * * * * pgrep -f .bd8888.py || /usr/bin/python3 /opt/.bd8888.py &
```

Traduzindo para português:

- **Linha 1:** “Quando o servidor reiniciar, execute este script Python”
- **Linha 2:** “A cada 2 minutos, verifique se o script está rodando. Se não estiver, inicie-o novamente”

Isso significa que mesmo que eu matasse o processo malicioso, ele voltaria em no máximo 2 minutos. E se eu reiniciasse o servidor achando que resolveria, o malware voltaria automaticamente.

Havia também uma técnica de evasão sofisticada:

```
* * * * * pgrep -f meshagent | while read pid; do mountpoint -q /proc/$pid || mount -o bind /tmp/empty /proc/$pid 2>/dev/null; done
```

Esse comando tenta esconder processos montando um diretório vazio sobre a entrada do processo em `/proc`. É uma técnica para dificultar a detecção por ferramentas de monitoramento.

### O Veredito

A situação era clara: o atacante tinha **acesso root** ao servidor. Ele não apenas instalou um minerador, mas criou múltiplas backdoors, mecanismos de persistência e técnicas de evasão. Não havia como ter certeza de que encontrei tudo.

A decisão mais segura, embora dolorosa, era **reinstalar o servidor do zero**.

***

## A Decisão de Reinstalar

Muitos profissionais hesitam em reinstalar um servidor comprometido. Dá trabalho, você perde configurações, precisa reconfigurar tudo. Mas quando há acesso root confirmado, a reinstalação não é exagero — é a única opção responsável.

O problema com tentar “limpar” um servidor comprometido é que você nunca tem certeza de que encontrou tudo. O atacante pode ter:

- Modificado binários do sistema (como `ls`, `ps`, `netstat`) para esconder suas atividades
- Instalado backdoors em locais que você não pensou em verificar
- Adicionado chaves SSH autorizadas para acesso futuro
- Modificado o kernel ou módulos do sistema

Cada minuto que você passa tentando limpar é um minuto que o servidor continua vulnerável. E se você errar algo, o atacante volta.

### O Erro que Cometi

Na pressa, formatei o servidor **antes** de fazer backup. Perdi configurações do nginx, dados do banco, workflows do n8n. Foi um erro. Mesmo em situações de emergência, vale tirar 10 minutos para exportar o essencial.

A sorte foi que o código-fonte da aplicação estava no GitHub. Sem isso, a recuperação seria muito mais complicada.

**Lição:** Sempre tenha seu código em um repositório Git. Sempre.

***

## Reconstruindo com Segurança

Com o servidor limpo (Ubuntu 24.04 recém-instalado), era hora de reconstruir — mas dessa vez, com segurança como prioridade desde o primeiro minuto.

### Por Que SSH com Chaves?

A primeira mudança foi eliminar o login por senha no SSH. Provavelmente foi assim que o atacante entrou — através de força bruta em uma senha fraca.

Para entender por que chaves são mais seguras, pense assim:

**Senha:** Uma sequência de caracteres que você digita. Mesmo uma senha forte como `K8#mP2$xL9` tem talvez 60 bits de entropia. Um atacante com uma botnet pode tentar milhões de combinações por hora.

**Chave criptográfica:** Um arquivo com 256+ bits de aleatoriedade verdadeira. Para quebrar por força bruta, seriam necessários bilhões de anos com toda a capacidade computacional do planeta.

Além disso, a chave nunca é transmitida pela rede. O servidor envia um desafio, seu computador usa a chave privada para assinar o desafio, e o servidor verifica com a chave pública. Mesmo que alguém intercepte a comunicação, não consegue reproduzir o processo.

A configuração foi simples:

1. Gerar um par de chaves no meu Mac (chave privada fica comigo, pública vai para o servidor)
2. Copiar a chave pública para o servidor
3. Configurar o SSH para recusar logins por senha

Depois dessa mudança, quando um bot tenta fazer login com senha, o servidor simplesmente ignora. A porta está aberta, mas sem a chave certa, não há entrada.

### O Firewall: Reduzindo a Superfície de Ataque

O próximo passo foi configurar o UFW (Uncomplicated Firewall). A lógica é simples: quanto menos portas abertas, menos oportunidades para atacantes.

Um servidor típico pode ter dezenas de serviços rodando, cada um em sua porta. Banco de dados na 5432, Redis na 6379, aplicação na 3000, métricas na 9090... Cada porta aberta é uma porta que um atacante pode tentar explorar.

Com o UFW, defini uma política restritiva:

- **Bloquear tudo que entra** por padrão
- **Permitir tudo que sai** (o servidor precisa acessar a internet)
- **Abrir exceções específicas:** apenas SSH (22), HTTP (80) e HTTPS (443)

Qualquer outro serviço que precise rodar no servidor fica acessível apenas internamente. O banco de dados, por exemplo, só aceita conexões de localhost — não há como um atacante externo sequer tentar conectar.

***

## Dokploy: Simplificando o Deploy

Com a segurança base configurada, era hora de instalar a infraestrutura para rodar a aplicação. Escolhi o Dokploy, uma plataforma de deploy self-hosted que funciona como um “Vercel particular”.

### Por Que Não Configurar Tudo Manualmente?

Eu poderia ter instalado nginx, configurado SSL com certbot, criado scripts de deploy, configurado o Docker manualmente. Mas isso tem custos:

- **Tempo:** Horas de configuração inicial
- **Manutenção:** Renovação de certificados, atualizações, troubleshooting
- **Risco de erro:** Configurações de segurança esquecidas, permissões erradas

O Dokploy abstrai tudo isso. Ele usa Traefik como reverse proxy (que gerencia SSL automaticamente), Docker para isolar aplicações, e uma interface web para gerenciar tudo. Em 5 minutos, a infraestrutura estava pronta.

### O Problema do Docker com Firewalls

Descobri algo importante durante a configuração: **Docker ignora o UFW**.

Quando você expõe uma porta no Docker (como `-p 3000:3000`), o Docker manipula diretamente o iptables, bypassando as regras do UFW. Isso significa que minha regra de “bloquear tudo exceto 22, 80, 443” não se aplicava às portas expostas pelo Docker.

O painel do Dokploy, por exemplo, roda na porta 3000. Mesmo com o UFW bloqueando, a porta estava acessível publicamente. Qualquer pessoa na internet poderia acessar

http<!---->://meu-ip:3000

e ver a tela de login.

A solução foi dupla:

1. **Configurar um domínio para o painel** (`admin.mindapps.ai`) com HTTPS
2. **Bloquear a porta 3000 diretamente no iptables**, na chain DOCKER-USER que é respeitada pelo Docker

Depois dessa configuração, o painel só é acessível pelo domínio com SSL, nunca pelo IP direto.

***

## Configurando os Domínios

A aplicação precisava responder em três domínios diferentes:

- **gruponaldi.com** — Site principal em português
- **naldi.es** — Versão para Espanha
- **naldi.eu** — Versão para Europa

### DNS: O Sistema de “Listas Telefônicas” da Internet

Quando você digita `gruponaldi.com` no navegador, seu computador precisa descobrir qual servidor contém esse site. Isso é feito através do DNS (Domain Name System), que traduz nomes legíveis em endereços IP.

A configuração básica envolve criar registros que dizem “este domínio aponta para este IP”:

- **Registro A:** Aponta o domínio raiz (gruponaldi.com) para o IP do servidor
- **Registro CNAME:** Aponta um subdomínio (www) para outro domínio

Como os domínios usavam Cloudflare para DNS, uma consideração importante foi **desativar o proxy do Cloudflare** temporariamente. O Cloudflare pode atuar como intermediário entre visitantes e seu servidor, mas isso interfere na emissão de certificados SSL pelo Traefik. Com o proxy desligado, o Traefik consegue provar que controla o domínio e obter certificados do Let’s Encrypt.

***

## A Aplicação: Payload CMS com MongoDB

Com a infraestrutura pronta, era hora de fazer deploy da aplicação em si: um site construído com Next.js e Payload CMS, usando MongoDB como banco de dados.

### Criando o Banco de Dados

O Dokploy simplifica a criação de bancos de dados. Em poucos cliques, criei uma instância do MongoDB com:

- **Usuário e senha dedicados** (nunca use credenciais padrão)
- **Rede interna** (o banco só é acessível por outras aplicações no mesmo servidor, nunca pela internet)

A connection string resultante seria algo como:

```
mongodb://usuario:senha@nome-interno-do-container:27017/banco
```

Note que o hostname é o nome interno do container Docker, não um IP ou domínio público. Isso garante que mesmo que alguém descubra as credenciais, não consegue conectar de fora do servidor.

### Variáveis de Ambiente: Separando Código de Configuração

Uma boa prática de desenvolvimento é nunca colocar senhas ou configurações sensíveis diretamente no código. Em vez disso, usamos variáveis de ambiente que são injetadas no momento da execução.

Para o Payload CMS, as variáveis essenciais são:

- **DATABASE\_URI:** Conexão com o MongoDB
- **PAYLOAD\_SECRET:** Chave para criptografar tokens de autenticação
- **NEXT\_PUBLIC\_SERVER\_URL:** URL pública do site (usada para gerar links corretos)

Os “secrets” (PAYLOAD\_SECRET, CRON\_SECRET, etc.) devem ser strings aleatórias longas. Nunca use algo previsível como “senha123” ou o nome do projeto. Uma forma simples de gerar é:

```
openssl rand -hex 32
```

Isso gera 64 caracteres hexadecimais aleatórios — impossíveis de adivinhar.

***

## O Desafio do Build: Quando o Docker Não Consegue Acessar o Banco

Com tudo configurado, cliquei em “Deploy”. E falhou.

O erro dizia que o Next.js não conseguia conectar ao MongoDB durante o build. Mas por quê? O banco estava rodando, as credenciais estavam corretas.

### Entendendo o Problema

Para entender o erro, é preciso entender como funciona o build de uma aplicação Next.js com Docker.

O Docker funciona em camadas isoladas. Quando você faz um build, o Docker cria um container temporário, executa os comandos de build, e depois descarta esse container. O resultado é uma imagem pronta para rodar.

O problema é que esse container temporário de build está **isolado da rede**. Ele não consegue acessar outros containers, como o MongoDB. Isso é intencional — builds devem ser reproduzíveis e não depender de serviços externos.

Porém, o Payload CMS (e muitas aplicações Next.js modernas) tentam conectar ao banco durante o build para:

1. **Validar o schema** — Garantir que as coleções existem
2. **Gerar páginas estáticas** — Buscar dados para pré-renderizar páginas

Quando o Next.js tenta executar `generateStaticParams()` para descobrir quais páginas dinâmicas existem (como `/posts/[slug]`), ele precisa consultar o banco. Durante o build, isso falha porque o banco não está acessível.

### A Solução

A solução envolve duas partes:

**1. Detectar quando estamos em modo de build:**

Criamos uma função simples que verifica uma variável de ambiente:

```
export function canConnectDB(): boolean {
  if (process.env.SKIP_DB_DURING_BUILD === ‘true’) {
    return false
  }
  return true
}
```

**2. Modificar as páginas para retornar vazio durante build:**

Em cada página que usa `generateStaticParams`, adicionamos uma verificação:

```
export async function generateStaticParams() {
  if (!canConnectDB()) {
    return [] // Durante build, não gerar páginas estáticas
  }
  
  // Código normal que busca do banco
  const posts = await payload.find({ collection: ‘posts’ })
  return posts.docs.map((post) => ({ slug: post.slug }))
}
```

**O que isso significa na prática?**

Durante o build, as páginas dinâmicas não são pré-geradas. Quando um visitante acessa `/posts/meu-artigo` pela primeira vez, o Next.js gera a página naquele momento (Server-Side Rendering) e pode cachear para visitas futuras.

É uma troca: perdemos a pré-geração em build time, mas ganhamos a capacidade de fazer build sem dependência do banco. Para a maioria das aplicações, o impacto na performance é negligenciável.

***

## Resultado Final

Após aproximadamente 4 horas de trabalho, o servidor estava:

- **Limpo:** Sistema operacional reinstalado, sem vestígios do malware
- **Seguro:** SSH apenas com chaves, firewall configurado, painel protegido
- **Funcional:** Aplicação rodando, certificados SSL emitidos, domínios respondendo

### Checklist de Segurança Implementada

<figure>
<img src="/blog/img/como-recuperar-servidor-hackeado-minerador-criptomoedas/e1aa49171a-690.webp" width="690" height="260" alt="Checklist de Segurança Implementada" loading="lazy" decoding="async">
<figcaption>Checklist de Segurança Implementada</figcaption>
</figure>

***

## Lições Aprendidas

### 1. Backups Antes de Tudo

Mesmo em emergências, tire 10 minutos para exportar dados críticos. Formatar sem backup foi meu maior erro nessa situação.

### 2. SSH com Senha é um Risco

Se seu servidor aceita login SSH por senha, é questão de tempo até alguém conseguir entrar. Bots estão constantemente testando combinações. Chaves criptográficas eliminam esse vetor completamente.

### 3. Código no Git é Seguro de Vida

A única razão pela qual consegui recuperar em 4 horas foi ter o código no GitHub. Se estivesse apenas no servidor, teria perdido tudo.

### 4. Docker e Firewalls Precisam de Atenção Especial

Não assuma que suas regras de firewall protegem portas expostas pelo Docker. Verifique e configure o iptables adequadamente.

### 5. Builds Devem Ser Independentes

Aplicações que dependem de serviços externos durante o build são frágeis. Sempre que possível, projete para que o build funcione de forma isolada.

***

## Cronologia

<figure>
<img src="/blog/img/como-recuperar-servidor-hackeado-minerador-criptomoedas/f9e4850653-685.webp" width="685" height="623" alt="Cronologia do ataque" loading="lazy" decoding="async">
<figcaption>Cronologia do ocorrido</figcaption>
</figure>

***

## Conclusão

Ataques de cryptojacking são cada vez mais comuns porque são lucrativos e de baixo risco para os atacantes. A melhor defesa é não dar a eles a chance de entrar:

1. **Nunca use senhas** para acesso SSH
2. **Mantenha software atualizado** — muitos ataques exploram vulnerabilidades conhecidas
3. **Minimize a superfície de ataque** — menos portas abertas, menos riscos
4. **Monitore** — alertas de CPU salvaram esse servidor de minerar Monero por semanas

Se você foi comprometido, não tente limpar. Reinstale. O tempo que você gasta tentando ter certeza de que encontrou tudo é tempo que o servidor continua vulnerável.

E acima de tudo: mantenha backups e código em repositórios. Servidores podem ser substituídos. Dados perdidos, não.
