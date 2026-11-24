---
title: "O fim do código como ativo: o novo perfil do desenvolvedor que todo Executivo precisa entender"
subtitle: "1 Ano de IA dentro de um time de engenharia expuseram uma verdade desconfortável sobre onde mora hoje o gargalo do software, como o trabalho mudou junto e por que quem só reagir depois vai pagar"
description: "1 ano de IA dentro de um time de engenharia expuseram uma verdade desconfortável sobre onde mora hoje o gargalo do software, como o trabalho mudou junto e por que quem só reagir depois vai pagar caro."
date: 2026-04-23T15:00:51.515Z
updated: 2026-04-23T15:03:22.081Z
slug: o-fim-do-codigo-como-ativo-o-novo
cover: img/o-fim-do-codigo-como-ativo-o-novo/capa.jpg
type: newsletter
---

Se a sua empresa constrói software, mesmo que em pequena escala, o seu time de desenvolvimento está mudando mais rápido do que você percebe. As pessoas continuam as mesmas. O que mudou foi o trabalho em si.

Quero explicar isso de um jeito que serve tanto para o CEO que tem duzentos engenheiros em folha quanto para o fundador que contratou dois devs e uma assinatura de Claude Code. A confusão no ar é parecida nos dois casos: produtividade prometida que não chega, sênior reclamando de sobrecarga, júnior entregando rápido demais para o conforto de quem revisa, e uma sensação geral de que o time virou uma caixa preta que engole IA de um lado e cospe código do outro, sem que ninguém, inclusive o CTO, consiga dizer com precisão o que está acontecendo lá dentro.

Vou ser direto: **essa confusão tem nome, tem causa e tem um caminho de saída**. Entender tudo isso virou diligência básica de qualquer liderança que tenha software dentro do negócio, e não mais um luxo reservado a executivo de tecnologia.

## O gargalo migrou para a especificação

Durante vinte anos, a engenharia de software funcionou assim: você contrata pessoas para escrever código, mede a entrega por linhas *commitadas*, features enviadas, story points fechados. O code review é o portão de qualidade. Se passou na revisão, vai para produção. O ofício era o código em si.

Com agentes de IA, essa lógica inteira quebra. O código começa a chegar mais rápido do que o time consegue processar. Em um cenário real de um time de vinte engenheiros rodando Claude Code e Cursor por seis meses, um sênior chegou frustrado com o pull request de um júnior. Milhares de linhas. Funcionava. Passava nos testes. Ele disse, sem meio-termo: “*eu não li o código. Não tinha como. Era código demais. Qual é o meu trabalho agora?*”

Essa é a pergunta do momento:

<div class="pullquote">
<p>Se o código já não é o gargalo, onde foi parar o gargalo?</p>
</div>

A resposta é incômoda para quem construiu carreira escrevendo código. O gargalo migrou para a especificação. A engenharia rigorosa que antes a gente aplicava depois que o código era escrito, como revisão, teste e cobertura, agora precisa acontecer antes que qualquer linha exista.

Um caso que se tornou emblemático: um dev pede para um agente construir um sistema de notificações. Requisito simples. O agente constrói. Funciona lindo em ambiente de teste. Vai para produção e dispara cinquenta mil e-mails em três minutos. Por quê? Porque ninguém tinha especificado um limite de taxa. O agente não sabia que isso era óbvio. Para ele, não era.

<figure>
<img src="/blog/img/o-fim-do-codigo-como-ativo-o-novo/0e1eda4af6-1200.webp" srcset="/blog/img/o-fim-do-codigo-como-ativo-o-novo/0e1eda4af6-728.webp 728w, /blog/img/o-fim-do-codigo-como-ativo-o-novo/0e1eda4af6-1200.webp 1200w" sizes="(max-width: 760px) 100vw, 720px" width="1200" height="1143" alt="Sistema de notificações equivalente ao Slack" loading="lazy" decoding="async">
<figcaption>Sistema de notificações equivalente ao Slack</figcaption>
</figure>

