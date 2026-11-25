---
title: "Quanto custa rodar IA sobre os dados da empresa"
subtitle: "Comparei API, assinatura por usuário e GPU própria com preços conferidos em 24 de novembro de 2026 e medições reais de tokens e vazão. Na maioria das empresas, a fatura do modelo é a menor das quatro linhas de custo."
description: "Comparei API, assinatura por usuário e GPU própria para rodar IA sobre dados da empresa, com preços de novembro de 2026, simulações em R$ e o ponto de virada."
summary: "Para empresas com até 50 pessoas, a fatura de tokens em API fica em cerca de R$ 2,6 mil por mês em 10 mil consultas sem otimização (ou R$ 609 otimizada), enquanto a engenharia responde por R$ 31,9 mil mensais no primeiro ano. Assinaturas por usuário custam menos em equipes menores (R$ 18,4 mil para 50 pessoas no mix 60/40), mas perdem para a API otimizada acima de 104 usuários. GPU própria em nuvem raramente empata por custo puro com uso em horário comercial."
date: 2026-11-24T18:00:00Z
slug: quanto-custa-rodar-ia-sobre-dados-da-empresa
type: newsletter
topics: [custos-e-modelos-de-ia]
---

Na manhã de 24 de novembro publiquei aqui o [teste em 80 contratos comparando RAG, contexto longo e Text-to-SQL](/blog/rag-contexto-longo-ou-text-to-sql-teste-80-contratos/). Ali, medi que despejar uma planilha de faturamento com 5 mil linhas inteira no contexto do modelo consome 447.568 tokens de entrada, enquanto filtrar os dados antes com SQL derruba a mesma pergunta para 554 tokens. Aquele teste mostrou quanto a arquitetura pesa no volume de tokens, e deixou em aberto a pergunta que chega logo depois à mesa do diretor financeiro. Quanto custa, em reais por mês, manter IA respondendo sobre os dados da empresa quando comparamos API paga por token, assinatura por usuário e GPU própria em nuvem?

Organizei a resposta em sete partes: as quatro linhas que compõem a conta real, os preços oficiais de API por milhão de tokens, os planos corporativos por assento, o aluguel de GPU em nuvem 24 horas contra horário comercial, a simulação por porte de empresa com o ponto de virada, o impacto de câmbio e impostos no Brasil, e o método com os limites desta modelagem. No fim ficam as perguntas frequentes. Todos os preços de tabela foram conferidos nas páginas oficiais dos provedores em 24 de novembro de 2026 e convertidos pela taxa PTAX de venda de fechamento do Banco Central do mesmo dia, de R$ 5,8573 por dólar.

## Quais são as quatro linhas de custo de um projeto de IA?

Quem compara opções olhando apenas a tabela de tokens por milhão erra o orçamento por uma ordem de grandeza. Na [planilha de custos de IA](/tools/planilha-custos-ia/) que montei para acompanhar projetos reais, toda arquitetura soma quatro linhas mensais: inferência, infraestrutura ao redor do modelo, desperdício e engenharia.

A primeira linha é a inferência útil, ou seja, o que efetivamente responde às perguntas do time, seja em tokens de API, em assentos realmente usados ou nas horas em que uma GPU está processando requisições. A segunda linha cobre o que sustenta a aplicação fora do modelo: contêineres da aplicação e do ambiente de execução de consultas, banco de dados gerenciado, logs, monitoramento e, no caso da GPU própria, disco para os pesos do modelo e telemetria da placa. A terceira linha mede o desperdício inevitável: retentativas por falhas na API, licenças de chat pagas para quem quase nunca abre a ferramenta e horas em que uma placa alugada fica ligada sem nenhuma pergunta na fila.

A quarta linha, engenharia, costuma dominar o primeiro ano e raramente aparece nas apresentações comerciais. Conectar um modelo aos dados da empresa com segurança exige construir e manter um ambiente somente leitura para consultas, controle de permissão por fonte, tabela e coluna, login corporativo por SSO, trilha de auditoria de cada pergunta, bateria de testes de acerto antes de trocar de versão do modelo, integração com as fontes internas e plantão de monitoramento. Quem escolhe GPU própria soma a essa conta a instalação, atualização e observabilidade do servidor de inferência.

Os 447.568 tokens de entrada que medi na planilha de 5 mil linhas do artigo anterior (onde a contagem mediu só a entrada, sem gerar resposta), somados aos 424 tokens de saída do perfil médio detalhado abaixo, custam US$ 1,80 (R$ 10,54) por pergunta no Claude Opus 5.5. Em 10 mil consultas por mês, esse desenho consome US$ 17.988 (R$ 105.358) mensais só na fatura do modelo. Filtrar no banco antes com SQL reduz a entrada para 554 tokens e leva a mesma pergunta, com os mesmos 424 tokens de saída, para US$ 0,0107 (R$ 0,06), 168 vezes menos.

