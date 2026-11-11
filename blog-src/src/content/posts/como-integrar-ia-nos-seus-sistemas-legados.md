---
title: "Como integrar IA nos seus sistemas legados: um workflow real"
subtitle: "Criar do zero é fácil. O desafio está em integrar IA nos sistemas que já rodam em produção. Aqui está o workflow que uso no meu time."
description: "Criar do zero é fácil. O desafio está em integrar IA nos sistemas que já rodam em produção. Aqui está o workflow que uso no meu time."
date: 2026-03-29T16:01:41.928Z
updated: 2026-03-29T16:03:57.089Z
slug: como-integrar-ia-nos-seus-sistemas-legados
cover: img/como-integrar-ia-nos-seus-sistemas-legados/capa.jpg
type: newsletter
---

<figure>
<img src="/blog/img/como-integrar-ia-nos-seus-sistemas-legados/98cabe6fb8-1456.webp" srcset="/blog/img/como-integrar-ia-nos-seus-sistemas-legados/98cabe6fb8-728.webp 728w, /blog/img/como-integrar-ia-nos-seus-sistemas-legados/98cabe6fb8-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="813" alt="Workflow de integração de IA em Sistemas Legados" loading="lazy" decoding="async">
<figcaption>Workflow de integração de IA em Sistemas Legados</figcaption>
</figure>

Você já viu aqueles vídeos. Alguém abre um prompt, descreve um app em três frases, e em dois minutos tem um produto funcionando. Login, dashboard, API, deploy. Tudo gerado por IA, do zero, sem esforço aparente.

É impressionante. E é real.

Mas se você trabalha com software de verdade, aquele sistema que já roda em produção, atende clientes, processa pagamentos, sustenta operações, você sabe que a sua realidade é outra. Você está no meio de um jogo que já começou, com regras que já foram definidas, código que já foi escrito, e um time que já tem seus processos.

A pergunta que realmente importa é: **como integrar IA no que já existe?**

***

## **Por que começar do zero é fácil**

Quando você cria um app do zero com IA, tudo conspira a seu favor.

Não existe arquitetura legada para respeitar. Não existe dívida técnica acumulada. Não existem dependências de terceiros que ninguém atualiza há dois anos. Não existe aquela regra de negócio obscura que está num `if` no meio de um service que ninguém quer mexer.

A IA gera código sem conflitos porque não há código anterior. Ela toma decisões de arquitetura sem restrições porque não há restrições. O resultado é limpo, coerente e bonito, exatamente porque o contexto é vazio.

Mas aqui está o ponto: **a esmagadora maioria do software que gera valor já existe e já está rodando**. O greenfield, aquele projeto que nasce do zero, sem bagagem, é a exceção. E otimizar para a exceção é uma armadilha.

Se você esperar o próximo projeto greenfield para começar a usar IA, vai ficar esperando por muito tempo. O desafio de verdade está nos sistemas que já estão aí.

***

## **O desafio real: IA dentro de sistemas que já funcionam**

Integrar IA em sistemas legados é um problema fundamentalmente diferente de criar algo do zero. Alguns dos motivos:

**O código não foi escrito pensando em IA.** A IA precisa entender contexto, e o contexto de um sistema legado é vasto, distribuído, e às vezes contraditório. Módulos que foram escritos por pessoas diferentes, em momentos diferentes, com premissas diferentes. Documentação desatualizada ou inexistente. Testes que cobrem o caminho feliz mas ignoram os edge cases que realmente importam.

**Os processos são enraizados.** Times têm seus rituais. Há resistência, muitas vezes legítima, a mudar o que funciona. “A gente sempre fez assim” carrega tanto inércia quanto confiança num processo que já provou que funciona em produção.

**O risco é real.** Um erro num app que acabou de nascer é um inconveniente. Um erro num sistema em produção pode significar clientes afetados, receita perdida, dados corrompidos. A margem para experimentação é menor.

Por tudo isso, o objetivo de integrar IA em sistemas legados é **aumentar a capacidade do time**: automatizar o que é repetitivo, acelerar o que é lento, e trazer uma segunda perspectiva para decisões que antes dependiam de uma pessoa só.

***

## **Um framework prático: como integrei IA no meu time**

Depois de testar diferentes abordagens, cheguei a um workflow que funciona. Ele roda no meu dia a dia, validado em projetos reais. A regra é simples: **toda mudança de código segue este fluxo, sem exceção**.

O workflow tem três fases, com gates explícitos entre elas.

### **Fase 1: Triagem (de feedback a issues)**

Tudo começa com input desestruturado. Conversas, mensagens, calls, PDFs, observações de usuários. O tipo de coisa que normalmente se perde ou vira um “depois a gente vê”.

O primeiro passo é transformar esse ruído em problemas concretos. Eu e o agente de IA analisamos juntos cada input e definimos: **“o problema é este”**. Uma issue de engenharia, com contexto técnico suficiente para que alguém (humano ou IA) consiga atuar.

O agente de IA então cria cada issue no repositório do projeto no GitHub, com descrição clara do problema.

