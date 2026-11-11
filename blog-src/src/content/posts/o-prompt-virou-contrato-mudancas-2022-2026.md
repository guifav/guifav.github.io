---
title: "O prompt virou contrato: o que quatro anos de papers e documentação oficial ensinam sobre conversar com a IA"
subtitle: "O que em 2022 era acurácia, hoje é desperdício documentado: o raciocínio entrou para dentro do modelo, o controle fino virou parâmetro de API e a engenharia migrou do texto para o contexto"
description: "O que em 2022 era acurácia, hoje é desperdício documentado: o raciocínio entrou para dentro do modelo, o controle fino virou parâmetro de API e a engenharia migrou do texto para o contexto"
date: 2026-07-28T20:04:10.135Z
updated: 2026-07-29T00:12:11.282Z
slug: o-prompt-virou-contrato-mudancas-2022-2026
cover: img/o-prompt-virou-contrato-mudancas-2022-2026/capa.jpg
type: newsletter
---

Em junho de 2023 eu publiquei aqui um [guia com seis dicas para escrever bons prompts](/blog/dicas-prompts-chat-gpt/). Uma delas recomendava dar tempo ao modelo para pensar, pedindo que ele explicasse o raciocínio passo a passo antes de responder. Era o estado da arte da época, alinhado com as melhores práticas publicadas pela própria OpenAI.

Em julho de 2026, a documentação oficial da mesma OpenAI traz a instrução inversa, com todas as letras: “Avoid chain-of-thought prompts”. Como os modelos atuais raciocinam internamente, pedir que pensem passo a passo é desnecessário. A Anthropic foi na mesma direção e hoje pede que o usuário apague instruções que mandam o modelo verificar o próprio trabalho: elas causam verificação em excesso e desperdiçam tokens sem ganho de qualidade. O conselho que em 2022 somava dezenas de pontos de acurácia virou, na documentação de 2026, um defeito a remover.

Na semana passada fiz um levantamento das documentações oficiais da OpenAI e da Anthropic para atualizar meu manual interno. Este ensaio cruza esse levantamento com o que a literatura científica registrou no mesmo período: são quase 40 papers, a maioria no arXiv, cada número citado aqui (final do texto tem a lista completa). A tese central cabe numa frase: **o prompt continua no centro do trabalho, com outra natureza.**

> O prompt deixou de ser a aula que ensina o modelo a pensar e virou o contrato que especifica comportamento, fronteiras e grau de autonomia.

Organizei o argumento, com auxilio do meu copiloto Hermes em dez pontos (inclusive fez esse levantamento de artigos e os categorizou, facilitando minha leitura). Os três primeiros reconstroem a era do prompt-aula com os números originais. Os pontos 04 a 06 documentam a virada: o raciocínio entrando para dentro do modelo, a instrução virando cláusula e a técnica virando parâmetro. Os pontos 07 a 09 mostram para onde a engenharia foi, o que os logs de uso real revelam sobre as pessoas e o que o mercado de trabalho fez com o cargo. O ponto 10 fecha com o que continua valendo e com as lições práticas para a sua empresa.

## 01. A era do prompt-aula: quando a frase valia 61 pontos