## Quanto custa chamar uma API de IA por milhão de tokens?

Na cobrança por API, a empresa paga exatamente pelos tokens enviados no prompt e devolvidos na resposta, com descontos expressivos para leitura de cache de prompt (quando o prefixo de instruções e esquemas se repete) e para processamento em lote assíncrono (Batch API, com 50% de redução na maioria dos provedores). A tabela abaixo reúne os preços Standard conferidos em 24 de novembro de 2026 nas páginas oficiais da Anthropic, da OpenAI e do Google, já convertidos pela PTAX de R$ 5,8573.

| Provedor | Modelo | Entrada (US$ / 1M) | Saída (US$ / 1M) | Cache hit (US$ / 1M) | Entrada (R$ / 1M) | Saída (R$ / 1M) | Observações e vigência |
|---|---|---:|---:|---:|---:|---:|---|
| Anthropic | Claude Haiku 5.5 | US$ 0,10 | US$ 0,50 | US$ 0,01 | R$ 0,59 | R$ 2,93 | Prompts até 100k tokens; acima de 100k sobe para US$ 0,50 / US$ 2,50; Batch -50% |
| Anthropic | Claude Sonnet 5.5 | US$ 2,00 | US$ 10,00 | US$ 0,10 | R$ 11,71 | R$ 58,57 | Padrão de produção; gravação de cache 5 min a US$ 2,50; Batch -50% |
| Anthropic | Claude Opus 5.5 | US$ 4,00 | US$ 20,00 | US$ 0,20 | R$ 23,43 | R$ 117,15 | Fronteira e agentes longos; gravação de cache 5 min a US$ 5,00; Batch -50% |
| Anthropic | Claude Fable 5.2 | US$ 8,00 | US$ 40,00 | US$ 0,20 | R$ 46,86 | R$ 234,29 | Raciocínio avançado; leitura de cache a 2,5% da entrada; Batch -50% |
| OpenAI | GPT-6 Luna | US$ 0,10 | US$ 0,50 | US$ 0,01 | R$ 0,59 | R$ 2,93 | Tarifa Standard até 272k tokens; tier Flex ou Batch corta 50% (US$ 0,05 / US$ 0,25) |
| OpenAI | GPT-6.1 Terra | US$ 1,00 | US$ 5,00 | US$ 0,05 | R$ 5,86 | R$ 29,29 | Tarifa Standard até 272k tokens; tier Flex ou Batch corta 50% (US$ 0,50 / US$ 2,50) |
| OpenAI | GPT-6.2 Sol / 6.1 Sol | US$ 2,00 | US$ 10,00 | US$ 0,10 | R$ 11,71 | R$ 58,57 | Standard até 272k tokens; acima de 272k dobra entrada; Batch -50% |
| OpenAI | GPT-6.2 Astra | US$ 8,00 | US$ 40,00 | US$ 0,40 | R$ 46,86 | R$ 234,29 | Standard até 272k tokens; Batch -50% |
| OpenAI | GPT-6 Astra | US$ 10,00 | US$ 50,00 | US$ 1,00 | R$ 58,57 | R$ 292,87 | Standard até 272k tokens; Batch -50% |
| Google | Gemini 3.5 Flash-Lite | US$ 0,30 | US$ 2,50 | US$ 0,03 | R$ 1,76 | R$ 14,64 | Paid Tier; saída inclui thinking tokens; armazenamento de cache a US$ 1,00/1M/h |
| Google | Gemini 3.8 Flash | US$ 0,75 | US$ 3,75 | US$ 0,075 | R$ 4,39 | R$ 21,96 | Vigente até 31/12/2026; sobe para US$ 1,50 / US$ 7,50 em 01/01/2027 (R$ 8,79 / R$ 43,93) |
| Google | Gemini 4 Argon / Carbon | US$ 2,00 | US$ 10,00 | US$ 0,10 | R$ 11,71 | R$ 58,57 | Vigente até 30/11/2026; sobe para US$ 4,00 / US$ 20,00 em 01/12/2026 (R$ 23,43 / R$ 117,15) |
| Google | Gemini 4 Neon | US$ 3,00 | US$ 15,00 | US$ 0,15 | R$ 17,57 | R$ 87,86 | Paid Tier; saída inclui thinking tokens |

