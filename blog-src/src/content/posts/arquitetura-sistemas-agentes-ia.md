---
title: "Agentes de IA como Componentes de Sistema: Um Novo Paradigma Arquitetural"
description: "Entenda como a arquitetura de agentes de IA está transformando o design de sistemas e aprenda a projetar soluções que integrem agentes como componentes ativos."
summary: "Agentes de IA viraram uma nova classe de usuários dos sistemas e precisam ser projetados como personas próprias, com capacidades e limitações, na chamada Interação Agente-Computador. Eles se conectam ao software por API, mais eficiente em tarefas de alto volume, ou pela interface gráfica, como no computer use do Claude, possivelmente mais lenta, porém mais transparente para supervisão humana. Manter humanos no loop exige controle sobre permissões e visibilidade sobre o uso dos dados."
date: 2024-11-13T03:00:00.000Z
slug: arquitetura-sistemas-agentes-ia
cover: img/arquitetura-sistemas-agentes-ia/capa.jpg
type: newsletter
topics: [agentes-de-ia]
---

Agentes de IA passaram a usar os sistemas da empresa como usuários: navegam pelas interfaces que projetamos, chamam APIs e executam tarefas em nosso nome. Projetar um sistema hoje inclui decidir como esse agente se conecta ao software (por API ou pela interface gráfica), que persona ele representa e onde os humanos mantêm controle sobre permissões e dados.

A comunidade de desenvolvimento adotou rapidamente os Grandes Modelos de Linguagem (LLMs) como [<u>ferramentas de design, iterações, idéias e soluções</u>](/blog/como-designers-profissionais-usam-ia-guia-completo-2024/), e em grande parte negligenciou suas implicações mais profundas. Agora, à medida que os agentes de IA se integram em nossos produtos digitais, esses sistemas evoluem de ferramentas para participantes ativos em nossos ambientes digitais, e precisamos projetar para eles.

*Os agentes de IA estão emergindo como uma nova classe de usuários, navegando independentemente pelas interfaces que projetamos e executando tarefas complexas em nosso nome. Isso marca o início de uma nova era de interação, a Interação Agente-Computador, em que a experiência do usuário abrange a relação humano-computador e também as experiências dos agentes de IA.*

## O que muda quando agentes de IA viram usuários dos sistemas da empresa?

Os humanos permanecem parte integral desta nova dinâmica, fornecendo supervisão e orientação. Ainda assim, os agentes de IA devem agora ser considerados personas distintas de usuários, com necessidades, capacidades e objetivos únicos. Isso implica projetar a experiência tanto para humanos quanto para agentes, criando interfaces que atendam a ambos e garantindo que tenham os recursos e informações necessários para funcionar efetivamente.

## O que é um agente de IA?

O Google I/O definiu agentes de IA como sistemas inteligentes capazes de raciocinar, planejar, reter informações e pensar vários passos à frente, tudo isso enquanto operam em vários softwares e sistemas sob supervisão humana. Outras empresas podem estruturar suas definições de maneira diferente, mas compartilham este conceito essencial: IA que pode pensar vários passos à frente e reter contexto enquanto trabalha independentemente. Na prática, funciona como um assistente digital que antecipa necessidades e resolve problemas proativamente.

[<u>Se não conhece sobre Agentes de IA, comece por aqui.</u>](/blog/agentes-ia-por-onde-comecar/)

<figure>
<img src="/blog/img/arquitetura-sistemas-agentes-ia/a93b1614f6-1400.webp" srcset="/blog/img/arquitetura-sistemas-agentes-ia/a93b1614f6-728.webp 728w, /blog/img/arquitetura-sistemas-agentes-ia/a93b1614f6-1400.webp 1400w" sizes="(max-width: 760px) 100vw, 720px" width="1400" height="780" alt="Agentes de IA de acordo com o Google" loading="lazy" decoding="async">
<figcaption>Agentes de IA de acordo com o Google</figcaption>
</figure>

## O agente deve usar a API ou a interface gráfica do sistema?