O ponto de partida é 2020, quando o paper do GPT-3 ([Brown et al., arXiv 2005.14165](https://arxiv.org/abs/2005.14165)) demonstrou que um modelo de 175 bilhões de parâmetros aprendia tarefas novas “sem qualquer atualização de gradiente ou fine-tuning”, apenas com demonstrações escritas no próprio texto de entrada. Nascia o few-shot prompting: ensinar pelo exemplo, dentro da janela de contexto.

Dois anos depois, o chain-of-thought completou o método. [Wei et al. (arXiv 2201.11903)](https://arxiv.org/abs/2201.11903) mostraram que oito exemplos com o raciocínio escrito por extenso levavam o PaLM 540B ao estado da arte no GSM8K, benchmark de problemas matemáticos: 56,9% de acerto nas tabelas do paper, superando os 55% de um GPT-3 ajustado com verificador externo. A versão mais famosa veio de [Kojima et al. (arXiv 2205.11916)](https://arxiv.org/abs/2205.11916): sem exemplo nenhum, a frase “Let’s think step by step” levou o text-davinci-002 de 17,7% para 78,7% no MultiArith e de 10,4% para 40,7% no GSM8K. Uma frase, 61 pontos percentuais de diferença. Logo depois, a self-consistency ([Wang et al., arXiv 2203.11171](https://arxiv.org/abs/2203.11171)) somou mais 17,9 pontos no GSM8K amostrando vários caminhos de raciocínio e votando na resposta majoritária.

<figure>
<img src="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/259420485a-1456.webp" srcset="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/259420485a-728.webp 728w, /blog/img/o-prompt-virou-contrato-mudancas-2022-2026/259420485a-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>Em 2022, o caminho do raciocínio vinha desenhado de fora: o prompt carregava os passos que o modelo deveria percorrer.</em></figcaption>
</figure>

Vale nomear o que esses números significavam na prática. O procedimento de raciocínio morava no prompt, escrito por um humano. Quem escrevia melhor a aula extraía mais do mesmo modelo, e a diferença entre uma aula boa e uma ruim era da ordem de dezenas de pontos. Foi esse diferencial que criou a disciplina, os cursos, os manuais internos e, por um breve período, o cargo de prompt engineer com salário de seis dígitos.

## 02. O folclore instrumentado: emoção, gorjeta e Jornada nas Estrelas

Entre 2023 e 2024, a literatura registrou uma fase curiosa: técnicas que nasciam da intuição social humana e funcionavam sem que ninguém soubesse explicar o mecanismo.

O EmotionPrompt ([Li et al., arXiv 2307.11760](https://arxiv.org/abs/2307.11760)) mediu o efeito de anexar apelos emocionais ao fim do prompt, como “isto é muito importante para a minha carreira”: 8% de melhora relativa em tarefas de instrução e 115% no BIG-Bench, com validação humana em 106 participantes. A polidez também virou variável experimental: [Yin et al. (arXiv 2402.14531)](https://arxiv.org/abs/2402.14531) mostraram que prompts rudes degradavam o desempenho (o Llama-2-70B caiu de 55,11 para 28,44 no MMLU entre o nível mais polido e o mais rude) e que o nível ótimo de cortesia variava por idioma. Um paper da MBZUAI catalogou [26 princípios de prompting](https://arxiv.org/abs/2312.16171), entre eles o de prometer gorjeta ao modelo. E as personas, técnica queridinha dos manuais (”você é um especialista em...”), foram testadas em escala por [Zheng et al. (arXiv 2311.10054)](https://arxiv.org/abs/2311.10054): 162 personas, 9 modelos, 2.410 questões factuais, e a conclusão de que adicionar persona no system prompt tem efeito “em grande parte aleatório” sobre a acurácia, com a seleção automática da melhor persona rendendo o mesmo que sorteio.

O episódio que melhor resume a fase veio da otimização automática. A máquina já superava o artesão desde 2022, quando o APE ([arXiv 2211.01910](https://arxiv.org/abs/2211.01910)) encontrou uma variação de “Let’s think step by step” que rendia mais que a original (82,0 contra 78,7 no MultiArith). Em 2023, pesquisadores do Google DeepMind usaram o próprio modelo para buscar prompts melhores ([OPRO, arXiv 2309.03409](https://arxiv.org/abs/2309.03409)) e a busca encontrou “Take a deep breath and work on this problem step-by-step”: 80,2% no GSM8K com PaLM 2-L, contra 71,8% da frase humana consagrada. Já a dupla da VMware ([Battle e Gollapudi, arXiv 2402.10949](https://arxiv.org/abs/2402.10949)) rodou otimização automática em modelos abertos e reportou que o prompt de melhor pontuação para o Llama2-70B na sua bateria era uma cena de Jornada nas Estrelas, com a resposta começando por “Captain’s Log, Stardate”: um framing que nenhum humano escreveria de propósito. As duas conclusões do paper anteciparam o fim da era: os resultados de cada truque “não generalizam universalmente entre modelos”, e o método mais eficaz é deixar o otimizador automático escrever o prompt. A IEEE Spectrum leu o recado e publicou, ainda em março de 2024, o obituário provocativo: [“AI prompt engineering is dead”](https://spectrum.ieee.org/prompt-engineering-is-dead).

<figure>
<img src="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/b4dad589fe-1456.webp" srcset="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/b4dad589fe-728.webp 728w, /blog/img/o-prompt-virou-contrato-mudancas-2022-2026/b4dad589fe-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>A sintonia fina de 2023: respire fundo, gorjeta, apelo emocional, persona. Ganhos reais, mecanismo desconhecido, transferência nenhuma.</em></figcaption>
</figure>

## 03. A fragilidade era o fundamento do ofício

Por que truques tão arbitrários mexiam tanto no resultado? Porque os modelos daquela geração eram estruturalmente sensíveis à superfície do texto, e a ciência mediu essa sensibilidade com precisão desconfortável.

[Sclar et al. (arXiv 2310.11324)](https://arxiv.org/abs/2310.11324) mostraram que mudanças triviais de formatação (espaços, separadores, dois-pontos) produziam diferenças de até 76 pontos de acurácia no mesmo modelo com o mesmo conteúdo, e que a sensibilidade persistia em modelos maiores e instruídos. [Cao et al. (arXiv 2406.10248)](https://arxiv.org/abs/2406.10248) mediram o pior caso entre paráfrases semanticamente equivalentes do mesmo pedido: 45,48 pontos de distância entre a melhor e a pior formulação no Llama-2-70B-chat, com o pior caso chegando a 9,38% de acerto. O formato do dado importava tanto quanto o texto: [He et al. (arXiv 2411.10541)](https://arxiv.org/abs/2411.10541) registraram variações de até 40% no GPT-3.5 em tarefas de código conforme o prompt vinha em texto puro, Markdown, JSON ou YAML, com o formato ótimo de um modelo valendo pouco para o vizinho. Até a posição da informação dentro da janela pesava: o clássico [Lost in the Middle (arXiv 2307.03172)](https://arxiv.org/abs/2307.03172) mostrou desempenho alto quando a informação relevante estava no início ou no fim do contexto e degradação significativa quando estava no meio.

<figure>
<img src="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/8b85b218a5-1456.webp" srcset="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/8b85b218a5-728.webp 728w, /blog/img/o-prompt-virou-contrato-mudancas-2022-2026/8b85b218a5-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>O mesmo conteúdo, outra pontuação: na geração 2023, espaços e separadores moviam o ponteiro em até 76 pontos.</em></figcaption>
</figure>

Enquanto isso, o estudo de HCI mais citado da época ([Why Johnny Can’t Prompt, CHI 2023](https://dl.acm.org/doi/10.1145/3544548.3581388)) observava não especialistas tentando escrever prompts e concluía que eles exploravam “de forma oportunista, sem sistematização”, travados por expectativas herdadas da instrução entre humanos. A fotografia completa da era é essa: um artefato instável, operado por intuição social, onde o domínio da superfície do texto valia dezenas de pontos. A habilidade era real. O que ninguém sabia era quanto tempo ela duraria.

## 04. 2024: o raciocínio entra para dentro do modelo

A virada tem data e paper. Em setembro de 2024 a OpenAI lançou o o1, primeiro modelo de fronteira treinado para gerar cadeias longas de raciocínio por conta própria, e dois meses depois a Microsoft publicou o estudo que mede o que isso fez com as técnicas clássicas ([From Medprompt to o1, arXiv 2411.03590](https://arxiv.org/abs/2411.03590)). O resultado central: sem técnica nenhuma, o o1-preview marcou 96,0% no MedQA, acima dos 90,2% do GPT-4 Turbo equipado com Medprompt, o pipeline de prompting médico mais sofisticado da geração anterior. E o achado mais importante vinha em seguida: “few-shot prompting hinders o1’s performance”, exemplos no prompt pioraram o desempenho do modelo de raciocínio, sugerindo que o in-context learning deixou de ser um mecanismo eficaz de direcionamento para essa família. A técnica fundadora de 2020 virou interferência.

O mesmo período trouxe a meta-análise que dimensionou o chain-of-thought: [Sprague et al. (arXiv 2409.12183)](https://arxiv.org/abs/2409.12183) revisaram mais de 100 papers e concluíram que os ganhos se concentram em matemática e raciocínio simbólico, com efeito pequeno no resto; no MMLU, responder direto empatava com o passo a passo, salvo quando a questão continha um sinal de igual. Em 2025, o grupo de Wharton confirmou a tendência nos modelos novos ([arXiv 2506.07142](https://arxiv.org/abs/2506.07142)): para modelos de raciocínio, pedir o passo a passo rende ganho marginal de acurácia com aumento substancial de tempo e tokens, porque eles já raciocinam por padrão.

<figure>
<img src="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/ada708da79-1456.webp" srcset="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/ada708da79-728.webp 728w, /blog/img/o-prompt-virou-contrato-mudancas-2022-2026/ada708da79-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>O andaime que sustentava o raciocínio ficava do lado de fora, escrito no prompt. A partir de 2024, a estrutura passou a vir de fábrica, dentro do casco.</em></figcaption>
</figure>

O pêndulo chegou a passar do ponto. A literatura de overthinking mediu o novo defeito: diante de “2+3=?”, um modelo de raciocínio típico gerou 13 soluções alternativas e 901 tokens, quando mais de 92% das vezes a primeira solução já estava correta ([arXiv 2412.21187](https://arxiv.org/abs/2412.21187)). Em 2026, um estudo de test-time compute mostrou que, no AIME, a utilidade marginal de pensar fica negativa depois de um certo ponto: respostas corretas começam a virar erradas quando o modelo insiste ([arXiv 2604.10739](https://arxiv.org/abs/2604.10739)). Guarde esse detalhe: se pensar demais custa caro e pode piorar o resultado, quanto pensar vira uma decisão de engenharia. Ela reaparece no ponto 06 como parâmetro de API.

## 05. O prompt vira contrato

Se o modelo já sabe raciocinar, o que sobra para o prompt? Sobra a parte que nenhum treinamento resolve: dizer o que fazer, para quem, dentro de quais fronteiras e com quanta autonomia. O prompt de 2026 se parece menos com uma aula e mais com um contrato, e essa mudança tem infraestrutura formal por trás.

O primeiro passo foi dado em 2024 pelo paper da hierarquia de instruções da OpenAI ([Wallace et al., arXiv 2404.13208](https://arxiv.org/abs/2404.13208)): treinar o modelo para tratar instruções como camadas de privilégio (sistema acima de desenvolvedor, desenvolvedor acima de usuário, usuário acima de texto vindo de ferramentas) e para ignorar seletivamente instruções de menor privilégio que conflitem com as de cima. A documentação atual da OpenAI formaliza a analogia jurídica do lado do desenvolvedor: as mensagens de developer “fornecem as regras e a lógica de negócio do sistema, como a definição de uma função”, e as mensagens de usuário funcionam “como os argumentos dessa função”. Quem já escreveu software reconhece o que isso significa: assinatura, tipos, pré-condições. Um contrato de interface.

E contratos cobram caro por ambiguidade. O guia de prompting do GPT-5 avisa que o modelo segue instruções “com precisão cirúrgica” e que, justamente por isso, prompts com instruções contraditórias ou vagas “podem ser mais danosos ao GPT-5 do que a outros modelos”, porque o modelo gasta tokens de raciocínio procurando uma forma de conciliar as contradições em vez de escolher uma delas. O caso documentado no próprio guia é o da Cursor: uma seção de prompt chamada maximize*context\_understanding, com a ordem “Be THOROUGH when gathering information”, ajudava os modelos anteriores e passou a causar uso excessivo de ferramentas no GPT-5. A correção foi remover o prefixo maximize* e suavizar a linguagem; com o prompt mais calmo, o time viu o modelo tomar “decisões melhores sobre quando confiar no conhecimento interno versus buscar ferramentas externas”. No mesmo guia, um dado que parece burocrático e diz muito: só de migrar para a Responses API, passando o estado de raciocínio de uma chamada para a seguinte, o score no Tau-Bench Retail subiu de 73,9% para 78,2%. O contrato de 2026 inclui até a memória do processo entre chamadas.

<figure>
<img src="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/4c3ec9e494-1456.webp" srcset="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/4c3ec9e494-728.webp 728w, /blog/img/o-prompt-virou-contrato-mudancas-2022-2026/4c3ec9e494-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>Instrução deixou de ser sugestão e virou cláusula com precedência: plataforma, desenvolvedor, usuário, ferramenta, cada camada com a sua alçada.</em></figcaption>
</figure>

A Anthropic escreveu o mesmo capítulo com outras palavras. As boas práticas atuais mandam desmontar a gritaria acumulada nos prompts da era anterior: onde se escrevia “CRITICAL: You MUST use this tool when...”, a recomendação agora é o tom normal de “Use this tool when...”, porque a ênfase agressiva calibrada para modelos desobedientes faz os obedientes dispararem demais. A página do Claude Opus 5 pede a remoção das instruções de verificação (”include a final verification step”, “use a subagent to verify”): o modelo verifica o próprio trabalho sem ser mandado, e a instrução herdada causa verificação em excesso, gastando tokens sem ganho de qualidade. A do Claude Fable 5 generaliza: skills e prompts escritos para modelos anteriores “são frequentemente prescritivos demais” e podem degradar a qualidade do modelo novo. Três páginas de documentação, um mesmo movimento: apagar texto virou otimização.

Um contrato, porém, só funciona se as partes respeitam a precedência, e aqui a pesquisa de 2026 impõe honestidade: um benchmark de hierarquias com múltiplos níveis ([arXiv 2604.09443](https://arxiv.org/abs/2604.09443)) mostrou modelos de fronteira acima de 99% quando há só duas camadas de privilégio, e o melhor deles em 42,7% quando as camadas se multiplicam como nos stacks reais de agentes. O prompt-contrato é o paradigma vigente, com cláusulas que ainda vazam.

## 06. A técnica vira parâmetro: o que era frase virou botão

A segunda metade da virada aconteceu fora do texto. Um a um, os controles que o prompt engineering exercia por frases foram absorvidos pela API como parâmetros nomeados.

O mecanismo ficou visível num paper simples e elegante ([s1, arXiv 2501.19393](https://arxiv.org/abs/2501.19393)): forçar o fim do raciocínio ou prolongá-lo anexando “Wait” transforma o tamanho do pensamento num dial contínuo, e só de girar esse dial o desempenho no AIME24 subiu de 50% para 57%, sem mudar uma palavra do prompt. Os vendors produtizaram o dial. Na OpenAI, reasoning\_effort controla o esforço de raciocínio e verbosity controla, em separado, o tamanho da resposta final; o GPT-5.2 chegou com esforço padrão em none, raciocínio como opção e a recomendação de ajustar só depois de rodar evals. Na Anthropic, effort vai de low a max, e a documentação faz questão de avisar que ele controla o volume de pensamento, e que para encurtar a resposta visível o caminho continua sendo pedir no prompt; o adaptive thinking, em que o modelo decide sozinho quando e quanto pensar, é o único modo de operação do Claude Fable 5. “Pense passo a passo”, a frase de 61 pontos, virou um argumento tipado na chamada de função.

<figure>
<img src="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/6c86bc9292-1456.webp" srcset="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/6c86bc9292-728.webp 728w, /blog/img/o-prompt-virou-contrato-mudancas-2022-2026/6c86bc9292-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>O que era frase virou botão: esforço de raciocínio, verbosidade e profundidade de pensamento saíram do texto e entraram na assinatura da API.</em></figcaption>
</figure>

Para o dia a dia das empresas, a consequência prática apareceu nos casos que a própria OpenAI documenta: a SafetyKit saltou de 50% para 88% de acurácia nas suas tarefas mais difíceis de classificação de imagem “sem qualquer modificação no pipeline”, apenas trocando GPT-4o por o1; a Blue J mediu melhora de 4x no desempenho de ponta a ponta na pesquisa tributária; um cliente da Braintrust viu o F1 de um juiz automático ir de 0,12 para 0,74. Todos esses ganhos vieram de trocar o motor e deixar o contrato em paz, com o prompt intacto. A pergunta de otimização mudou de “que palavras uso?” para “que modelo, com quanto esforço, a que custo?”: uma decisão de arquitetura e orçamento, tomada em parâmetros.

## 07. De prompt engineering a context engineering

Com o raciocínio internalizado e o controle fino parametrizado, o que sobrou de engenharia migrou para uma camada acima: decidir o que entra na janela de contexto do modelo. A Anthropic deu nome ao movimento num ensaio de engenharia de setembro de 2025: construir com modelos “está se tornando menos sobre encontrar as palavras e frases certas para os prompts e mais sobre responder à pergunta mais ampla de qual configuração de contexto tem mais chance de gerar o comportamento desejado”. O texto introduz a imagem que organiza a disciplina: o modelo tem um “orçamento de atenção” finito, e o trabalho do engenheiro é encontrar “o menor conjunto possível de tokens de alto sinal que maximize a probabilidade do resultado desejado”. Do lado acadêmico, um survey de julho de 2025 sistematizou mais de 1.400 papers sob o guarda-chuva de context engineering ([Mei et al., arXiv 2507.13334](https://arxiv.org/abs/2507.13334)), formalizando a disciplina que transcende o design do prompt: retrieval, memória, ferramentas, gestão da janela.

Dois resultados mostram por que essa camada virou o gargalo. Primeiro, a robustez à sujeira: em modelos clássicos, inserir contexto irrelevante derrubou o desempenho em 55,89% na média de 13 modelos, enquanto o o1-preview caiu 2,68% ([arXiv 2504.02111](https://arxiv.org/abs/2504.02111)); o modelo novo aguenta quase tudo, menos um contexto mal curado em escala. Segundo, a origem da sensibilidade: um estudo de 2026 ([arXiv 2602.04297](https://arxiv.org/abs/2602.04297)) mostrou que boa parte da fragilidade que restou nos modelos modernos é atribuível a prompt subespecificado; com a especificação completa do comportamento, a variância entre formulações desaba. Dito de outro jeito: a brittleness de 2023 era do modelo; a de 2026 é do contrato mal escrito.

<figure>
<img src="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/55fffe4b54-1456.webp" srcset="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/55fffe4b54-728.webp 728w, /blog/img/o-prompt-virou-contrato-mudancas-2022-2026/55fffe4b54-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>A engenharia mudou de escrever frases para dosar o que entra: ferramentas, memória, skills e dados disputam um orçamento de atenção finito.</em></figcaption>
</figure>

A infraestrutura acompanhou. As Agent Skills da Anthropic empacotam instruções em pastas versionáveis que o agente descobre e carrega sob demanda, por divulgação progressiva: o prompt virou artefato de software, componível e compartilhável, o que conversa direto com o que escrevi em [O fim do código como ativo](/blog/o-fim-do-codigo-como-ativo-o-novo/) e com o [sistema de registros que mantém meus agentes](https://iaaplicada.substack.com/p/sistema-de-registros-para-agentes). A pesquisa acompanha: o método ACE ([arXiv 2510.04618](https://arxiv.org/abs/2510.04618)) trata o contexto como um playbook que evolui com o uso, com ganho de 10,6% em benchmarks de agentes. E até a escrita do que sobra está sendo automatizada: o DSPy tratava prompt como alvo de compilador já em 2023 ([arXiv 2310.03714](https://arxiv.org/abs/2310.03714)), o TextGrad levou a otimização por feedback textual à Nature em 2025, e o GEPA ([arXiv 2507.19457](https://arxiv.org/abs/2507.19457)) mostrou evolução reflexiva de prompts superando reinforcement learning com até 35 vezes menos rollouts. Quando a máquina escreve o prompt melhor que o humano, o valor humano se desloca para especificar o que “melhor” significa: a métrica, o contexto, o contrato.

## 08. As pessoas mudaram menos que os modelos

Tudo acima descreve a fronteira da engenharia. Os logs de uso real contam uma história paralela e mais honesta sobre o resto de nós.

Os grandes corpora de conversas reais (WildChat, com 1 milhão de diálogos, e LMSYS-Chat-1M) viraram matéria-prima para estudos longitudinais, e o resultado mais consistente é a inércia individual. Um estudo de 2026 com 12 mil usuários acompanhados por meses ([arXiv 2605.29018](https://arxiv.org/abs/2605.29018)) resume: “os hábitos dos usuários são pegajosos e resistentes à mudança”; a complexidade média das mensagens na população cresceu na casa de 1,5x, mas o efeito é de composição, puxado por usuários novos, e quem usa intensamente já era diferente desde a primeira mensagem. Outro trabalho sobre 211 mil consultas do WildChat ([arXiv 2508.01213](https://arxiv.org/abs/2508.01213)) detectou a única migração clara: os pedidos foram ficando menos “comando seco” e mais carregados de contexto, com cada usuário convergindo para um template pessoal estável depois de umas 20 interações, e com muito menos marcadores de polidez do que conversas entre humanos. Das 58 técnicas que [The Prompt Report](https://arxiv.org/abs/2406.06608) catalogou em 2024, o usuário comum segue conhecendo pouquíssimas; o aprendizado real foi dar contexto e repetir o que funciona.

<figure>
<img src="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/b20b36567c-1456.webp" srcset="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/b20b36567c-728.webp 728w, /blog/img/o-prompt-virou-contrato-mudancas-2022-2026/b20b36567c-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>Cada usuário cava o próprio sulco nas primeiras semanas e permanece nele; quem move a média da população são os que chegam depois.</em></figcaption>
</figure>

A escala desse “resto de nós” é o que dá peso ao achado. O estudo do NBER sobre uso do ChatGPT (setembro de 2025) estimou cerca de 10% da população adulta mundial usando o produto, com o uso pessoal crescendo de 53% para mais de 70% das conversas e três categorias (orientação prática, busca de informação e escrita) somando quase 80% do volume. O Anthropic Economic Index, sobre milhões de conversas do Claude ([arXiv 2503.04761](https://arxiv.org/abs/2503.04761)), mapeou 36% das ocupações usando IA em pelo menos um quarto das suas tarefas, com 57% do uso em padrão de colaboração e 43% em delegação. E o folclore, aquele do ponto 02, envelheceu mal: um estudo de outubro de 2025 ([arXiv 2510.04950](https://arxiv.org/abs/2510.04950)) achou o efeito invertido no GPT-4o, com prompts rudes superando os educados (84,8% contra 80,8%), enquanto o grupo de Wharton ([arXiv 2503.04818](https://arxiv.org/abs/2503.04818)) mediu efeitos de polidez e formatação reais, porém imprevisíveis por pergunta. A lição operacional para quem usa IA todo dia: pare de colecionar truque de frase; eles mudam de sinal a cada geração de modelo. Especifique o que quer e dê o contexto.

## 09. O mercado precificou: de cargo a tarefa

O mercado de trabalho registrou a transição com a frieza de sempre. Nos dados da Indeed, as buscas por prompt engineering saíram de 2 por milhão em janeiro de 2023, atingiram o pico de 144 por milhão em abril de 2023 e estabilizaram na faixa de 20 a 30 por milhão. Em abril de 2025, o Wall Street Journal decretou no título: o emprego mais quente de 2023 já estava obsoleto. A pesquisa Work Trend Index da Microsoft, com 31 mil trabalhadores em 31 países, colocou prompt engineer na penúltima posição entre os novos papéis que as empresas consideravam contratar. A síntese mais citada veio de um executivo de recrutamento: o prompt engineering “virou de emprego em tarefa muito, muito rápido”. Dados de 2026 de uma base de vagas do setor (com a ressalva de ser levantamento próprio, sem revisão por pares) apontam queda de cerca de 30% nos títulos exclusivos entre 2024 e 2026, com o triplo de vagas de outros títulos exigindo a habilidade.

<figure>
<img src="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/0fcf757aa5-1456.webp" srcset="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/0fcf757aa5-728.webp 728w, /blog/img/o-prompt-virou-contrato-mudancas-2022-2026/0fcf757aa5-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>A maré de 2023 deixou a marca no pilar: o pico de abril, o refluxo e o nível estável em que a habilidade continuou no porto, distribuída pelos ofícios.</em></figcaption>
</figure>

Note que a leitura correta é a menos dramática: a habilidade sobreviveu ao cargo. Saber especificar comportamento de modelo virou competência transversal, embutida em engenharia, produto, marketing, jurídico. O movimento é o mesmo que descrevi para o código em [O fim do código como ativo](/blog/o-fim-do-codigo-como-ativo-o-novo/): quando uma prática se comoditiza, o valor migra para a camada de cima. No caso do prompt, para o contexto proprietário e para a qualidade da especificação.

## 10. O que continua valendo, e o que levar para a sua empresa

Depois de tanta demolição, o inventário do que ficou de pé é curto e sólido. Clareza e direção continuam sendo a regra de ouro dos dois vendors: a OpenAI recomenda prompts simples e diretos (”Keep prompts simple and direct”), zero exemplos como ponto de partida (”Try zero shot first, then few shot if needed”) e delimitadores para separar as partes da entrada; a Anthropic mantém o teste do colega (se um colega sem contexto se confunde com o seu prompt, o Claude também se confunde) e acrescenta uma instrução nova e reveladora: explicar o motivo por trás da instrução, porque o modelo “é inteligente o suficiente para generalizar a partir da explicação”. Para contexto longo, o conselho continua mecânico e mensurável: documentos no topo, pergunta no fim, com ganho de até 30% de qualidade nos testes da Anthropic. E a recomendação mais recente da casa inverte de vez a gramática de 2022: “prefira instruções gerais a passos prescritivos”; um simples “pense com cuidado” costuma render raciocínio melhor do que o plano passo a passo escrito à mão. A aula ficou por conta do modelo. A direção, por nossa conta.

<figure>
<img src="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/983a929b79-1456.webp" srcset="/blog/img/o-prompt-virou-contrato-mudancas-2022-2026/983a929b79-728.webp 728w, /blog/img/o-prompt-virou-contrato-mudancas-2022-2026/983a929b79-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="" loading="lazy" decoding="async">
<figcaption><em>O que sobrou do ofício é engenharia de carga: clareza, estrutura, contexto e medição sustentando o vão; a decoração foi embora com a maré.</em></figcaption>
</figure>

Fecho com quatro lições para empresas, em ordem de incômodo.

**Primeira:** prompts de 2023 rodando em modelos de 2026 pioram o resultado, e a sua empresa provavelmente tem vários. O caso Cursor, a instrução de verificação do Opus 5 e o aviso do Fable 5 sobre skills prescritivas demais contam a mesma história: o acúmulo de instruções defensivas calibradas para modelos antigos virou passivo. A OpenAI recomenda, na migração para o GPT-5.5, começar de uma baseline limpa em vez de carregar cada instrução do stack anterior. Auditar e apagar virou trabalho de otimização com ROI imediato.

**Segunda:** o controle fino saiu do texto e foi para a API, e isso transfere a decisão de quem escreve para quem arquiteta. Escolher modelo, reasoning\_effort, verbosity e orçamento de pensamento por tarefa é o novo prompt engineering, com efeito direto na fatura. Quem trata esses parâmetros como default está pagando por raciocínio que não precisa ou economizando onde não devia.

**Terceira:** trate prompt como código de produção, porque os vendors já tratam. Versionamento, revisão, testes de regressão, rollback e evals; a OpenAI está encerrando o produto de prompts reutilizáveis da API (v1/prompts, com desligamento marcado para 30/11/2026) e orientando mover o conteúdo dos prompts para o código da aplicação. Prompt sem eval é opinião com acesso a produção.

**Quarta:** a vantagem durável mora no contexto, o insumo que só a sua operação gera. Modelos todo concorrente aluga pela mesma API; o orçamento de atenção cada empresa preenche com o que tem: dados proprietários organizados, ferramentas bem descritas, memória institucional, skills que codificam como o seu negócio decide. É o mesmo argumento do [ativo invisível](/blog/o-ativo-invisivel-dados-como-vantagem-competitiva/), uma camada acima: quem não construiu a base de contexto vai descobrir que ela também não se compra pronta.

A pergunta que deixo para o seu planejamento: quanto do manual de prompts que a sua empresa escreveu em 2023 continua colado nos seus sistemas em 2026, e quanto ele está custando, em tokens e em resultados, sem que ninguém tenha medido?

***

**Referências principais.**

Documentação oficial:

- OpenAI (Reasoning best practices; GPT-5 Prompting Guide; Text generation; Deprecations; Model guidance GPT-5.5)
- Anthropic (Prompting best practices; Prompting Claude Opus 5; Prompting Claude Fable 5; Effort; Adaptive thinking; Effective context engineering for AI agents; Agent Skills).

Papers citados:

- Brown et al. 2020 (arXiv 2005.14165);
- Wei et al. 2022 (2201.11903);
- Kojima et al. 2022 (2205.11916);
- Wang et al. 2022 (2203.11171);
- Zamfirescu-Pereira et al., CHI 2023 (DOI 10.1145/3544548.3581388);
- Sclar et al. 2023 (2310.11324);
- Li et al. 2023 (2307.11760);
- Yin et al. 2024 (2402.14531);
- Bsharat et al. 2023 (2312.16171);
- Liu et al. 2023 (2307.03172);
- Zhou et al. 2022 (2211.01910);
- Yang et al. 2023 (2309.03409);
- Battle e Gollapudi 2024 (2402.10949);
- Schulhoff et al. 2024 (2406.06608);
- Zheng et al. 2024 (2311.10054);
- Cao et al. 2024 (2406.10248);
- He et al. 2024 (2411.10541);
- Nori et al. 2024 (2411.03590);
- Sprague et al. 2024 (2409.12183);
- Chen et al. 2024 (2412.21187);
- Wallace et al. 2024 (2404.13208);
- Muennighoff et al. 2025 (2501.19393);
- Meincke et al. 2025 (2503.04818 e 2506.07142);
- Mei et al. 2025 (2507.13334);
- Khattab et al. 2023 (2310.03714);
- Yuksekgonul et al. 2024 (2406.07496; Nature 639, 2025);
- Agrawal et al. 2025 (2507.19457);
- Zhang et al. 2025 (2510.04618);
- Chatziveroglou et al. 2025 (2504.02111);
- Pecher et al. 2026 (2602.04297);
- Hicke e Tomlinson 2026 (2605.29018);
- Zhu et al. 2025 (2508.01213);
- Handa et al. 2025 (2503.04761);
- Chatterji et al. 2025 (NBER WP 34255);
- Dobariya e Kumar 2025 (2510.04950);
- Zhang et al. 2026 (2604.09443);
- Zhou et al. 2026 (2604.10739).

Infos gerais de mercado:

- IEEE Spectrum (mar/2024),
- Wall Street Journal (abr/2025),
- Fortune (mai/2025),
- Microsoft Work Trend Index.