**Por que isso importa:** a maioria dos times perde valor nessa etapa. O feedback existe, mas nunca vira uma issue rastreável. Com o agente de IA, a triagem acontece em tempo real e o custo de transformar feedback em artefato de engenharia cai drasticamente.

### **Fase 2: Planejamento (de issues a plano)**

Com as issues criadas, o agente de IA avalia cada uma, tanto isoladamente quanto no contexto global do projeto. Às vezes duas issues que parecem independentes são, na verdade, sintomas do mesmo problema. O agente identifica isso e pode consolidá-las num único plano de ação.

O plano é detalhado: o que muda, por que muda, como muda. Ele é registrado numa ferramenta de gestão. Pode ser Notion, Jira, Trello, ou alternativas open source como **Plane**, **OpenProject**, **LeanTime** ou **Taiga**. O importante é que o plano exista como artefato visível, acessível, e separado do código.

Então eu leio o plano e aprovo (ou peço ajustes). **Nada executa sem plano aprovado.** Esse é o primeiro gate.

**Por que isso importa:** a IA é rápida para executar, mas isso pode ser perigoso sem direção. O plano é o freio intencional que garante que a velocidade da IA está apontada na direção certa.

### **Fase 3: Execução (de plano aprovado a deploy)**

Com o plano aprovado, a execução segue um fluxo disciplinado:

1. **Branch.** O agente de IA cria uma branch (`feat/xxx` ou `fix/xxx`) a partir da main. Nunca trabalha direto na main.
2. **Implementação.** O agente implementa e commita na branch, seguindo o plano aprovado.
3. **Pull Request.** O agente abre um PR no GitHub com descrição clara do que muda e por quê.
4. **Revisão.** Aqui entra um conceito importante: **a revisão do PR pode (e deve) envolver uma segunda IA**. A mesma inteligência que escreveu o código carrega viés de autoria, pelo mesmo motivo que um desenvolvedor não deveria aprovar seu próprio PR. Uma segunda IA traz uma perspectiva independente, aumentando a assertividade da análise crítica. Mas o merge é **sempre** decisão humana.
5. **Deploy.** Build e deploy via CI/CD (GitHub Actions, pipelines automatizados, ou manualmente quando necessário).
6. **Status.** Atualização do status na ferramenta de gestão, fechando o ciclo.

### **Os gates**

Três regras inegociáveis:

- **Nada executa sem plano aprovado.** A IA não tem autonomia para decidir o que fazer, só como fazer.
- **Nada vai direto pra main.** Toda mudança passa por PR, sempre.
- **Merge é sempre decisão humana.** A IA pode sugerir, implementar, e até revisar, mas a palavra final é do líder técnico.

> A IA é o motor. O humano é o volante.

***

## **Por que usar uma segunda IA para revisar PRs**

Esse ponto merece destaque porque é contra-intuitivo. Se você já tem uma IA gerando código, por que usar outra para revisar?

Pelo mesmo motivo que code review existe: **viés do autor**. Quando uma IA (ou um humano) escreve código, ela toma decisões implícitas sobre nomes, sobre estrutura, sobre o que precisa ou não de tratamento de erro. Essas decisões ficam invisíveis para quem as tomou.

Uma segunda IA, sem o contexto de autoria, olha para o código com olhos frescos. Ela questiona premissas que a primeira IA deu como certas. Ela identifica edge cases que o autor não considerou. Ela avalia se o código realmente faz o que o PR diz que faz.

Na prática, isso aumenta significativamente a qualidade do que chega para revisão humana. O líder técnico pode ser o último filtro, focando em decisões de arquitetura e de negócio em vez de caçar bugs triviais.

***

## **O caminho realista**

Se você está esperando o momento perfeito para integrar IA no seu time, tenho uma má notícia: ele não existe. O momento é agora, com o sistema que você já tem, com o time que você já tem.

Algumas lições que aprendi:

- **Comece pelo processo.** Antes de pedir para uma IA escrever código no seu sistema legado, defina como ela vai trabalhar. Qual é o fluxo? Quais são os gates? Quem aprova o quê? O workflow vem antes da implementação.
- **Trate a IA como membro do time.** Um membro do time tem responsabilidades definidas, participa de processos, e é cobrado por resultados. Quando você define que o agente de IA é responsável por criar issues, elaborar planos e abrir PRs, ele passa a ter um papel claro dentro do time.
- **Mantenha o humano no controle das decisões que importam.** A IA acelera execução, mas as decisões de o que fazer e quando fazer continuam sendo humanas. Velocidade de máquina com julgamento humano.
- **Busque disciplina.** O workflow que descrevi tem falhas. Mas funciona porque tem regras claras, gates explícitos, e artefatos rastreáveis. É isso que permite escalar o uso de IA sem perder o controle.

***

Criar um app do zero com IA é fácil. Integrar IA nos seus sistemas legados é onde está o trabalho de verdade, e onde está o maior impacto.

O código que importa já existe. O que você vai fazer com ele?