Para traduzir preço por milhão em custo real por pergunta, usei o perfil medido numa bateria de 224 perguntas sobre dados empresariais no TalkWithData, a plataforma que eu construo (declaro o conflito de interesse aqui, e a modelagem abaixo vale para qualquer aplicação própria). Uma consulta completa faz em média quatro chamadas ao modelo: entender o pedido e escrever SQL (2.214 tokens de entrada e 102 de saída), interpretar um pedido sobre documento (1.578 e 47), corrigir eventual falha de consulta (85 e 41 na média por pergunta) e redigir a resposta final (5.104 e 234). Somadas, são 8.981 tokens de entrada e 424 de saída por pergunta.

Se a empresa mandar todas as quatro chamadas ao Claude Opus 5.5 sem nenhuma otimização, cada consulta custa US$ 0,0444 (R$ 0,26). Em 10 mil consultas no mês, a fatura de API fica em US$ 444 (R$ 2.601). Ao combinar três alavancas que detalhei no artigo sobre [custos de IA generativa em produção](/blog/ia-generativa-em-producao-custos/), rotear 60% das chamadas mais simples para o Claude Haiku 5.5 mantendo 40% no Opus 5.5, aproveitar cache de prompt sobre 60% de prefixo fixo com taxa de acerto de 85% (já pagando a gravação de 1,25 vez nos 15% de cache miss) e deslocar 20% das rotinas agendadas para Batch API, o custo por consulta cai 76,6%, para US$ 0,0104 (R$ 0,06). Em 10 mil consultas mensais, a fatura do modelo desce para US$ 104 (R$ 609). Mesmo em 100 mil consultas no mês, o gasto com tokens fica em US$ 1.040 (R$ 6.090).

Otimizar roteamento e cache corta a fatura do modelo para menos de um quarto, e é por isso que eu concentro a revisão de orçamento nas linhas fixas de infraestrutura e engenharia.

## Quanto custam as assinaturas corporativas por usuário?

Quando o objetivo imediato é dar acesso a modelos de fronteira para o time trabalhar com arquivos, resumos e análises pontuais sem construir software próprio, a assinatura por assento elimina quase toda a engenharia inicial. Os valores oficiais conferidos em 24 de novembro de 2026 estão abaixo.

| Provedor | Plano | US$ / usuário / mês | R$ / usuário / mês | Regras de contratação |
|---|---|---:|---:|---|
| OpenAI | ChatGPT Business Standard | US$ 20 (anual) / US$ 25 (mensal) | R$ 100,00 (tabela BR anual) / R$ 146,43 (mensal PTAX) | 2 a 200 assentos; sem treino nos dados por padrão; página brasileira exibe R$ 100/mês no anual |
| OpenAI | ChatGPT Business Premium | US$ 100 (anual) / US$ 125 (mensal) | R$ 500,00 (tabela BR anual) / R$ 732,16 (mensal PTAX) | Cinco vezes o uso do Standard, sem limite de 5 horas; página brasileira exibe R$ 500/mês no anual |
| OpenAI | ChatGPT Enterprise | Sob consulta | Sob consulta | Contrato anual via vendas; SCIM, residência de dados e pool de créditos |
| Anthropic | Claude Team Standard | US$ 20 (anual) / US$ 25 (mensal) | R$ 117,15 (anual) / R$ 146,43 (mensal) | 2 a 150 assentos; uso acima do plano Pro; sem treino nos dados |
| Anthropic | Claude Team Premium | US$ 100 (anual) / US$ 125 (mensal) | R$ 585,73 (anual) / R$ 732,16 (mensal) | Cinco vezes o uso do Standard; inclui Claude Code e Cowork |
| Anthropic | Claude Enterprise | US$ 20 (anual) + consumo API | R$ 117,15 + consumo API | Contrato anual, mínimo de 20 assentos; taxa fixa cobre governança (SCIM, logs, retenção) e tokens correm na tarifa de API |
| Google | Workspace Business Starter | Preço nativo em BRL | R$ 40,90 (anual; promo 1º ano R$ 32,72 até 20 usuários) | Valor oficial Brasil sem impostos; 30 GB; Gemini integrado ao Gmail |
| Google | Workspace Business Standard | Preço nativo em BRL | R$ 81,80 (anual) | Valor oficial Brasil sem impostos; 2 TB; Gemini no Gmail, Docs e Meet |
| Google | Workspace Business Plus | Preço nativo em BRL | R$ 128,40 (anual) | Valor oficial Brasil sem impostos; 5 TB; Vault, segurança avançada e Gemini |
| Google | Workspace Enterprise | Sob consulta | Sob consulta | Contrato via vendas; controles avançados de compliance |

Para 50 pessoas distribuídas em 60% de assentos Standard (US$ 20 no anual) e 40% de assentos Premium (US$ 100 no anual), os assentos somam US$ 2.600 por mês (R$ 15.229 na PTAX). Somando 80 horas iniciais de treinamento e adoção diluídas no primeiro ano mais 10 horas mensais de acompanhamento, o total mensal fica em US$ 3.137 (R$ 18.372).

