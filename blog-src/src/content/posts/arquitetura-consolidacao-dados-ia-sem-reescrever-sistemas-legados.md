---
title: "Como transformei dados fragmentados em uma plataforma pronta para IA, preservando todos os sistemas legados"
subtitle: "Uma arquitetura em camadas para consolidar dezenas de fontes heterogêneas, habilitar inteligência artificial e retroalimentar toda a operação, sem risco e sem reescrever sistemas originais e legados"
description: "Uma arquitetura em camadas para consolidar dezenas de fontes heterogêneas, habilitar inteligência artificial e retroalimentar toda a operação, sem risco e sem reescrever sistemas originais e legados"
date: 2026-03-29T21:13:06.054Z
updated: 2026-03-29T21:13:50.374Z
slug: arquitetura-consolidacao-dados-ia-sem-reescrever-sistemas-legados
cover: img/arquitetura-consolidacao-dados-ia-sem-reescrever-sistemas-legados/capa.jpg
type: newsletter
---

<figure>
<img src="/blog/img/arquitetura-consolidacao-dados-ia-sem-reescrever-sistemas-legados/5b94a3eda1-1456.webp" srcset="/blog/img/arquitetura-consolidacao-dados-ia-sem-reescrever-sistemas-legados/5b94a3eda1-728.webp 728w, /blog/img/arquitetura-consolidacao-dados-ia-sem-reescrever-sistemas-legados/5b94a3eda1-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="1020" alt="Arquitetura de consolidação de dados" loading="lazy" decoding="async">
<figcaption>Arquitetura de consolidação de dados</figcaption>
</figure>

Toda empresa com mais de três anos de operação tem o mesmo problema. Ninguém planejou que os sistemas conversassem entre si. O CRM roda em MySQL, o financeiro em PostgreSQL, a plataforma de relacionamento com clientes usa outro MySQL com schema completamente diferente, o marketing alimenta planilhas e ferramentas SaaS que expõem APIs próprias, e há dados em filas, em caches, em buckets de storage, em bancos NoSQL que alguém subiu para um projeto piloto que ficou. Cada sistema foi a melhor decisão no contexto em que nasceu. O problema real sempre foi a ausência de uma estratégia de consolidação.

Quando um diretor pede “quero um relatório cruzando engajamento com receita”, a resposta honesta em muitas empresas é: “precisamos de dois dias e três planilhas coladas no Excel”.

Este artigo descreve a arquitetura que desenhei e implementei para resolver esse problema, com foco no racional por trás de cada decisão.

***

## O cenário: dados em volume, sem utilidade prática

O cliente operava com dezenas de sistemas acumulados ao longo de anos: bancos MySQL em versões diferentes, instâncias PostgreSQL, APIs de terceiros, planilhas compartilhadas que funcionavam como “banco de dados” de áreas inteiras, dados em ferramentas SaaS acessíveis apenas por exportação manual. Os dados estavam lá, em volume considerável, mas apresentavam três problemas críticos:

**Fragmentação.** Uma mesma entidade (um cliente, um contrato, uma transação) existia em dois ou três sistemas com schemas diferentes, IDs diferentes, e graus de atualização diferentes. Sem fonte única de verdade.

**Obsolescência.** Campos abandonados, registros duplicados, categorias que já não refletiam a operação. O dado envelhecia dentro do banco sem que ninguém o curasse.

**Opacidade para IA.** Este era o ponto decisivo. A empresa queria usar inteligência artificial para recomendações, automação e análise preditiva. IA opera sobre dados consistentes e estruturados. Um modelo de linguagem que recebe contexto fragmentado produz respostas fragmentadas. Um modelo de scoring alimentado com duplicatas gera rankings distorcidos.

O desafio era ao mesmo tempo técnico e estratégico: tornar o patrimônio de dados da empresa utilizável por IA, sem interromper as operações que dependiam dos sistemas existentes.

***