A especificação está virando o produto; código, descartável. Essa é a inversão mais completa de como o software foi construído nos últimos cinquenta anos.

## A camada que ainda não tem nome

Entre a especificação e o código que vai para produção, existe agora uma camada de trabalho inteira que nenhum organograma sabe descrever. Alguém precisa quebrar o problema em pedaços do tamanho certo para o agente resolver. Alguém precisa saber quando deixar o agente correr e quando segurar a rédea. Alguém precisa corrigir a saída não reescrevendo o código, e sim ajustando o prompt, o contexto ou a restrição.

Vejo isso sendo chamado de trabalho supervisório… De arquitetura executável... De curadoria de agentes... Nenhum termo pegou ainda, e isso é um sintoma de que a disciplina está nascendo em tempo real dentro das empresas que usam IA para valer.

Esse trabalho supervisório é onde mora o novo valor. A habilidade crítica mudou da velocidade de digitação para a capacidade de carregar um modelo mental do sistema inteiro, antecipar onde a IA vai errar, definir fronteiras claras e avaliar saídas não-determinísticas sem ingenuidade.

## A fratura invisível: seniores afogados, juniores decolando, mid-levels na zona de perigo

Em qualquer time real que adotou IA de forma séria nos últimos meses, ocorre a mesma fratura. O time se divide em três grupos, e os três estão vivendo realidades completamente diferentes.

**Os seniores estão afogados.** Eles entendem o sistema inteiro, conhecem a dívida técnica, carregam o contexto histórico. Isso era superpoder. Agora vira condenação. Porque juniores e agentes geram código dez vezes mais rápido, e código não vai para produção sozinho. Precisa de revisão arquitetural, precisa encaixar em legado, precisa ser desbloqueado. O sênior vira *controlador de tráfego aéreo*. Passa o dia revisando saída de IA e já não produz quase nada de fato. A pessoa que antes era a mais produtiva do time passa a ser a menos produtiva em termos de linhas, e a mais crítica em termos de decisão. Ninguém sabe como medir isso e o burnout passa a ser real.

**Os juniores estão decolando.** Contraintuitivo? Sim. Mas faz sentido. O júnior não tem a memória muscular que manda escrever código de um jeito específico. Ele trata a IA como colega de equipe e não como ameaça à própria identidade. A fase deficitária do onboarding, aqueles seis meses em que o júnior drena produtividade do time enquanto aprende o código, praticamente desapareceu. Um júnior bem acoplado a um Claude Code entrega código útil na primeira semana.

**Os mid-levels estão na zona de perigo.** Três a cinco anos de experiência. Habilidade suficiente para ter vícios, insuficiente para ser arquiteto de verdade. Re-treinar um mid-level, tirando-o da lógica de escrever sintaxe e colocando-o na lógica de supervisionar agentes, é o desafio organizacional mais duro que líderes de tech estão encontrando em 2026. O problema costuma aparecer em conversas de corredor e em reuniões fechadas de liderança, mas raramente desce para o plano de carreira, para a política de compensação ou para a estrutura de contratação que a empresa pratica de fato.

Para o CEO, a implicação é estratégica. Se você só enxerga o time como um bloco, você vai tratar seniores e mid-levels com a mesma remuneração, as mesmas metas, o mesmo plano de carreira. E vai perder os seniores para o burnout e os mid-levels para a obsolescência, não necessariamente "ganhando” bons juniores.

## O problema do conhecimento "tribal"

Aqui mora uma armadilha específica, do tipo que só aparece depois que a empresa já apostou pesado em IA. O agente não tem contexto organizacional, mesmo você criando infinitas skills, plugins e MCPs. Não sabe que o job de marketing roda toda terça à meia-noite e satura o pool de conexões do banco. Não sabe que aquele microserviço mal-documentado foi escrito com pressa antes das férias de Natal e nunca mais foi tocado. Não sabe que o cliente X tem um contrato especial que impede um determinado comportamento do sistema.