Enxergo dois limites que costumam aparecer meses depois da assinatura. Primeiro, o licenciamento fixo por assento cobra o mesmo valor de quem faz duzentas perguntas por mês e de quem quase nunca abre a ferramenta; na planilha, eu modelo esse desperdício com a premissa declarada de 35% de licenças subutilizadas, o que na conta de 50 pessoas representa US$ 910 (R$ 5.330) pagos por mês sem retorno proporcional. Segundo, um chat por assento atende produtividade individual, mas raramente aplica permissão por linha e coluna de um banco SQL corporativo nem grava uma trilha verificável da consulta executada em cada resposta. O próprio modelo do Claude Enterprise sinaliza essa fronteira ao cobrar US$ 20 fixos por usuário pela camada de governança e faturar o uso real em tarifa de API.

## Quanto custa alugar GPU em nuvem 24 horas contra horário comercial?

Rodar um modelo aberto em GPU própria entra na conversa por dois motivos recorrentes: a promessa de fugir do custo variável por token e a exigência de manter dados sob controle da infraestrutura da empresa. Para comparar com precisão, conferi os preços On-Demand por GPU-hora em 24 de novembro de 2026 em quatro provedores: RunPod (Secure Cloud), Lambda, AWS EC2 e Google Cloud. A tabela mostra o custo mensal em reais em dois regimes: ligada 24 horas por dia (730 horas no mês) e ligada apenas em horário comercial estendido (220 horas no mês, equivalentes a 10 horas em 22 dias úteis).

| Provedor | GPU / Instância | Região | US$ / hora | R$ / hora | 24/7 (730 h, R$ / mês) | Comercial (220 h, R$ / mês) | Observações |
|---|---|---|---:|---:|---:|---:|---|
| RunPod | NVIDIA L4 (24 GB) | Global (Secure) | US$ 0,69 | R$ 4,04 | R$ 2.950 | R$ 889 | Cobrança por segundo; 12 vCPU / 50 GB RAM |
| RunPod | NVIDIA L40S (48 GB) | Global (Secure) | US$ 1,09 | R$ 6,38 | R$ 4.661 | R$ 1.405 | 16 vCPU / 94 GB RAM |
| Lambda | NVIDIA A100 SXM (40 GB) | EUA (1 GPU) | US$ 1,99 | R$ 11,66 | R$ 8.509 | R$ 2.564 | Mesma classe de placa usada na medição de vazão abaixo |
| RunPod | NVIDIA A100 PCIe (80 GB) | Global (Secure) | US$ 2,09 | R$ 12,24 | R$ 8.936 | R$ 2.693 | A100 SXM 80 GB sai a US$ 2,19/h |
| RunPod | NVIDIA H100 PCIe (80 GB) | Global (Secure) | US$ 2,89 | R$ 16,93 | R$ 12.357 | R$ 3.724 | 16 vCPU / 188 GB RAM |
| RunPod | NVIDIA H100 SXM (80 GB) | Global (Secure) | US$ 4,39 | R$ 25,71 | R$ 18.771 | R$ 5.657 | 20 vCPU / 125 GB RAM |
| RunPod | NVIDIA H200 (141 GB) | Global (Secure) | US$ 5,29 | R$ 30,99 | R$ 22.619 | R$ 6.817 | 24 vCPU / 276 GB RAM |
| RunPod | NVIDIA B200 (180 GB) | Global (Secure) | US$ 7,99 | R$ 46,80 | R$ 34.164 | R$ 10.296 | 28 vCPU / 283 GB RAM |
| AWS EC2 | `g6.xlarge` (1× L4 24 GB) | `us-east-1` | US$ 0,80 | R$ 4,71 | R$ 3.441 | R$ 1.037 | On-Demand Linux (US$ 0,8048/h); `sa-east-1` tem ágio regional |
| AWS EC2 | `g6e.xlarge` (1× L40S 48 GB) | `us-east-1` | US$ 1,86 | R$ 10,90 | R$ 7.957 | R$ 2.398 | On-Demand Linux (US$ 1,8610/h) |
| AWS EC2 | `p4de.24xlarge` (8× A100 80 GB) | `us-east-1` | US$ 3,43 | R$ 20,10 | R$ 14.671 | R$ 4.422 | Preço por GPU; vendida em nó de 8 placas a US$ 27,45/h (R$ 117,4 mil/mês em 730 h) |
| AWS EC2 | `p5.4xlarge` (1× H100 80 GB) | `us-east-1` | US$ 6,88 | R$ 40,30 | R$ 29.418 | R$ 8.866 | Opção de 1 placa; o nó `p5.48xlarge` (8× H100) custa US$ 55,04/h (R$ 235,3 mil/mês em 730 h) |
| Google Cloud | `g2-standard-4` (1× L4 24 GB) | `southamerica-east1` | US$ 0,71 | R$ 4,16 | R$ 3.036 | R$ 915 | Região São Paulo; em `us-central1` custa US$ 0,70/h |
| Google Cloud | `a2-highgpu-1g` (1× A100 40 GB) | `us-central1` | US$ 3,67 | R$ 21,50 | R$ 15.692 | R$ 4.729 | Em São Paulo sobe para US$ 4,27/h (R$ 18.258/mês em 730 h; +16%) |
| Google Cloud | `a2-ultragpu-1g` (1× A100 80 GB) | `us-central1` | US$ 5,07 | R$ 29,69 | R$ 21.673 | R$ 6.532 | On-Demand (US$ 5,0688/h); opção de 1 placa de 80 GB em `us-central1` |
| Google Cloud | `a3-highgpu-8g` (8× H100 80 GB) | `us-central1` | US$ 11,06 | R$ 64,79 | R$ 47.296 | R$ 14.254 | Preço por GPU; nó de 8 placas On-Demand a US$ 88,49/h (R$ 378,4 mil/mês em 730 h); tipos A3 com menos de 8 GPUs só existem em Spot ou Flex-start (limite de 7 dias) |