## O princípio: zero impacto nas origens

A primeira decisão arquitetural, e provavelmente a mais importante, foi preservar os sistemas originais intactos.

Reescrever um CRM legado ou migrar um ERP financeiro para um banco unificado é o tipo de projeto que consome meses, gera risco operacional, e frequentemente falha. Já vi isso acontecer. O sistema “novo e melhor” fica pronto com 70% das funcionalidades do antigo, e a equipe operacional passa seis meses compensando as lacunas manualmente.

A abordagem que adotei parte de uma premissa simples: manter cada aplicação original rodando exatamente como está, alimentando seus respectivos bancos, APIs e storages normalmente. Cada fonte de dados, independente da tecnologia (MySQL, PostgreSQL, APIs REST, arquivos em bucket, exportações de SaaS), passa a ser espelhada, de forma incremental e não intrusiva, para um data lake centralizado.

Na prática, o time operacional não percebe nenhuma diferença no dia a dia. O CRM continua funcionando. O ERP continua emitindo notas. A plataforma web continua servindo usuários. As planilhas do marketing continuam sendo preenchidas. Nos bastidores, cada alteração em cada uma dessas fontes é capturada e replicada para uma camada de armazenamento separada, independente de ser um banco relacional, uma API, ou um arquivo CSV em um bucket.

***

## A arquitetura em camadas

O desenho final tem quatro camadas distintas, cada uma com uma responsabilidade clara.

### Camada 1: Aplicações Originais

MySQL em diferentes versões, PostgreSQL, APIs de SaaS, planilhas, filas de mensagens, arquivos em storage. Cada sistema continua operando como sempre operou. O único ponto de contato é o mecanismo de captura adequado a cada tecnologia: CDC para bancos relacionais, conectores de API para SaaS, ingestão de arquivos para exports e planilhas, consumers para filas. A complexidade está justamente em lidar com essa diversidade sem impor um padrão único na origem. Esta camada é apenas observada, nunca alterada.

### Camada 2: Data Lake (Espelhamento)

Aqui os dados chegam organizados em três zonas:

A **Raw Zone** recebe o dado bruto, exatamente como saiu da origem. Sem transformação, sem limpeza. É o registro histórico fiel do que cada sistema produziu. Fundamental para auditoria e para reprocessamento quando as regras de negócio mudam.

A **Staging Zone** aplica as primeiras transformações: limpeza, deduplicação, tipagem correta, validação de qualidade. Um campo “telefone” que no MySQL era varchar livre aqui é padronizado. Um registro duplicado é identificado e marcado.

A **Curated Zone** é onde o dado ganha estrutura de negócio. As entidades são consolidadas: o “cliente” que existia em três sistemas agora é uma única entidade com todos os atributos unificados. O modelo relacional passa a refletir a realidade do negócio, alinhado à operação e ao vocabulário da empresa.

### Camada 3: Enriquecimento e Inteligência

Esta camada é onde o dado se torna operacional para IA, e onde mora boa parte do valor diferencial da arquitetura.

Sobre a base consolidada da Curated Zone, esta camada executa processamentos que adicionam informação. Modelos de scoring calculam propensão, relevância, risco. Algoritmos de matchmaking cruzam perfis para gerar recomendações. Enriquecimento externo adiciona dados de terceiros (geocodificação, dados de mercado, complementos cadastrais). Métricas de negócio são pré-calculadas e materializadas para consumo rápido.

O ponto crítico aqui é que algumas aplicações finais já vivem nesta camada. O sistema de matchmaking, por exemplo, funciona como um serviço que consome e produz dados em tempo quase-real. Esta camada é parte ativa da operação.

### Camada 4: Aplicações Finais (Consumo e Retroalimentação)

Dashboards, APIs inteligentes, automações, e agentes de IA. Todas essas aplicações consomem dados que já passaram por consolidação, limpeza, e enriquecimento. Um agente de IA que responde perguntas sobre clientes consulta uma única camada que já consolidou e enriqueceu a informação de todas as origens.