Os primeiros agentes de IA dependiam exclusivamente de APIs para interagir com outros sistemas. Avanços recentes, particularmente o "uso do computador" pioneiro em modelos como Claude, desbloquearam um novo nível de agência. Esses agentes agora podem interagir diretamente com interfaces gráficas de usuário, controlando o cursor, inserindo inputs e navegando por aplicativos como um usuário humano. Isso lhes concede acesso sem precedentes a produtos baseados em navegador, permitindo que realizem tarefas com um nível de autonomia e sofisticação que nunca vimos antes.

Nesta nova era de Interação Agente-Computador, os profissionais de IA devem escolher entre duas abordagens para permitir que os [<u>agentes de IA interajam com software</u>](/blog/como-criar-agentes-ia-guia-passo-a-passo/):

1. **Integração Direta via API ou "uso de ferramentas"**: Usando chamadas de função e APIs para interagir com sistemas programaticamente. Isso é frequentemente mais eficiente, pois evita a sobrecarga de renderizar interfaces visuais. No entanto, a qualidade e cobertura da API pode variar.
2. **Interação com Interface Visual ou "ferramentas humanas"**: Tendo agentes de IA interagindo com software através de suas interfaces gráficas de usuário, como os humanos fazem. Embora potencialmente mais lento, esta abordagem oferece maior transparência e permite que os humanos monitorem e controlem melhor as ações da IA.

| Critério | Integração via API (uso de ferramentas) | Interface visual (uso do computador) |
|---|---|---|
| Como o agente age | Chamadas de função e APIs, de forma programática | Controla o cursor, insere dados e navega como um usuário humano |
| Eficiência | Maior, porque evita renderizar interfaces | Potencialmente mais lenta |
| Supervisão humana | Depende do que o sistema registra das chamadas | Humanos acompanham e controlam melhor as ações |
| Limitação principal | Qualidade e cobertura da API variam | Velocidade |
| Quando usar | Tarefas bem definidas e de alto volume | Tarefas que pedem supervisão humana cuidadosa |

A integração via API pode ser ideal para tarefas bem definidas de alto volume onde velocidade e eficiência são mais importantes. A interação com interface visual pode ser mais adequada para tarefas que requerem supervisão humana cuidadosa, fornecendo mais transparência e controle. Profissionais de UX e parceiros multifuncionais enfrentam um desafio crucial em determinar o método de interação mais eficaz para cada caso de uso e os relacionamentos com usuários finais.

<figure>
<img src="/blog/img/arquitetura-sistemas-agentes-ia/6307740f6a-1400.webp" srcset="/blog/img/arquitetura-sistemas-agentes-ia/6307740f6a-728.webp 728w, /blog/img/arquitetura-sistemas-agentes-ia/6307740f6a-1400.webp 1400w" sizes="(max-width: 760px) 100vw, 720px" width="1400" height="789" alt="Compute Use da Anthropic" loading="lazy" decoding="async">
<figcaption>Compute Use da Anthropic</figcaption>
</figure>

## Como projetar a experiência para agentes de IA?

À medida que os agentes de IA evoluem para usuários ativos de nossos produtos digitais, os arquitetos de soluções, junto com os designers de UI/UX, precisam expandir sua prática para considerar esses novos participantes. Assim como pesquisamos as necessidades dos usuários humanos, agora devemos entender suas capacidades, o que eles requerem para funcionar efetivamente e como alcançam seus objetivos designados.

Embora os agentes, em última instância, sirvam às intenções humanas, eles frequentemente trabalham em redes complexas onde interagem com outros agentes para completar tarefas. Por exemplo, um agente pode processar dados que outro agente usa para fazer recomendações, tudo a serviço da solicitação original de um humano. Isso cria novas camadas de interação que os designers devem considerar e apoiar.

Assim como criamos personas para usuários humanos, agora devemos desenvolver personas para agentes de IA. Essas personas devem capturar as nuances do comportamento de um agente, suas forças e limitações, e suas capacidades em evolução à medida que a tecnologia e o contexto avançam. Isso nos permitirá projetar interfaces e interações otimizadas para fluxos de trabalho do agente, assim como fazemos para os usuários humanos.

## Qual é o papel de quem projeta no comportamento do agente?