Mesmo quando há opção de placa única On-Demand nas grandes nuvens, o aluguel 24/7 custa R$ 21,7 mil por mês numa A100 de 80 GB no Google Cloud (`a2-ultragpu-1g`) e R$ 29,4 mil numa H100 na AWS (`p5.4xlarge`). Quando o workload exige nós fechados de 8 placas On-Demand (como a `p4de.24xlarge` na AWS ou a `a3-highgpu-8g` no Google Cloud, onde tipos A3 menores só existem em Spot ou Flex-start com limite de 7 dias), a fatura 24/7 fica entre R$ 117,4 mil e R$ 378,4 mil mensais antes de contratos plurianuais (Savings Plans ou CUD) ou instâncias Spot. Além do aluguel da máquina, o tamanho do modelo limita a placa escolhida: como mostrei no guia de [cálculo de memória de GPU para LLMs](/blog/calculo-memoria-gpu-llm/), modelos abertos maiores consomem rapidamente toda a VRAM disponível quando somamos os pesos e o cache KV de requisições simultâneas.

Para medir a vazão real de uma placa sem chutar, rodei em 23 de novembro de 2026 um teste numa NVIDIA A100 SXM de 40 GB servindo o modelo aberto Qwen3-8B em precisão `bf16` pelo `vLLM`. Com quatro perguntas simultâneas, a placa entregou 1.447 respostas completas por hora mantendo p90 de 10,1 segundos (dentro do limite operacional de 11 segundos que adoto para consulta interativa). Ao subir para oito perguntas simultâneas, o p90 saltou para 14,6 segundos.

Desligar a GPU fora do expediente derruba o aluguel da placa em 70% (uma A100 de 40 GB na Lambda cai de R$ 8.509 para R$ 2.564 mensais em 220 horas), mas acrescenta tempo de aquecimento ao religar a instância (cold start), trava rotinas noturnas e mantém intacto o custo de engenharia do servidor de inferência.

## Onde fica o ponto de virada entre assinatura, API e GPU própria?

Para enxergar onde cada escolha vence, simulei as quatro opções somando todas as quatro linhas de custo com as premissas da [planilha de custos de IA](/tools/planilha-custos-ia/) convertidas pela PTAX de R$ 5,8573. A engenharia usa um engenheiro sênior CLT (R$ 17.600 de salário mais 80% de encargos em 168 horas úteis, igual a R$ 188,57 ou US$ 32,19 por hora), com as horas de construção amortizadas em 12 meses mais a manutenção mensal: 880 h de construção e 96 h/mês na API (R$ 31.931/mês), 1.000 h e 120 h/mês na GPU própria (R$ 38.343/mês) e 80 h e 10 h/mês na assinatura (R$ 3.143/mês). A infraestrutura auxiliar entra a US$ 250/mês (R$ 1.464) na API e US$ 500/mês (R$ 2.929) na GPU. A assinatura usa o mix de 60% Standard (US$ 20) e 40% Premium (US$ 100), com 35% de licenças subutilizadas. A GPU usa 1× A100 SXM 40 GB na Lambda (US$ 1,99/h) ligada 24/7 com fator de pico de 8 vezes sobre a média mensal.

No porte médio (50 pessoas e 10 mil consultas por mês), as quatro linhas ficam distribuídas assim:

| Linha de custo (50 pessoas, 10 mil consultas/mês) | API sem otimização | API otimizada | Assinatura (60/40) | GPU própria (1× A100 24/7) |
|---|---:|---:|---:|---:|
| 1. Inferência útil | R$ 2.559 (US$ 437) | R$ 599 (US$ 102) | R$ 9.899 (US$ 1.690) | R$ 79 (US$ 14) |
| 2. Em volta do modelo | R$ 1.464 (US$ 250) | R$ 1.464 (US$ 250) | R$ 0 (US$ 0) | R$ 2.929 (US$ 500) |
| 3. Desperdício (falhas, ociosidade ou licenças paradas) | R$ 42 (US$ 7) | R$ 10 (US$ 2) | R$ 5.330 (US$ 910) | R$ 8.430 (US$ 1.439) |
| 4. Engenharia (construção amortizada + manutenção) | R$ 31.931 (US$ 5.452) | R$ 31.931 (US$ 5.452) | R$ 3.143 (US$ 537) | R$ 38.343 (US$ 6.546) |
| **Total mensal** | **R$ 35.997 (US$ 6.146)** | **R$ 34.005 (US$ 5.806)** | **R$ 18.372 (US$ 3.137)** | **R$ 49.780 (US$ 8.499)** |

Os números acima podem diferir em R$ 1 na soma por arredondamento de centavos (na GPU, a inferência útil desconta a taxa de falha de 1,6%, somando R$ 79,26 em inferência útil e R$ 8.429,64 em ociosidade e retentativas). Na API otimizada, 94% da conta mensal está na engenharia e apenas 1,8% está na fatura de tokens. Na GPU própria, a placa fica ociosa 99,05% do mês para atender 10 mil consultas (utilização média de 0,95%), e ainda carrega R$ 6.412 a mais por mês de engenharia em relação à API.

Variando o porte da empresa sob as mesmas premissas, o custo total mensal por opção muda assim:

| Porte da empresa e volume mensal | Assinatura (60/40) | API sem otimização | API otimizada | GPU própria (1× A100 24/7) |
|---|---:|---:|---:|---:|
| Pequena (20 pessoas, 2 mil consultas/mês) | R$ 9.234 (US$ 1.577) | R$ 33.916 (US$ 5.790) | R$ 33.518 (US$ 5.722) | R$ 49.780 (US$ 8.499) |
| Média (50 pessoas, 10 mil consultas/mês) | R$ 18.372 (US$ 3.137) | R$ 35.997 (US$ 6.146) | R$ 34.005 (US$ 5.806) | R$ 49.780 (US$ 8.499) |
| Grande (200 pessoas, 100 mil consultas/mês) | R$ 64.059 (US$ 10.937) | R$ 59.405 (US$ 10.142) | R$ 39.486 (US$ 6.741) | R$ 49.780 (US$ 8.499) |

O primeiro ponto de virada acontece entre assinatura e API otimizada. Considerando 200 consultas mensais por pessoa (cerca de 10 por dia útil) e engenharia construída dentro de casa, a assinatura no mix 60/40 (US$ 52 médios por pessoa) custa menos para empresas pequenas e médias, e cruza o custo total da API otimizada em 104 usuários (20,7 mil consultas por mês). Acima desse patamar, cada usuário novo na assinatura acrescenta US$ 52 mensais, enquanto o custo marginal da mesma pessoa na API otimizada é de US$ 2,08 (R$ 12,18). Se toda a equipe couber no plano Standard de US$ 20 sem precisar de assentos Premium, o cruzamento com a aplicação própria em API sobe para 289 usuários.

O segundo ponto de virada ocorre entre API e GPU própria, e ele raramente favorece a placa alugada quando a API está bem arquitetada. Com vazão medida de 1.447 respostas por hora e fator de pico de 8 vezes no horário comercial, uma única A100 de 40 GB suporta até 132 mil consultas por mês antes de exigir uma segunda placa para segurar o pico. Olhando só a fatura do modelo contra a API sem otimização (Opus 5.5 puro), o aluguel 24/7 da GPU empata em 32,7 mil consultas por mês; somando infraestrutura e engenharia, o empate sobe para 63 mil consultas por mês. Já contra a API otimizada (US$ 0,0104 por consulta), só o aluguel 24/7 da placa exigiria 139,7 mil consultas por mês para empatar, volume acima do teto de pico de uma única GPU. Ao entrar a segunda placa para evitar fila no meio da manhã, o empate foge de novo.

Mesmo desligando a A100 fora do expediente (220 horas por mês, baixando o aluguel da placa para US$ 438 ou R$ 2.564), o empate olhando apenas a fatura do modelo cai para 42,1 mil consultas mensais contra a API otimizada, mas quando somamos a engenharia adicional de MLOps o empate total volta a subir para 171,4 mil consultas por mês, novamente acima da capacidade de pico de uma placa. Por isso considero que alugar GPU própria em nuvem se justifica por três razões concretas que independem de economia imediata: exigência contratual ou regulatória de que nenhum dado saia do ambiente controlado da empresa, carga contínua distribuída nas 24 horas sem pico agudo, ou uso intensivo de modelos abertos ajustados internamente em volumes acima de centenas de milhares de chamadas por mês.

