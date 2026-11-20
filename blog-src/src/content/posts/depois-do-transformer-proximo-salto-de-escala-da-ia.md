---
title: "Depois do transformer: onde está o próximo salto de escala da IA"
subtitle: "O transformer e o tensor em GPU viraram salto porque abriram um eixo de escala previsível e rodavam na máquina que existia. Apliquei os dois testes aos candidatos de 2026: o salto que já aconteceu, a continuação do tensor, a física por baixo e a aposta que mais lembra 2017."
description: "Os candidatos de 2026 ao próximo salto de escala da IA, julgados pelos dois testes que fizeram do transformer e do tensor em GPU um salto: eixo de escala novo e casamento com o hardware."
date: 2026-11-18T12:00:00Z
slug: depois-do-transformer-proximo-salto-de-escala-da-ia
cover: img/depois-do-transformer-proximo-salto-de-escala-da-ia/capa.png
type: newsletter
---

Em agosto publiquei aqui a tradução de um [diário de trabalho que vai do GPT-2 ao Kimi K3](/blog/22580-do-gpt-2-ao-kimi-k3-explicado/). O autor conta 22.580 modelos GPT-2 dentro do Kimi K3 e mostra, camada por camada, que sete anos de avanço vieram quase todos de escala sobre o mesmo núcleo: a atenção ficou mais barata e, em parte das camadas, linear, mas o sistema continua sendo um transformer que roda multiplicação de matrizes numa GPU. Era um retrato do que não mudou.

Neste ensaio procuro a tecnologia que pode fazer pela próxima fase da IA o que o transformer, em 2017, e a computação com tensores em GPU fizeram pela fase atual. Levantei 20 candidatos em arquitetura, representação numérica, hardware e treino, cada um com a fonte primária, a evidência de 2025 e 2026 e o melhor argumento contra. Concluo que o salto mais provável vem da composição de frentes que já rodam no hardware de hoje, e que a aposta que mais lembra 2017 ainda não tem evidência para sustentar a expectativa que carrega.

## 01. O que fez do transformer um salto

Redes recorrentes já modelavam linguagem antes de 2017. O transformer trocou a leitura do texto palavra por palavra pela atenção sobre todas as posições ao mesmo tempo, e com isso o treino virou um problema de multiplicar matrizes grandes, a operação que uma GPU executa melhor do que qualquer outra. A partir daí, mais dados e mais computação passaram a render melhoria previsível, e as leis de escala viraram planilha de orçamento.

