---
title: "O ativo invisível: o que 116 mil relatórios anuais ensinam sobre o valor dos dados"
subtitle: "Empresas intensivas em dados valem até 35% mais que concorrentes do mesmo nicho, mas o lucro demora anos para aparecer, e quem não construiu a base cedo não compra o atraso depois."
description: "Estudo com 116 mil relatórios mostra: empresas intensivas em dados valem até 35% mais que concorrentes diretas. O retorno demora anos e a sequência importa."
date: 2026-06-12T19:56:40.358Z
updated: 2026-06-15T11:46:46.709Z
slug: o-ativo-invisivel-dados-como-vantagem-competitiva
cover: img/o-ativo-invisivel-dados-como-vantagem-competitiva/capa.jpg
type: newsletter
---

Há pouco mais de um ano, escrevi aqui que o verdadeiro ativo estratégico das organizações seria a composição única de dados proprietários combinados com modelos de IA treinados especificamente sobre eles ([O Futuro das Organizações](/blog/o-futuro-das-organizacoes-alem-da/)). Era uma tese. Esta semana revisitei um estudo que dá a essa tese o que ela mais precisava: números.

O estudo é “Data Assets and Industry Competition: Evidence from 10-K Filings”, de Adam Saunders (University of British Columbia) e Prasanna Tambe (NYU Stern), disponível no [SSRN](https://ssrn.com/abstract=2537089). Os dois fizeram algo que parece óbvio depois que alguém faz: usaram mineração de texto para ler todos os relatórios anuais que as empresas de capital aberto americanas são obrigadas a entregar à SEC (os formulários 10-K) entre 1996 e 2012, e mediram quanto cada empresa fala sobre capturar, armazenar, gerenciar e analisar dados.

O período analisado pode parecer datado, e é justamente essa distância que torna o estudo valioso. Eles mediram a formação da vantagem competitiva por dados antes do hype de big data, antes de deep learning, antes de LLMs. O mecanismo que documentaram é estrutural e independe de moda. E, como vou argumentar no final, é exatamente o mecanismo que está se repetindo agora com IA.

Organizei o argumento do paper em dez pontos, na ordem em que eles se constroem. Vamos a eles.

## 01. O problema: dados são um ativo invisível

Bases de dados, capacidade analítica e práticas data-driven são ativos intangíveis. Geram valor, custam caro para construir, levam anos para maturar, e não aparecem em nenhuma linha do balanço patrimonial. O contador registra o servidor, mas os quinze anos de histórico de transações que rodam nele ficam de fora.

<figure>
<img src="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/df483fc825-1456.webp" srcset="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/df483fc825-728.webp 728w, /blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/df483fc825-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="Dados como vantagem competitiva" loading="lazy" decoding="async">
<figcaption>Dados como vantagem competitiva</figcaption>
</figure>

Isso criava um problema de medição que travava o campo inteiro. As fontes tradicionais de dados sobre investimento em TI cobriam poucas centenas de empresas, períodos curtos, ou simplesmente deixaram de existir. Sem medida, sem evidência; sem evidência, a conversa sobre “dados como ativo” ficava no terreno da opinião: o pitch de consultoria de um lado, o ceticismo do CFO do outro.

A consequência prática: ninguém conseguia responder com rigor a perguntas básicas. Empresas que investem em dados valem mais? Lucram mais? Quanto tempo demora? A vantagem é replicável ou cumulativa?

## 02. Como mediram: do dicionário ao painel final

A solução dos autores tem duas pernas. A primeira é a fonte: o 10-K. Diferente de press release e de deck de investidor, o 10-K é regulado pelo Securities Act e pela Sarbanes-Oxley: cada afirmação é juridicamente vinculante, auditada, e mentir nele é crime com pena para os executivos que assinam. Quando uma empresa escreve no 10-K que implementou um data warehouse, o departamento jurídico dela deixou.

A segunda perna é o dicionário. Para evitar viés de quem escolhe as palavras, eles partiram de uma fonte externa e pública, o relatório Big Data do McKinsey Global Institute (2011), e extraíram toda combinação de palavra com “data”. Saíram 1.035 combinações, que foram validadas manualmente em amostras reais de 10-Ks: sobreviveram 410 keywords, agregadas em 155 termos-raiz (customer data, data mining, data warehouse, clinical data, data privacy...).

<figure>
<img src="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/437cd8b1fc-1456.webp" srcset="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/437cd8b1fc-728.webp 728w, /blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/437cd8b1fc-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="Como mediram: do dicionário ao painel final" loading="lazy" decoding="async">
<figcaption>Como mediram: do dicionário ao painel final</figcaption>
</figure>

Aplicado o dicionário aos relatórios e cruzando com dados financeiros da Compustat, o funil ficou assim: 194.935 firma-anos no ponto de partida; 116.173 com 10-K válido (o painel usado para análise de difusão); e 33.120 observações na amostra final de valor e lucro, depois de exigir financeiros completos, excluir financeiras (cujo balanço não se compara) e excluir produtoras de TI, que citam dados para vender, não para usar. A medida final, PCTDATA, ranqueia as empresas de cada ano em decis de intensidade de keywords.

É a maior base já construída sobre o tema: cerca de 1.950 empresas por ano, dezessete anos. Ler fontes públicas em escala com software, aliás, ficou radicalmente mais acessível desde então: o [radar de inteligência de mercado](/blog/radar-inteligencia-mercado-open-source/) que publiquei em código aberto faz o equivalente com LLMs, sem depender de dicionário de keywords.

## 03. A difusão: começa na TI e se espalha pela economia inteira

Com o painel montado, a primeira pergunta é descritiva: quem fala de dados, e desde quando?

Em 1996, 78% das produtoras de TI já mencionavam ao menos uma keyword de dados em seus 10-Ks, contra 41% do resto da economia. Em 2012, as produtoras chegaram a 90%, mas a história real está nas usuárias: 75%, quase o dobro do ponto de partida. O crescimento atravessou todos os setores, inclusive os menos óbvios: agricultura foi de 20% para 62%, utilities de 26% para 78%.

<figure>
<img src="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/b575e6f044-1456.webp" srcset="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/b575e6f044-728.webp 728w, /blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/b575e6f044-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="A difusão: começa na TI e se espalha pela economia inteira" loading="lazy" decoding="async">
<figcaption>A difusão: começa na TI e se espalha pela economia inteira</figcaption>
</figure>

O detalhe mais interessante está na dispersão dentro de cada setor. Entre as produtoras de TI, a variação de intensidade entre empresas vem caindo desde 1996: a prática amadureceu, padronizou, virou commodity. Entre as usuárias, a dispersão vem subindo. Líderes e retardatários estão se afastando. É a assinatura estatística de uma prática que deixou de ser curiosidade técnica e virou arma competitiva.

## 04. O vocabulário muda e conta a história da década

Olhar quais termos crescem é quase uma arqueologia da gestão de dados. Nos anos 90, o termo dominante era “data processing”, dados como tarefa operacional. Ele encolhe ao longo da amostra (11,5% para 8,0% dos relatórios), enquanto “database” quase dobra (13,9% para 24,8%) e “data center” sobe de 1,3% para 9,7%.

A explosão, porém, é de outra família. O termo que mais cresceu em todo o período foi “data privacy”: de exatos 2 relatórios (entre cerca de 3.000) em 1996 para 6,3% da amostra em 2012. Em 2012, data security, data privacy, sensitive data, data protection e personal data estavam todos entre os 20 termos mais citados: nenhum deles aparecia de forma relevante em 1996.

<figure>
<img src="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/a99d6779d8-1456.webp" srcset="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/a99d6779d8-728.webp 728w, /blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/a99d6779d8-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="O vocabulário muda e conta a história da década" loading="lazy" decoding="async">
<figcaption>O vocabulário muda e conta a história da década</figcaption>
</figure>

O centro de gravidade do discurso corporativo migrou de processar (anos 90) para coletar (anos 2000) e depois proteger (pós-2007). Guarde essa sequência: ela reaparece no ponto 07, precificada pelo mercado. E lembre que isso é anos antes de GDPR e LGPD existirem: as empresas líderes sinalizavam preocupação com privacidade uma década antes do regulador chegar. E a disputa por esse insumo só se intensificou: em [78% dos bots de IA querem uma coisa: seus dados](/blog/osbots-de-ia-querem-uma-coisa-dados/) mostrei quem está coletando o quê na internet de hoje.

## 05. O mercado paga prêmio, e o prêmio não tem teto

Aqui o estudo sai do descritivo e entra no que interessa. A métrica é o Tobin’s q: valor de mercado dividido pelo custo de reposição dos ativos tangíveis. Se q é maior que 1, o mercado enxerga na empresa valor além de máquinas, prédios e estoques, ou seja, precifica intangíveis.

Resultado, controlando por P\&D, publicidade, alavancagem, tamanho, lucratividade, setor e ano (2001–2012, excluindo a bolha ponto-com): a relação entre intensidade de dados e valor é uma escada. Nos decis de baixo, nada. Do sétimo decil em diante, o prêmio aparece e cresce: +11,7%, +12,8%, +23,9%, até +35,3% de Tobin’s q para as empresas no decil mais intensivo, comparadas à base.

<figure>
<img src="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/60a1ee72ff-1456.webp" srcset="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/60a1ee72ff-728.webp 728w, /blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/60a1ee72ff-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="O mercado paga prêmio, e o prêmio não tem teto" loading="lazy" decoding="async">
<figcaption>O mercado paga prêmio, e o prêmio não tem teto</figcaption>
</figure>

Dois detalhes elevam a barra. Primeiro, a relação é monotônica e não desacelera no topo: não existe “intensidade demais”. Segundo, o resultado sobrevive ao controle mais fino possível: comparando cada empresa apenas com concorrentes da mesma indústria de seis dígitos NAICS, no mesmo ano, o prêmio do topo segue em +28,4%. O prêmio é diferença entre vizinhos diretos de mercado, com qualquer efeito de “setor de tecnologia vale mais” já descontado pelos controles.

## 06. O lucro vem, mas demora anos (e discurso genérico custa caro)

Se o mercado paga na frente, o lucro confirma depois. A intensidade de dados de hoje não tem correlação significativa com o lucro de hoje. Tem com o lucro de daqui a dois, três, cinco anos, e o efeito cresce a cada ano de defasagem, chegando a +3,2 pontos percentuais de ROA acima da média da indústria no horizonte de cinco anos (top 10% vs. bottom 10%).

<figure>
<img src="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/169891c264-1456.webp" srcset="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/169891c264-728.webp 728w, /blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/169891c264-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="O lucro vem, mas demora anos (e discurso genérico custa caro)" loading="lazy" decoding="async">
<figcaption>O lucro vem, mas demora anos (e discurso genérico custa caro)</figcaption>
</figure>

A leitura é a mesma que Brynjolfsson e colegas fizeram para a TI em geral: o ativo só vira resultado quando os complementos organizacionais (processos, habilidades, cultura de decisão, o lado humano que explorei em [Construindo Times de IA](/blog/construindo-times-de-ia-lideranca/)) ficam prontos. Isso leva anos. Avaliar uma iniciativa de dados pelo resultado do primeiro ano é medir com a régua errada.

O contraste que dá força ao resultado: os autores rodaram o mesmo modelo com menções genéricas a “information technology” e “information systems”. O coeficiente é negativo: empresas que falam de TI em geral, sem prática específica de dados, lucram menos que as concorrentes nos anos seguintes. O mercado e a contabilidade convergem no mesmo veredito: o mercado recompensa a prática específica e cobra caro do discurso genérico.

## 07. As três eras do valor: coleta, segurança, padronização

Com análise fatorial (sem categorias pré-definidas), as 155 keywords se agrupam sozinhas em exatamente três temas: coleta (clinical data, trial data, collecting data...), segurança (data security, data protection, personal data...) e padronização (standardizing data, extract data, map data...).

O mercado premiou cada um em seu tempo. Entre 2001 e 2006, o prêmio estava na coleta: coeficiente de +0,218 sobre Tobin’s q, forte e significativo. De 2007 a 2012, o prêmio da coleta zera e migra para a segurança (+0,200). A padronização, sozinha, nunca foi premiada em nenhum período.

<figure>
<img src="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/5cf9a1794e-1456.webp" srcset="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/5cf9a1794e-728.webp 728w, /blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/5cf9a1794e-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="As três eras do valor: coleta, segurança, padronização" loading="lazy" decoding="async">
<figcaption>As três eras do valor: coleta, segurança, padronização</figcaption>
</figure>

A interpretação dos autores é a que a microeconomia sugere: enquanto coletar dados diferenciava, o mercado pagava por coleta. Quando todo mundo passou a coletar, virou aposta de mesa: o prêmio foi para a fronteira seguinte. É um padrão de migração de valor que qualquer executivo de tecnologia reconhece, agora com coeficiente e erro-padrão.

## 08. Não dá para pular etapas: a evidência de path dependence

E a padronização, que nunca aparece premiada sozinha? Aqui está, para mim, o resultado mais importante do paper.

Os autores separaram as empresas em dois grupos: as que em algum momento anterior registraram alta atividade de coleta (top 25%) e as que nunca registraram. No período 2007–2012, investir em segurança vale +0,321 de Tobin’s q para quem tem a base de coleta, quase o triplo do efeito (não significativo) para quem não tem. E a padronização só gera prêmio estatisticamente detectável para quem coletou antes (+0,142).

<figure>
<img src="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/48330843a2-1456.webp" srcset="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/48330843a2-728.webp 728w, /blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/48330843a2-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="Não dá para pular etapas: a evidência de path dependence" loading="lazy" decoding="async">
<figcaption>Não dá para pular etapas: a evidência de path dependence</figcaption>
</figure>

Em outras palavras: segurança e padronização funcionam como multiplicadores de uma base que precisa existir antes. Os dados rejeitam explicitamente a história reconfortante de que retardatários conseguem “alcançar” comprando as camadas de cima depois: investimento tardio sem fundação rende pouco, e a diferença entre os dois grupos é maior em 2007–2012 do que em 2001–2006. A vantagem não só persiste; ela compõe.

A boa notícia para quem está atrás: construir a fundação não exige reescrever tudo. A [arquitetura em camadas que documentei aqui](/blog/arquitetura-consolidacao-dados-ia-sem-reescrever-sistemas-legados/) consolida dezenas de fontes heterogêneas preservando os sistemas legados, e é o caminho pragmático para o degrau de baixo.

## 09. Antes de acreditar: os testes contra as explicações rivais

Todo resultado correlacional convive com duas objeções, e o paper enfrenta as duas de frente.

Objeção um: causalidade invertida, a ideia de que empresas boas é que falam de dados. O teste: acompanhar o valor de mercado em volta da primeira aparição de uma keyword de dados no 10-K de cada empresa. Um ano antes: nada (+4,0%, não significativo). Às vésperas: nada. De nove a dez meses depois: +18,8%, altamente significativo. Se a keyword fosse só um marcador de empresa boa, o prêmio existiria antes da primeira menção. Não existe.

<figure>
<img src="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/2bcfd5c6cd-1456.webp" srcset="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/2bcfd5c6cd-728.webp 728w, /blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/2bcfd5c6cd-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="Antes de acreditar: os testes contra as explicações rivais" loading="lazy" decoding="async">
<figcaption>Antes de acreditar: os testes contra as explicações rivais</figcaption>
</figure>

Objeção dois: cheap talk, “é discurso para inflar a ação”. O teste: medir algo que narrativa nenhuma melhora, o giro de estoque. Resultado: melhora de cerca de 10% no ano seguinte à primeira keyword, e nada antes. A eficiência operacional real acompanha o discurso, exatamente o que se espera se o discurso reflete prática.

E a robustez: trocando o dicionário McKinsey por um léxico do NIST, ou usando só a palavra “data”, ou reincluindo financeiras e produtoras de TI, o padrão se mantém. Os autores são honestos sobre os limites: a medida captura a materialidade declarada dos dados, sem dizer quanto a empresa investe, e controles finos não substituem um experimento randomizado. Mas o conjunto de testes elimina as explicações alternativas óbvias.

## 10. O que levar para a sua empresa

Quatro conclusões, em ordem de incômodo.

Primeira: dados são um ativo de verdade, com prêmio de mercado mensurável, mesmo invisível no balanço. Se a sua empresa só gerencia o que aparece no demonstrativo, ela está gerenciando o conjunto errado.

Segunda: o retorno é estruturalmente defasado. O mercado precifica na frente; o lucro confirma dois a cinco anos depois. Quem cobra payback de iniciativa de dados no orçamento do ano seguinte está medindo com a régua errada e matando o ativo antes de ele maturar.

Terceira: a sequência importa mais que o montante. Coleta primeiro; segurança e padronização multiplicam sobre essa base, e rendem pouco sem ela.

Quarta: a vantagem é cumulativa. Bases de dados levam anos para construir, e os retardatários do estudo não conseguiram comprar o atraso. Em 2012, isso significava dispersão crescente de desempenho dentro das indústrias. O nome disso em estratégia é fosso.

<figure>
<img src="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/9feb450ce9-1456.webp" srcset="/blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/9feb450ce9-728.webp 728w, /blog/img/o-ativo-invisivel-dados-como-vantagem-competitiva/9feb450ce9-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="819" alt="O que levar para a sua empresa" loading="lazy" decoding="async">
<figcaption>O que levar para a sua empresa</figcaption>
</figure>

## O paralelo com 2026

Releia os dez pontos trocando “dados” por “IA” e o paper fica desconfortavelmente atual.

A escada que o estudo documentou (coletar, depois proteger, depois padronizar, cada camada multiplicando a anterior) ganhou um degrau novo: treinar e ativar modelos sobre dados proprietários. E o padrão de migração de valor do ponto 07 está se repetindo na nossa frente. Em 2024–2025, “ter IA” diferenciava; está virando aposta de mesa na mesma velocidade em que coletar dados virou nos anos 2000, o mesmo movimento de comoditização que descrevi em [O fim do código como ativo](/blog/o-fim-do-codigo-como-ativo-o-novo/), agora uma camada acima. Quando todo concorrente tiver acesso aos mesmos modelos de fronteira (e já tem), o prêmio migra para o único insumo que não se compra por API: o ativo de dados proprietário, coletado, protegido e padronizado ao longo de anos. E o degrau seguinte já está visível: aplicações desenhadas para [aprender com o próprio uso](/blog/self-improvement-architecture-aplicacoes-que-aprendem-com-uso-real/), que transformam a operação diária em coletora contínua desse ativo.

O que o paper acrescenta à conversa de 2026 é a estrutura temporal da tese: o prêmio chega antes do lucro, o lucro demora cinco anos, e quem pula a fundação não recupera depois. Empresas que hoje tentam “comprar IA” sem nunca terem construído a base de dados são as empresas de padronização-sem-coleta do estudo: investimento de cima sem o degrau de baixo.

A pergunta que fica para o seu planejamento é a mesma de 1996: que dados só a sua operação gera, e o que você está fazendo, hoje, para que eles existam, organizados, protegidos e utilizáveis, em 2031?

***

**Referência:** Saunders, A. \& Tambe, P. (2015). *Data Assets and Industry Competition: Evidence from 10-K Filings.* Draft de setembro/2015, SSRN 2537089. Coeficientes citados: tabelas 3–16 do draft. Significância: os valores citados como significativos têm p\<0,05 ou p\<0,01.