Quem quiser testar essas contas com o salário da própria equipe, outro mix de modelos ou o valor de uma plataforma pronta pode baixar a [planilha de custos de IA](/tools/planilha-custos-ia/) e trocar apenas as células amarelas.

## Como o câmbio, os impostos e o faturamento no Brasil alteram a conta?

Converter o preço de tabela pela PTAX de R$ 5,8573 (fechamento do Banco Central em 24 de novembro de 2026) dá uma base comparável, mas ainda subestima o desembolso efetivo de empresas brasileiras que pagam direto no exterior. Compras em dólar no cartão corporativo com provedores sem faturamento local, caso das APIs diretas da Anthropic e da OpenAI ou de aluguel de GPU na RunPod e na Lambda, pagam IOF de 3,5% (alíquota fixada pelos Decretos 12.466 e 12.499/2025, que encerraram o cronograma anterior de redução gradual) somado ao spread cambial do banco emissor, além de não gerarem nota fiscal brasileira para aproveitamento contábil simples.

Empresas que precisam de faturamento local em reais com nota fiscal costumam resolver esse atrito por três caminhos disponíveis no país. O Google Workspace cobra em reais direto na tabela brasileira (R$ 40,90, R$ 81,80 e R$ 128,40 por usuário no plano anual, sem impostos). Para consumo de API e inferência, a AWS Brasil (onde o Amazon Bedrock é serviço nativo e consolida o consumo de modelos Claude na mesma fatura em reais emitida pela AWS Serviços Brasil com NFS-e, diferentemente de licenças compradas no AWS Marketplace) e o Google Cloud Brasil (Vertex AI e Compute Engine) permitem faturamento local em reais. Faturar no Brasil substitui o IOF e o spread do cartão por tributos locais embutidos na nota fiscal de serviços (PIS, COFINS e ISS), então o financeiro precisa comparar o valor líquido após créditos fiscais de cada regime.

Vale checar o ágio regional antes de cravar o provedor local. No Google Cloud, a instância `g2-standard-4` com uma L4 em São Paulo (`southamerica-east1`) custa US$ 0,71 por hora, praticamente empatada com US$ 0,70 em `us-central1`. Já na `a2-highgpu-1g` com uma A100 de 40 GB, São Paulo cobra US$ 4,27 por hora contra US$ 3,67 nos Estados Unidos, um acréscimo de 16%. No lado das assinaturas, a página brasileira do ChatGPT Business exibe R$ 100 por mês no assento Standard anual e R$ 500 no Premium anual, valores abaixo da conversão direta de US$ 20 e US$ 100 pela PTAX atual, o que melhora ligeiramente a conta local da OpenAI frente ao Claude Team cobrado em dólar.

## Qual o método e quais são os limites desta conta?

O tamanho médio de consulta (8.981 tokens de entrada e 424 de saída em quatro chamadas) saiu dos logs de uma bateria de 224 perguntas sobre dados empresariais rodada em 6 de outubro de 2026 no TalkWithData. A diferença entre despejar uma planilha de 5 mil linhas no contexto (447.568 tokens) e filtrar com SQL (554 tokens) foi medida em 22 de novembro de 2026 no endpoint de contagem de tokens da Anthropic. A vazão da GPU (1.447 respostas completas por hora com p90 de 10,1 segundos) foi medida em 23 de novembro de 2026 com Qwen3-8B em `bf16` no `vLLM` rodando numa A100 SXM de 40 GB com quatro perguntas simultâneas. Repito a declaração de conflito de interesse: eu construo o TalkWithData, e todas as contas de API, assinatura e GPU própria neste texto funcionam de forma independente dele.

O teste de GPU mediu um modelo aberto de 8 bilhões de parâmetros (Qwen3-8B), que cabe com folga numa A100 de 40 GB mas entrega menos capacidade de raciocínio do que um modelo de fronteira como o Claude Opus 5.5 ou o GPT-6.2 Astra. Subir para um modelo aberto de 70 bilhões de parâmetros ou mais exige placas de 80 GB ou nós multi-GPU, o que multiplica o custo mensal da nuvem.

Cada provedor usa um tokenizador próprio, e até o mesmo provedor altera a contagem entre gerações: a própria Anthropic documenta que modelos a partir do Claude 4.7 geram cerca de 30% mais tokens para o mesmo texto. Transportar o mesmo volume de tokens para outro modelo ou provedor sem medir de novo introduz desvio na conta.