Desse precedente tiro os dois testes que aplico a cada candidato. O primeiro pergunta se a tecnologia abre um eixo de escala novo, em que gastar mais de algum recurso rende capacidade de forma previsível. O segundo pergunta se ela casa com o hardware que existe, ou com o que a indústria já aceita fabricar. A pesquisadora Sara Hooker chamou o segundo efeito de [loteria do hardware](https://arxiv.org/abs/2009.06489): ideias costumam vencer porque rodam bem na máquina disponível, e perder quando dependem de uma máquina que ainda não existe.

<figure>
<img src="/blog/img/depois-do-transformer-proximo-salto-de-escala-da-ia/01-dois-testes-1456.webp" srcset="/blog/img/depois-do-transformer-proximo-salto-de-escala-da-ia/01-dois-testes-728.webp 728w, /blog/img/depois-do-transformer-proximo-salto-de-escala-da-ia/01-dois-testes-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="816" alt="Matriz com dois eixos. Horizontal: casa com o hardware de hoje. Vertical: eixo de escala novo comprovado. No quadrante dos dois sim, computação na inferência com aprendizado por reforço. Com hardware sim e eixo novo parcial, esparsidade e baixa precisão, e interconexão óptica e silício de inferência. Com eixo potencial e hardware incerto, memória no momento do teste. Com hardware sim e sem eixo novo, difusão para texto. Sem os dois, redes de 1 bit, fotônica para computação, analógica, termodinâmica, neuromórfica e quântica." loading="lazy" decoding="async">
<figcaption><em>Fig. 1. Os candidatos de 2026 pelos dois testes do transformer. Quase todos os que têm evidência forte ficam do lado do hardware que já existe.</em></figcaption>
</figure>

## 02. O salto que já aconteceu: pensar mais na hora de responder

O único candidato que já passou nos dois testes chegou em setembro de 2024, quando a OpenAI apresentou o o1 e mostrou que o desempenho [cresce com a computação gasta na hora da resposta](https://openai.com/index/learning-to-reason-with-llms/), além da computação gasta no treino. O mecanismo é treinar o modelo com aprendizado por reforço em tarefas cuja resposta pode ser verificada por código, como matemática e testes de software, até ele produzir raciocínios longos que se corrigem no caminho. Na hora de usar, o modelo pode pensar mais, gerar várias tentativas e conferir as próprias respostas.

Entrar nesse eixo custou pouco. Segundo o artigo da DeepSeek na Nature, de setembro de 2025, o aprendizado por reforço do R1 custou cerca de [US$ 294 mil em 512 GPUs H800](https://www.scientificamerican.com/article/secrets-of-chinese-ai-model-deepseek-revealed-in-landmark-paper/), sem contar os cerca de US$ 6 milhões do modelo base e sem verificação independente do número. O efeito sobre a capacidade aparece no ARC-AGI-3, um teste de raciocínio interativo em que humanos sem treino resolvem todos os ambientes. Ele foi lançado em março de 2026 com [todos os modelos de fronteira abaixo de 1%](https://the-decoder.com/arc-agi-3-offers-2m-to-any-ai-that-matches-untrained-humans-yet-every-frontier-model-scores-below-1/). Em novembro, o primeiro colocado do [placar oficial](https://arcprize.org/leaderboard) marca 81,2%, com custo listado na casa de dezenas de milhares de dólares.

O eixo é novo e roda nas mesmas GPUs, por isso considero que o salto já aconteceu entre 2024 e 2026, e o que está em aberto é o fôlego dele. Toby Ord estimou que [dez vezes mais computação de reforço rende o mesmo ganho que três vezes mais computação de inferência](https://www.tobyord.com/writing/how-well-does-rl-scale), e que o primeiro milhão de vezes de aumento saiu quase de graça porque partia de quase nada. Um estudo de abril de 2025 mostrou que o reforço com recompensa verificável [melhora a primeira tentativa sem ampliar o que o modelo base consegue resolver quando pode tentar muitas vezes](https://arxiv.org/abs/2504.13837). Para quem planeja orçamento, a tarefa difícil vai ficar mais cara por resposta, mesmo com o token ficando mais barato.

## 03. A continuação do tensor: menos parâmetros ativos e menos bits

A segunda frente tem menos glamour e, na minha leitura, o maior efeito sobre custo nos próximos anos. Ela soma técnicas que reduzem quanto do modelo trabalha em cada token e quantos bits cada número ocupa.

A mistura de especialistas divide cada camada em muitos blocos e ativa poucos por token. O [DeepSeek-V4-Pro](https://arxiv.org/abs/2606.19348), publicado em abril de 2026, tem 1,6 trilhão de parâmetros e ativa 49 bilhões, cerca de 3%. O mesmo modelo usa atenção esparsa, em que um índice barato escolhe quais trechos do contexto cada consulta deve ler, e com um milhão de tokens de contexto ele precisa de 27% da computação por token e de 10% da memória de contexto da versão anterior. A [Moonshot](https://arxiv.org/abs/2510.26692) seguiu outro caminho para o mesmo fim, intercalando camadas lineares com camadas de atenção completa, e relatou até 75% menos memória de contexto e até seis vezes mais velocidade de geração em um milhão de tokens.

Nos bits, a NVIDIA treinou um modelo de 12 bilhões de parâmetros com 10 trilhões de tokens [com a maior parte das operações em números de 4 bits](https://arxiv.org/abs/2509.25149), com perda comparável à do treino em 8 bits, e depois pré-treinou o [Nemotron 3 Super](https://arxiv.org/abs/2604.12374), de 120 bilhões de parâmetros, no mesmo formato, sobre 25 trilhões de tokens. Cada vez que o número cai pela metade, a vazão do chip dobra e a memória necessária cai pela metade.

<figure>
<img src="/blog/img/depois-do-transformer-proximo-salto-de-escala-da-ia/02-composicao-1456.webp" srcset="/blog/img/depois-do-transformer-proximo-salto-de-escala-da-ia/02-composicao-728.webp 728w, /blog/img/depois-do-transformer-proximo-salto-de-escala-da-ia/02-composicao-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="816" alt="Gráfico de barras com a fração que o modelo usa em relação à referência. Parâmetros ativos por token: 3%, no DeepSeek-V4-Pro, com 49 bilhões de 1,6 trilhão. Computação por token com um milhão de tokens de contexto: 27%, do V4-Pro contra o V3.2. Memória de contexto com um milhão de tokens: 10%, do V4-Pro contra o V3.2. Bits por número: 50%, com 4 bits contra 8 no treino em NVFP4." loading="lazy" decoding="async">
<figcaption><em>Fig. 2. Cada técnica corta uma parte do custo, e os cortes se multiplicam no mesmo modelo.</em></figcaption>
</figure>

Nenhuma dessas técnicas é nova no conceito, e a mistura de especialistas foi descrita em 2017. O salto está na composição: menos parâmetros ativos, menos computação por token, menos memória de contexto e menos bits se multiplicam, e pela minha conta o produto pode chegar a uma ordem de grandeza de custo por token em poucos anos. Mas a MiniMax [abandonou a atenção híbrida e voltou à atenção completa](https://www.lmsys.org/blog/2025-11-04-miminmax-m2/) depois de ver déficits em raciocínio de vários passos em escala maior, e o formato de 4 bits já exige truques numéricos e camadas de exceção, sinal de que o piso está perto.

## 04. A física por baixo: luz entre os chips e silício para responder rápido

Um modelo com trilhões de parâmetros espalhados em especialistas obriga dezenas de GPUs a conversar o tempo todo, e a rede entre elas vira o gargalo. Por isso a óptica co-empacotada, que leva o transceptor de luz para dentro do encapsulamento do switch, entrou no roteiro dos fabricantes. A NVIDIA anunciou em março de 2025 [switches com óptica co-empacotada](https://nvidianews.nvidia.com/news/nvidia-spectrum-x-co-packaged-optics-networking-switches-ai-factories) e afirma ganho de 3,5 vezes em eficiência de energia, e a Marvell [comprou a Celestial AI por cerca de US$ 3,25 bilhões](https://www.financierworldwide.com/fw-news/2025/12/4/marvell-to-acquire-celestial-ai-in-325bn-deal) em dezembro do mesmo ano.

A latência ganhou silício próprio. Em janeiro de 2026 a OpenAI fechou com a Cerebras um [contrato de 750 megawatts](https://www.cerebras.ai/blog/openai-partners-with-cerebras-to-bring-high-speed-inference-to-the-mainstream), e em fevereiro lançou o [GPT-5.3-Codex-Spark](https://openai.com/index/introducing-gpt-5-3-codex-spark/), o primeiro modelo dela servido fora de GPUs NVIDIA, a mais de mil tokens por segundo. No mesmo anúncio, a OpenAI diz que a GPU continua sendo o token mais barato para uso amplo. O chip do tamanho de um wafer compra velocidade, e ela importa para agentes que encadeiam milhares de passos.

A luz entra na IA pela rede entre os chips, enquanto a conta continua no silício. A Lightmatter publicou na Nature um [processador que multiplica matrizes com luz](https://www.nature.com/articles/s41586-025-08854-x) e roda modelos reais, com 65,5 trilhões de operações por segundo a 78 watts, eficiência que pela minha conta fica abaixo da de uma GPU atual, e o produto que a empresa vende é interconexão.

## 05. A aposta que mais lembra 2017: o modelo que aprende enquanto responde

Se alguma tecnologia da lista tem o perfil do transformer, é a memória no momento do teste. A ideia, presente no [Titans](https://arxiv.org/abs/2501.00663) e no [Nested Learning](https://research.google/blog/introducing-nested-learning-a-new-ml-paradigm-for-continual-learning/), ambos do Google, é deixar parte dos pesos ser atualizada durante a própria resposta, comprimindo o contexto em parâmetros em vez de guardá-lo numa memória que cresce. O [TTT-E2E](https://arxiv.org/abs/2512.23675), de dezembro de 2025, mostrou em modelos de 3 bilhões de parâmetros que essa abordagem melhora com o tamanho do contexto como a atenção completa, com latência constante e 2,7 vezes mais rápida que a atenção completa em 128 mil tokens.

Hoje um modelo é congelado no fim do treino e recebe o mundo pelo contexto. Um modelo que atualiza os próprios pesos acumula competência no trabalho, que é a definição de aprendizado contínuo, e deixaria de ter data de corte do conhecimento.

Os resultados vão até cerca de 3 bilhões de parâmetros e vêm dos próprios autores, e não encontrei modelo de fronteira que atualize pesos em produção. Memórias recorrentes têm um histórico conhecido de empatar na média e perder em recuperação exata e em raciocínio de vários passos, o mesmo problema que levou a MiniMax de volta à atenção completa, e o gradiente calculado dentro da inferência paraleliza pior na GPU, o que pesa contra na loteria do hardware. Considero essa a aposta de maior potencial da lista e a de menor evidência.

## 06. O que ainda não passa nos testes

A difusão para texto gera blocos inteiros em paralelo em vez de um token por vez. O Mercury Coder, da Inception, foi medido pela Artificial Analysis a [1.109 tokens por segundo](https://arxiv.org/abs/2506.17298), e o próprio Google admite que o DiffusionGemma fica abaixo do Gemma 4 autorregressivo em qualidade. O ganho é de latência para tarefas curtas, sem eixo novo de capacidade.

As redes de pesos ternários, como o [BitNet b1.58](https://arxiv.org/abs/2402.17764) da Microsoft, trocam multiplicação por soma e usam cerca de dez vezes menos memória que a meia precisão. Elas empatam com a meia precisão a partir de uns 3 bilhões de parâmetros, mas nenhum laboratório treinou um modelo ternário grande e competitivo, e a demonstração de um modelo de 100 bilhões rodando em CPU usou um modelo fictício, sem treino.

Outros quatro candidatos multiplicam o que já existe sem abrir eixo novo. O treino distribuído com pouca comunicação, como o DiLoCo, usa [mais de cem vezes menos comunicação](https://arxiv.org/abs/2503.09799) que o treino convencional e já treinou modelos em [datacenters de regiões diferentes](https://deepmind.google/blog/decoupled-diloco/), o que ajuda a contornar o limite de energia de um único local. Os dados sintéticos rendem até 1,8 vez mais aproveitamento dos dados [em modelos pequenos](https://arxiv.org/abs/2603.18534), e o texto gerado do zero [mostra sinais de colapso](https://arxiv.org/abs/2510.01631). O raciocínio em espaço latente, sem escrever a cadeia de pensamento, [fica abaixo da cadeia explícita acima de 1 bilhão de parâmetros](https://arxiv.org/abs/2606.31779). E os modelos de mundo, como o [V-JEPA 2](https://arxiv.org/abs/2506.09985) da Meta, miram robótica e vídeo, outro domínio, ainda sem resultado que supere os modelos generativos em escala.

A computação analógica em memória, a termodinâmica, a neuromórfica e a quântica dividem o mesmo problema. Os ganhos de várias ordens de grandeza em energia aparecem em [projeções de circuito em escala GPT-2](https://www.nature.com/articles/s43588-025-00854-1) ou em [simulações com bases de imagens pequenas](https://arxiv.org/abs/2510.23972), o chip termodinâmico da Extropic não roda transformer, e a computação quântica ainda não mostrou vantagem em hardware para aprendizado de máquina. Todas exigem trocar o modelo e a máquina ao mesmo tempo, e esse é o tipo de aposta que a loteria do hardware costuma punir.

## 07. O que eu levaria para o planejamento

As tecnologias com evidência forte rodam no hardware que já existe ou entram no catálogo da NVIDIA, que absorveu os 4 bits, a óptica co-empacotada e, com o [licenciamento da tecnologia da Groq](https://www.tomshardware.com/tech-industry/semiconductors/nvidia-confirms-20-billion-groq-deal-to-bolster-ai-inference-dominance) em dezembro de 2025, o silício de baixa latência.

Na prática, planejaria duas curvas de custo em vez de uma. O custo por token tende a cair por composição, à medida que os modelos ativam menos parâmetros e usam menos bits, e os provedores repassam parte disso no preço. O custo por tarefa difícil tende a subir, porque o eixo que mais rendeu capacidade nos últimos dois anos é o de pensar mais, e pensar mais custa tokens. Orçamentos que medem só o preço do token vão errar nas duas direções.

Também trataria latência como produto separado. Com silício dedicado servindo o mesmo tipo de modelo a mais de mil tokens por segundo, uma tarefa que hoje leva minutos num agente pode levar segundos, e isso muda o que vale automatizar. E revisaria desenhos de sistema que existem só para economizar contexto, porque um milhão de tokens a 10% da memória de antes deixa de ser luxo.

Por fim, eu não apostaria a arquitetura de um produto em substrato exótico antes de ele rodar o modelo dominante. Acompanharia de perto a memória no momento do teste, que é o único candidato capaz de mudar o que é um modelo, e por isso o que mais pede preparo de governança.

Se o próximo salto for o modelo que aprende enquanto trabalha, quem na sua empresa vai decidir o que ele pode aprender?