Um aspecto fundamental desta camada é que ela retroalimenta todas as anteriores. Os scores calculados por modelos de ML podem ser escritos de volta nos bancos de origem, para que as aplicações legadas se beneficiem da inteligência gerada. Novos dados produzidos por automações alimentam o data lake. Resultados de agentes de IA recalculam métricas na camada de enriquecimento. O sistema forma um ciclo: cada camada se beneficia do que as outras produzem.

***

## Decisões que importaram

Algumas decisões técnicas merecem destaque por terem sido determinantes para o sucesso do projeto.

**CDC para bancos, conectores para o resto.** Change Data Capture permite replicar apenas o que mudou nos bancos relacionais, reduzindo janela de latência e custo de processamento. Mas CDC é apenas uma das estratégias. Para APIs de SaaS, há conectores que sincronizam periodicamente. Para planilhas e exports, há ingestão baseada em eventos de arquivo. A decisão de qual mecanismo usar para cada fonte é parte do trabalho de arquitetura, e cada escolha errada aqui se paga em fragilidade do pipeline.

**Schema-on-read na Raw Zone.** Permitir que os dados cheguem sem schema rígido absorve mudanças nos sistemas de origem sem quebrar o pipeline. Quando o time de produto adiciona um campo no CRM, o data lake absorve automaticamente. A transformação acontece nas camadas seguintes, onde há controle.

**Materialized views para métricas.** KPIs pré-calculados e atualizados incrementalmente eliminam a necessidade de queries complexas em tempo real. O dashboard que carrega em menos de 2 segundos só é possível porque o cálculo pesado já foi feito antecipadamente.

**Separação entre enriquecimento e consumo.** Manter a camada de inteligência separada da camada de consumo permite que diferentes aplicações usem diferentes níveis de processamento. Um dashboard simples pode consumir direto da Curated Zone. Um agente de IA precisa dos dados enriquecidos. Essa flexibilidade evita over-engineering para casos simples.

**Retroalimentação entre camadas.** Permitir que as aplicações finais escrevam dados de volta nas camadas anteriores transforma a arquitetura em um sistema vivo. Os dados melhoram a cada ciclo: um score gerado na Camada 3 pode refinar um processo na Camada 1, que por sua vez produz dados de melhor qualidade no próximo ciclo de ingestão.

***

## O resultado: dados como ativo estratégico

A empresa passou a contar com uma plataforma de dados consolidada que serve como alicerce para todas as iniciativas de IA e automação da operação.

Funcionalidades que exigiam semanas de trabalho manual passaram a ser implementáveis em dias. Novos agentes de IA podem ser conectados à plataforma sem necessidade de integrações customizadas. Novas aplicações consomem dados já consolidados e enriquecidos desde o primeiro dia.

Nenhum sistema original foi descontinuado ou reescrito. O risco operacional foi zero. A equipe continuou trabalhando normalmente enquanto a infraestrutura de dados era construída em paralelo.

***

## Para quem enfrenta o mesmo desafio

Se sua empresa tem dados em múltiplos sistemas e está tentando viabilizar projetos de IA, a pergunta mais produtiva é: “nossos dados estão prontos para alimentar IA de forma confiável?”

Na grande maioria dos casos, ainda não estão. E a solução passa por construir uma camada de inteligência sobre os sistemas que já existem.

Este é o tipo de projeto que tenho prazer em conceber e executar: diagnóstico de maturidade de dados, desenho de arquitetura de consolidação, e implementação de plataformas que transformam dados fragmentados em ativos estratégicos prontos para IA.

Se quiser conversar sobre como isso se aplica à sua realidade, me encontre no LinkedIn ou entre em contato diretamente.

***

*Guilherme Favaron é especializado em arquitetura de dados e plataformas de IA. Trabalha com empresas que querem extrair valor real dos dados que já possuem.*