Isso é conhecimento tribal. Vive na cabeça dos seniores. E quando um agente de IA é chamado para resolver um incidente às duas da manhã, ele lê a documentação oficial, segue o manual e *reinicia o servidor*. Repetidas vezes. Enquanto o time humano precisa ser chamado do mesmo jeito para descobrir que o problema era um *job batch* e não um serviço travado.

<div class="callout">
<p>“reiniciar o servidor” é uma ação voltada a sintomas, típica da IA que assume que há um serviço travado ou vazamento de recurso, por exemplo, enquanto o “job batch” no exemplo é a causa estrutural: um trabalho agendado, legítimo, que em determinado horário entra em cena e simplesmente consome CPU, memória ou conexões até saturar o sistema de novo.</p>
<p>Cada reboot limpa o estado por alguns segundos ou minutos, mas assim que o job volta a rodar, o problema reaparece, ou seja, a IA conduz o júnior a tratar o termômetro, não a febre. A correção real neste exemplo é atuar no próprio job para que ele não “coma todos os recursos de uma vez”; em termos de código, isso pode ser tão simples quanto adicionar um limitador de ritmo ou concorrência, por exemplo ao processar um lote:</p>
<p><em>for item in batch: process(item); time.sleep(0.01)</em></p>
<p>você transforma uma explosão de carga em um fluxo controlado que o sistema aguenta sem cair.</p>
</div>

A consequência é direta. Toda aquela promessa de “sistemas auto-cicatrizantes” (self-healing) movidos por IA continua sendo conversa de marketing enquanto a sua empresa não construir um subconsciente de agente. Um grafo de conhecimento que codifique cada incidente, cada caso de borda esquisito, cada pedaço de conhecimento institucional que mora só na cabeça da sua equipe mais experiente.

Construir isso parece investimento não urgente até o dia em que se torna(rá) o único investimento que importa.

## Agentes obedientes demais

Há outro ponto pouco discutido. Agentes de IA são treinados para serem prestativos. São puxa-sacos por construção. Querem te manter engajado na conversa, são propositivos mesmo quando não são necessários. Durante um incidente, o último que você quer no comando é um puxa-saco. Você quer alguém que desafie a hipótese, que force o humano a olhar para o que ele não quer olhar.

A resposta emergente dentro de times que já sofreram com isso é o que está sendo chamado de “agente bravo” (*angry agent*): um agente configurado especificamente para furar a teoria em curso, apontar contradições, exigir evidência. Trata-se de higiene de operação, ainda que muita gente trate como luxo dispensável. Porque dois agentes concordando entre si, o humano cansado e a IA obediente, é a receita perfeita para um servidor queimando enquanto todo mundo bate cabeça.

## Contratação: você está comprando a habilidade errada

Se você está contratando desenvolvedor agora com a descrição de vaga que usava há dois anos, você está comprando a habilidade errada. Velocidade para teclar código virou commodity, e deixou de funcionar como diferencial real.

O perfil que você precisa ter no time é outro. Pensamento arquitetural. Julgamento sob incerteza. Capacidade de escrever uma especificação tão precisa que a IA não consiga interpretar errado. Capacidade de desenhar um conjunto de testes que capture alucinações antes de elas irem para produção. Capacidade de debugar um sistema que a pessoa não escreveu pessoalmente.

Isso é uma pessoa completamente diferente da que você contratava em 2023-4. E o pior: muita gente no mercado ainda se vende pela habilidade antiga. Seu processo seletivo precisa mudar. Se o teste técnico é “escreva um algoritmo de busca binária em trinta minutos”, você está medindo exatamente a habilidade que a IA substituiu.

Um teste mais útil em 2026 que tenho aplicado nos processos de recrutamento é: *aqui está uma especificação \[vaga\], um sistema legado indo a meia-boca e um agente de IA à sua disposição. O que você faz?*

## Dívida cognitiva e compreensão contínua