As horas de engenharia (880 h iniciais e 96 h/mês na API, 1.000 h e 120 h/mês na GPU) e o custo de R$ 188,57 por hora são premissas declaradas para uma operação típica brasileira. Esse valor muda bastante se a empresa já tem equipe de dados estruturada ou se contrata uma plataforma pronta que absorve a camada de permissões, auditoria e conectores.

Por fim, preços de API e GPU mudam sem aviso, como mostram os reajustes já anunciados pelo Google para Gemini 4 em 1º de dezembro de 2026 e Gemini 3.8 Flash em 1º de janeiro de 2027. Descontos de contrato anual (CUD ou Savings Plans) ou instâncias Spot reduzem o aluguel da nuvem ao custo de compromisso de prazo ou risco de interrupção.

Antes de assinar qualquer orçamento de IA para os dados da empresa, eu sempre pergunto qual das quatro linhas domina o custo no primeiro ano. Na proposta que chegou à sua mesa, as horas de engenharia e a ociosidade fora do pico entraram na mesma planilha que o preço por milhão de tokens?

## Perguntas frequentes

### Quanto custa manter uma IA conectada ao banco de dados de uma empresa média?

Numa empresa de 50 pessoas rodando 10 mil consultas por mês com arquitetura própria em API otimizada, a fatura de tokens fica perto de R$ 609 por mês (US$ 104). Somando infraestrutura auxiliar (R$ 1.464) e a engenharia para construir e manter permissões, auditoria, testes de acerto e conectores (R$ 31.931/mês no primeiro ano, amortizando a construção em 12 meses), o custo total fica em torno de R$ 34 mil por mês. A [planilha de custos de IA](/tools/planilha-custos-ia/) permite recalcular esse total com o salário e o volume da sua empresa.

### Vale a pena alugar GPU própria para economizar com API?

Por economia pura, raramente compensa para empresas com uso concentrado em horário comercial. Uma A100 de 40 GB ligada 24/7 custa R$ 8.509 por mês na Lambda (ou R$ 18.258 no Google Cloud em São Paulo) e fica ociosa mais de 90% do tempo até 100 mil consultas mensais. Contra uma API otimizada com roteamento e cache (R$ 0,06 por consulta no perfil medido), a placa nunca empata antes de saturar no pico de uso. A GPU própria faz sentido quando há exigência contratual de soberania de dados, tráfego constante nas 24 horas ou uso pesado de modelos abertos ajustados internamente.

### A partir de quantos usuários a API fica mais barata do que ChatGPT Business ou Claude Team?

Na simulação com 200 consultas mensais por pessoa e engenharia própria, a assinatura no mix 60% Standard e 40% Premium (US$ 52 médios por usuário) passa a custar mais do que a API otimizada a partir de 104 usuários. Se toda a empresa usar apenas o assento Standard de US$ 20, o empate sobe para 289 usuários. Olhando apenas o consumo do modelo por pessoa sem construir software do zero, 200 consultas na API otimizada custam US$ 2,08 (R$ 12,18), bem abaixo de qualquer assento mensal.

### Qual modelo de IA tem o melhor custo-benefício em API hoje?

A estratégia mais barata e confiável que vejo em produção combina um modelo rápido de centavos por milhão para classificação, extração e roteamento (como Claude Haiku 5.5 ou GPT-6 Luna a US$ 0,10 de entrada e US$ 0,50 de saída, ou Gemini 3.5 Flash-Lite a US$ 0,30 e US$ 2,50) com um modelo intermediário ou de fronteira apenas para as etapas de raciocínio difícil (como Claude Sonnet 5.5 ou GPT-6.2 Sol a US$ 2,00 e US$ 10,00, ou Claude Opus 5.5 a US$ 4,00 e US$ 20,00).

### Como pagar APIs de IA em reais com nota fiscal no Brasil?

Empresas brasileiras contratam os mesmos modelos por nuvens com faturamento local: Amazon Bedrock na AWS Serviços Brasil (serviço nativo que consolida o consumo de modelos Claude na fatura em reais com NFS-e) e Vertex AI no Google Cloud Brasil para a família Gemini e modelos hospedados. Essa via substitui o IOF de 3,5% e o spread do cartão corporativo internacional pelos tributos locais embutidos na nota fiscal de serviços (PIS, COFINS e ISS). Em planos por usuário, o Google Workspace já pratica tabela oficial em reais no Brasil, e o ChatGPT Business exibe valores em reais na página brasileira para contratação anual.

---

Se você está fechando o orçamento de um projeto de IA na sua empresa e quer revisar a conta antes de escolher o caminho, [agende uma conversa comigo](https://calendar.app.google/VyG67Nq5p6zsstAt8).