Os Grandes Modelos de Linguagem (LLMs) são os "cérebros" por trás dos agentes de IA, imbuindo-os com capacidades de inteligência e raciocínio. Os designers de UX têm um papel crucial em moldar esses LLMs, indo além do design de interface para influenciar o próprio núcleo do comportamento do agente.

Na modelagem da mente da IA, os designers e UI/UX profissionais possuem uma compreensão única das necessidades e modelos mentais dos usuários. Essa expertise é inestimável na elaboração de prompts efetivos que guiam LLMs para resultados desejados. Colaborando estreitamente com engenheiros para desenvolver prompts de sistema alinhados com a intenção do usuário, podemos garantir que os agentes de IA forneçam experiências relevantes e significativas a serviço das pessoas.

Além disso, os designers de UX devem participar ativamente na criação de estratégias para avaliar o desempenho do agente e aproveitar o feedback do usuário para refinar o comportamento do LLM. Isso envolve estabelecer um volante de dados que melhora continuamente a capacidade do agente de entender e responder às necessidades do usuário.

Projetar PARA agentes de IA requer uma mudança de mentalidade. Além de criar um produto para eles usarem, estamos moldando os próprios agentes, influenciando sua inteligência e comportamento através de prompting cuidadoso e feedback contínuo. Isso representa uma nova fronteira para a arquitetura de soluções, onde nossa expertise se estende ao próprio coração da IA.

## Como manter humanos no controle de sistemas com agentes de IA?

Embora projetar para agentes de IA apresente novos desafios empolgantes, nunca devemos perder de vista o objetivo de todas as soluções para humanos: aprimorar experiências humanas. A IA deve servir à humanidade, e nossos esforços de design de soluções devem priorizar necessidades e valores humanos.

O controle é primordial. Profissionais de UX devem considerar cuidadosamente como capacitar usuários com agência sobre suas interações com IA. Isso inclui projetar mecanismos claros para conceder permissões, fornecer contexto sobre acesso a dados e oferecer opções de opt-out para aqueles que preferem não se envolver com agentes de IA. Estabelecer confiança através do controle do usuário é essencial para a adoção bem-sucedida de experiências dirigidas por agentes.

A transparência é igualmente crucial. Os usuários precisam de insights claros sobre como os agentes de IA utilizam seus dados, interagem com suas ferramentas e colaboram com outros agentes. Em cenários envolvendo múltiplos agentes de diferentes empresas, os usuários devem ter visibilidade dos participantes e a capacidade de escolher quais entidades eles permitem em seu ecossistema digital.

| Princípio | O que o sistema precisa oferecer ao usuário |
|---|---|
| Controle | Mecanismos claros para conceder permissões ao agente |
| Controle | Contexto sobre quais dados o agente acessa |
| Controle | Opção de opt-out para quem prefere não se envolver com agentes |
| Transparência | Visibilidade sobre como o agente usa os dados, as ferramentas e outros agentes |
| Transparência | Com agentes de várias empresas, saber quem participa e escolher quem entra no seu ecossistema |

Felizmente, podemos nos basear em frameworks de arquitetura de sistemas existentes para navegar neste complexo ambiente. O design de sistema, por exemplo, oferece ferramentas valiosas para visualizar os relacionamentos intrincados dentro de ecossistemas dirigidos por agentes, mantendo uma perspectiva centrada no humano.

<figure>
<img src="/blog/img/arquitetura-sistemas-agentes-ia/4632d96a43-1456.webp" srcset="/blog/img/arquitetura-sistemas-agentes-ia/4632d96a43-728.webp 728w, /blog/img/arquitetura-sistemas-agentes-ia/4632d96a43-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="1349" alt="Exemplo de arquitetura pensada em Usuário Humano e Agente de IA" loading="lazy" decoding="async">
<figcaption>Exemplo de arquitetura pensada em Usuário Humano e Agente de IA</figcaption>
</figure>

Adaptando técnicas de mapeamento como blueprints comumente usados em design de serviço, podemos representar efetivamente a interação entre humanos, agentes e produtos, destacando linhas de visibilidade e controle.

## O que isso muda para quem projeta sistemas?

Estamos entrando em uma nova fase do design digital, em que agentes de IA se tornam usuários ativos de nossos sistemas, além de ferramentas dentro deles. Esta mudança requer que designers de UX expandam sua perspectiva, considerando como humanos e agentes de IA interagem com interfaces e entre si.