Por décadas, o code review serviu para caçar bug e também, de quebra, para ensinar o sistema aos desenvolvedores. Se os agentes passam a escrever todo o código e o time para de ler, o time vira estrangeiro dentro do próprio produto.

Quando alguma coisa quebra às três da manhã, o seu engenheiro está encarando código escrito por uma máquina, tentando fazer engenharia reversa da lógica enquanto o cliente grita do outro lado.

Isso é **dívida cognitiva**. Ela se acumula silenciosamente, sem aparecer em nenhum dashboard, até o dia em que o time inteiro não consegue mais tomar uma decisão arquitetural porque ninguém mais entende de fato o que o software faz.

A contramedida é **agendar compreensão**. Rituais semanais de arquitetura conjunta, mesmo que o código venha dos agentes. Ferramentas que resumem mudanças arquiteturais para humanos. Sessões deliberadas de leitura de código, como se fossem sessões de leitura coletiva. Soa analógico em um mundo que vende autonomia total. É justamente por isso que funciona.

## O que fazer como CEO

Três movimentos concretos para as próximas semanas.

Primeiro, audite seu time pela lente de senior-afogado, júnior-decolando, mid-level-em-risco. Trata-se de diagnóstico estratégico, e quem trata isso como exercício burocrático de RH perde os melhores primeiro. O plano de carreira, a compensação e o treinamento precisam ser diferentes para cada grupo. Tratar todo mundo igual é garantir perda de talento nos próximos doze meses. O princípio da equidade vale aqui também.

<div class="callout">
<p>O princípio da equidade é a ideia de tratar pessoas de forma diferente para gerar resultados justos, e que no balanço todos sejam igualmente bem tratados. Enquanto igualdade distribui os mesmos recursos para todos, equidade ajusta recursos, metas e condições conforme a necessidade, o contexto e o impacto esperado. Em gestão de times, isso significa não dar o mesmo plano de carreira, métricas e carga para sênior, júnior e mid-level, mas calibrar incentivos e suporte conforme o papel estratégico de cada um. A régua não é “todo mundo igual”, e sim “cada pessoa recebe o que precisa para contribuir ao máximo e não ser prejudicada pelo contexto”.</p>
</div>

Segundo, comece a construir o subconsciente da sua empresa. Todo incidente vira aprendizado documentado de forma que um agente consiga consumir. Toda regra implícita vira regra explícita. Toda decisão arquitetural ganha um *why* registrado. Isso parece infraestrutura chata até o dia em que é o diferencial competitivo. Para este subconsciente ter impacto relevante, você precisará de volume relativamente grande de registros. E quanto mais volume tiver, mais registros conseguirá criar antecipadamente, mitigando problemas futuros.

Terceiro, mude o filtro de contratação. Pare de comprar velocidade de digitação. Comece a comprar julgamento, clareza de especificação e tolerância à ambiguidade. O teste técnico precisa refletir o trabalho real de 2026, não o trabalho real de 2018.

A analogia final que eu gosto é a da placa gráfica. Em 1992, engenheiros calculavam à mão a matemática para desenhar um único polígono na tela. Em 1994, as GPUs chegaram e assumiram essa tarefa. Quem insistiu em codar polígono à mão em 1995 não virou especialista. Virou obsoleto. Os que sobreviveram viraram engenheiros de iluminação, animadores, programadores de física. Pararam de ensinar o computador a desenhar um triângulo e começaram a ensinar o computador a simular como a luz reflete em uma rua molhada.

A engenharia de software está vivendo exatamente esse momento. Ninguém mais vai codar polígono à mão em 2028. A pergunta é se o seu time vai estar entre os que escalaram para a nova camada ou entre os que insistiram na antiga até ficarem obsoletos.

Os melhores fundadores e CEOs que eu conheço não estão em pânico com isso. Estão adaptando. Silenciosamente, metodicamente, reescrevendo cargos, planos de carreira e processos. Seja um deles.