Embora a rápida evolução da tecnologia de IA possa parecer avassaladora, este campo ainda está em seus estágios iniciais. Os princípios fundamentais do design de experiência do usuário permanecem valiosos, e estamos estendendo-os para abranger usuários artificiais junto com humanos. Isso apresenta uma oportunidade única para designers moldarem como os agentes de IA interagem com sistemas e servem às necessidades humanas.

O futuro do design de soluções inclui entender e projetar para esta Interação Agente-Computador. Aqueles que desenvolverem expertise nesta área agora ajudarão a definir as melhores práticas para os anos vindouros.

## Perguntas frequentes sobre agentes de IA como componentes de sistema

### Qual é a diferença entre um agente de IA e uma automação tradicional?

Uma automação tradicional segue passos definidos de antemão. Um agente de IA, na definição apresentada no Google I/O, raciocina, planeja, retém informações e pensa vários passos à frente enquanto opera em vários softwares e sistemas, sob supervisão humana. Para montar o primeiro, veja o [guia passo a passo para criar agentes de IA](/blog/como-criar-agentes-ia-guia-passo-a-passo/).

### Quando o agente deve agir por API e quando pela interface gráfica?

Por API, quando a tarefa é bem definida, de alto volume, e a velocidade pesa mais. Pela interface gráfica, quando a tarefa pede supervisão humana cuidadosa, porque quem acompanha vê e controla melhor cada ação do agente, ao custo de uma execução possivelmente mais lenta.

### Por que criar uma persona para o agente de IA?

A persona registra as forças, as limitações e as capacidades do agente, que mudam com a tecnologia e o contexto. Com ela, a equipe projeta interfaces, APIs e fluxos que o agente consegue usar bem, do mesmo jeito que faz para os usuários humanos.

### Como o usuário sabe o que o agente faz com os dados dele?

O sistema precisa mostrar quais dados o agente acessa, como ele usa as ferramentas e com quais outros agentes colabora, e deixar o usuário conceder, recusar ou retirar permissões. Blueprints de design de serviço ajudam a desenhar essas linhas de visibilidade e controle entre humanos, agentes e produtos.

## Para maiores informações

**Inspiração para este artigo:**

- **Anthropic**: [<u>Introducing computer use</u>](https://www.anthropic.com/news/3-5-models-and-computer-use)
- **NNg**: [<u>Service Blueprints</u>](https://www.nngroup.com/articles/service-blueprints-definition/)
- **Latent Space podcast**: [<u>Language Agents: From Reasoning to Acting</u>](https://youtu.be/8t65bss7U74?si=sxz9x8SXJsi9xdz-)
- **Cognition**: [<u>Introducing Devin</u>](https://www.cognition.ai/blog/introducing-devin)
- **MIT technology Review**: [<u>Google’s Astra is its first AI-for-everything agent</u>](https://www.technologyreview.com/2024/05/14/1092407/googles-astra-is-its-first-ai-for-everything-agent/)
- **Alex Klein**: [<u>The agentic era of UX</u>](https://uxdesign.cc/the-agentic-era-of-ux-4b58634e410b)

**Vídeos que já produzi sobre Agentes de IA**

- [**<u>OpenAI Swarm</u>**](https://youtu.be/Cma66RFTqeE)[<u>: Desvendando o Poder dos Multi-Agentes na IA</u>](https://youtu.be/Cma66RFTqeE)
- [**<u>O Futuro Multimodal da IA</u>**](https://youtu.be/1ffecoemR_Q)[<u>: Explorando Agentes Inteligentes</u>](https://youtu.be/1ffecoemR_Q)
- [**<u>Tutorial Prático</u>**](https://youtu.be/emyRZqY1jpo)[<u>: Aprenda a Criar Agente de IA em 2024</u>](https://youtu.be/emyRZqY1jpo)
- [**<u>O Futuro Multimodal da IA</u>**](https://youtu.be/Te3-B3haeDI)[<u>: Explorando Agentes Inteligentes</u>](https://youtu.be/Te3-B3haeDI)
