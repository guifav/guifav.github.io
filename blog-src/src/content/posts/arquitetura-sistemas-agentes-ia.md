---
title: "Agentes de IA como Componentes de Sistema: Um Novo Paradigma Arquitetural"
description: "Entenda como a arquitetura de agentes de IA está transformando o design de sistemas e aprenda a projetar soluções que integrem agentes como componentes ativos."
date: 2024-11-13T03:00:00.000Z
updated: 2026-11-24
slug: arquitetura-sistemas-agentes-ia
cover: img/arquitetura-sistemas-agentes-ia/capa.jpg
type: newsletter
---

## Introduzindo a era da Interação Agente-Computador

Enquanto toda a comunidade de desenvolvimento rapidamente adotou os Grandes Modelos de Linguagem (LLMs) como [<u>ferramentas de design, iterações, idéias e soluções</u>](/blog/como-designers-profissionais-usam-ia-guia-completo-2024/), em grande parte negligenciamos suas implicações mais profundas. Agora, à medida que os agentes de IA se integram em nossos produtos digitais, enfrentamos uma transformação fundamental: esses sistemas estão evoluindo de ferramentas para participantes ativos em nossos ambientes digitais, e precisamos projetar para eles.

*Os agentes de IA estão emergindo como uma nova classe de usuários, navegando independentemente pelas interfaces que projetamos e executando tarefas complexas em nosso nome. Isso marca o início de uma nova era de interação, a Interação Agente-Computador, onde a experiência do usuário abrange não apenas a relação humano-computador, mas também as experiências dos agentes de IA.*

Reconhecidamente, os humanos permanecem parte integral desta nova dinâmica, fornecendo supervisão e orientação. Ainda assim, os agentes de IA devem agora ser considerados personas distintas de usuários, com necessidades, capacidades e objetivos únicos. Isso implica projetar a experiência tanto para humanos quanto para agentes, criando interfaces que atendam a ambos e garantindo que tenham os recursos e informações necessários para funcionar efetivamente.

## Compreendendo os Agentes de IA

O Google I/O definiu agentes de IA como sistemas inteligentes capazes de raciocinar, planejar, reter informações e pensar vários passos à frente, tudo isso enquanto operam em vários softwares e sistemas sob supervisão humana. Outras empresas podem estruturar suas definições de maneira diferente, mas compartilham este conceito essencial: IA que pode pensar vários passos à frente e reter contexto enquanto trabalha independentemente. É como ter um assistente digital que pode verdadeiramente antecipar suas necessidades e resolver problemas proativamente.

[<u>Se não conhece sobre Agentes de IA, comece por aqui.</u>](/blog/agentes-ia-por-onde-comecar/)

<figure>
<img src="/blog/img/arquitetura-sistemas-agentes-ia/a93b1614f6-1400.webp" srcset="/blog/img/arquitetura-sistemas-agentes-ia/a93b1614f6-728.webp 728w, /blog/img/arquitetura-sistemas-agentes-ia/a93b1614f6-1400.webp 1400w" sizes="(max-width: 760px) 100vw, 720px" width="1400" height="780" alt="Agentes de IA de acordo com o Google" loading="lazy" decoding="async">
<figcaption>Agentes de IA de acordo com o Google</figcaption>
</figure>

Enquanto os primeiros agentes de IA dependiam exclusivamente de APIs para interagir com outros sistemas, avanços recentes, particularmente no "uso do computador" pioneiro por modelos como Claude, desbloquearam um novo nível de agência. Esses agentes avançados agora podem interagir diretamente com interfaces gráficas de usuário, controlando o cursor, inserindo inputs e navegando por aplicativos como um usuário humano. Isso lhes concede acesso sem precedentes a produtos baseados em navegador, permitindo que realizem tarefas com um nível de autonomia e sofisticação que nunca vimos antes.

Nesta nova era de Interação Agente-Computador, os profissionais de IA devem escolher entre duas abordagens para permitir que os [<u>agentes de IA interajam com software</u>](/blog/como-criar-agentes-ia-guia-passo-a-passo/):

1. **Integração Direta via API ou "uso de ferramentas"**: Usando chamadas de função e APIs para interagir com sistemas programaticamente. Isso é frequentemente mais eficiente, pois evita a sobrecarga de renderizar interfaces visuais. No entanto, a qualidade e cobertura da API pode variar.
2. **Interação com Interface Visual ou "ferramentas humanas"**: Tendo agentes de IA interagindo com software através de suas interfaces gráficas de usuário, como os humanos fazem. Embora potencialmente mais lento, esta abordagem oferece maior transparência e permite que os humanos monitorem e controlem melhor as ações da IA.

A integração via API pode ser ideal para tarefas bem definidas de alto volume onde velocidade e eficiência são mais importantes. A interação com interface visual pode ser mais adequada para tarefas que requerem supervisão humana cuidadosa, fornecendo mais transparência e controle. Profissionais de UX e parceiros multifuncionais enfrentam um desafio crucial em determinar o método de interação mais eficaz para cada caso de uso e os relacionamentos com usuários finais.

<figure>
<img src="/blog/img/arquitetura-sistemas-agentes-ia/6307740f6a-1400.webp" srcset="/blog/img/arquitetura-sistemas-agentes-ia/6307740f6a-728.webp 728w, /blog/img/arquitetura-sistemas-agentes-ia/6307740f6a-1400.webp 1400w" sizes="(max-width: 760px) 100vw, 720px" width="1400" height="789" alt="Compute Use da Anthropic" loading="lazy" decoding="async">
<figcaption>Compute Use da Anthropic</figcaption>
</figure>

## Projetando a experiência para agentes de IA

À medida que os agentes de IA evoluem para usuários ativos de nossos produtos digitais, os arquitetos de soluções, junto com os designers de UI/UX, precisam expandir sua prática para considerar esses novos participantes. Assim como pesquisamos as necessidades dos usuários humanos, agora devemos entender suas capacidades, o que eles requerem para funcionar efetivamente e como alcançam seus objetivos designados.

Embora os agentes ultimamente sirvam às intenções humanas, eles frequentemente trabalham em redes complexas onde interagem com outros agentes para completar tarefas. Por exemplo, um agente pode processar dados que outro agente usa para fazer recomendações, tudo a serviço da solicitação original de um humano. Isso cria novas camadas de interação que os designers devem considerar e apoiar.

Assim como criamos personas para usuários humanos, agora devemos desenvolver personas para agentes de IA. Essas personas devem capturar as nuances do comportamento de um agente, suas forças e limitações, e suas capacidades em evolução à medida que a tecnologia e o contexto avançam. Isso nos permitirá projetar interfaces e interações otimizadas para fluxos de trabalho do agente, assim como fazemos para os usuário humanos.

## Moldando a Mente da IA

Os Grande Modelos de Linguagem (LLMs) são os "cérebros" por trás dos agentes de IA, imbuindo-os com capacidades de inteligência e raciocínio. Mas os designers de UX têm um papel crucial em moldar esses LLMs, indo além do design de interface para influenciar o próprio núcleo do comportamento do agente.

Na modelagem da mente da IA, os designers e UI/UX profissionais possuem uma compreensão única das necessidades e modelos mentais dos usuários. Essa expertise é inestimável na elaboração de prompts efetivos que guiam LLMs para resultados desejados. Colaborando estreitamente com engenheiros para desenvolver prompts de sistema alinhados com a intenção do usuário, podemos garantir que os agentes de IA forneçam experiências relevantes e significativas a serviço das pessoas.

Além disso, os designers de UX devem participar ativamente na criação de estratégias para avaliar o desempenho do agente e aproveitar o feedback do usuário para refinar o comportamento do LLM. Isso envolve estabelecer um volante de dados que melhora continuamente a capacidade do agente de entender e responder às necessidades do usuário.

A principal conclusão é esta: projetar PARA agentes de IA requer uma mudança de mentalidade. Não se está apenas criando um produto para eles usarem; estamos ativamente moldando os próprios agentes, influenciando sua inteligência e comportamento através de prompting cuidadoso e feedback contínuo. Isso representa uma nova fronteira para a arquitetura de soluções, onde nossa expertise se estende ao próprio coração da IA.

## Mantendo Humanos no Loop

Embora projetar para agentes de IA apresente novos desafios empolgantes, nunca devemos perder de vista o objetivo de todas as soluções para humanos: aprimorar experiências humanas. A IA deve servir à humanidade, e nossos esforços de design de soluções devem priorizar necessidades e valores humanos.

O controle é primordial. Profissionais de UX devem considerar cuidadosamente como capacitar usuários com agência sobre suas interações com IA. Isso inclui projetar mecanismos claros para conceder permissões, fornecer contexto sobre acesso a dados e oferecer opções de opt-out para aqueles que preferem não se envolver com agentes de IA. Estabelecer confiança através do controle do usuário é essencial para a adoção bem-sucedida de experiências dirigidas por agentes.

A transparência é igualmente crucial. Os usuários precisam de insights claros sobre como os agentes de IA utilizam seus dados, interagem com suas ferramentas e colaboram com outros agentes. Em cenários envolvendo múltiplos agentes de diferentes empresas, os usuários devem ter visibilidade dos participantes e a capacidade de escolher quais entidades eles permitem em seu ecossistema digital.

Felizmente, podemos nos basear em frameworks de arquitetura de sistemas existentes para navegar neste complexo ambiente. O design de sistema, por exemplo, oferece ferramentas valiosas para visualizar os relacionamentos intrincados dentro de ecossistemas dirigidos por agentes, mantendo uma perspectiva centrada no humano.

<figure>
<img src="/blog/img/arquitetura-sistemas-agentes-ia/4632d96a43-1456.webp" srcset="/blog/img/arquitetura-sistemas-agentes-ia/4632d96a43-728.webp 728w, /blog/img/arquitetura-sistemas-agentes-ia/4632d96a43-1456.webp 1456w" sizes="(max-width: 760px) 100vw, 720px" width="1456" height="1349" alt="Exemplo de arquitetura pensada em Usuário Humano e Agente de IA" loading="lazy" decoding="async">
<figcaption>Exemplo de arquitetura pensada em Usuário Humano e Agente de IA</figcaption>
</figure>

Adaptando técnicas de mapeamento como blueprints comumente usados em design de serviço, podemos representar efetivamente a interação entre humanos, agentes e produtos, destacando linhas de visibilidade e controle.

## Conclusão

Estamos entrando em uma nova fase do design digital onde agentes de IA estão se tornando usuários ativos de nossos sistemas, não apenas ferramentas dentro deles. Esta mudança requer que designers de UX expandam sua perspectiva, considerando como humanos e agentes de IA interagem com interfaces e entre si.

Embora a rápida evolução da tecnologia de IA possa parecer avassaladora, este campo ainda está em seus estágios iniciais. Os princípios fundamentais do design de experiência do usuário permanecem valiosos — estamos simplesmente estendendo-os para abranger usuários artificiais junto com humanos. Isso apresenta uma oportunidade única para designers moldarem como os agentes de IA interagem com sistemas e servem às necessidades humanas.

O futuro do design de soluções inclui entender e projetar para esta Interação Agente-Computador. Aqueles que desenvolverem expertise nesta área agora ajudarão a definir as melhores práticas para os anos vindouros.

## Para maiores informações

**Inspiração para este artigo:**

- **Anthropic** — [<u>Introducing computer use</u>](https://www.anthropic.com/news/3-5-models-and-computer-use)
- **NNg** — [<u>Service Blueprints</u>](https://www.nngroup.com/articles/service-blueprints-definition/)
- **Latent Space podcast** — [<u>Language Agents: From Reasoning to Acting</u>](https://youtu.be/8t65bss7U74?si=sxz9x8SXJsi9xdz-)
- **Cognition** — [<u>Introducing Devin</u>](https://www.cognition.ai/blog/introducing-devin)
- **MIT technology Review** — [<u>Google’s Astra is its first AI-for-everything agent</u>](https://www.technologyreview.com/2024/05/14/1092407/googles-astra-is-its-first-ai-for-everything-agent/)
- **Alex Klein** — [<u>The agentic era of UX</u>](https://uxdesign.cc/the-agentic-era-of-ux-4b58634e410b)

**Vídeos que já produzi sobre Agentes de IA**

- [**<u>OpenAI Swarm</u>**](https://youtu.be/Cma66RFTqeE)[<u>: Desvendando o Poder dos Multi-Agentes na IA</u>](https://youtu.be/Cma66RFTqeE)
- [**<u>O Futuro Multimodal da IA</u>**](https://youtu.be/1ffecoemR_Q)[<u>: Explorando Agentes Inteligentes</u>](https://youtu.be/1ffecoemR_Q)
- [**<u>Tutorial Prático</u>**](https://youtu.be/emyRZqY1jpo)[<u> - Aprenda a Criar Agente de IA em 2024</u>](https://youtu.be/emyRZqY1jpo)
- [**<u>O Futuro Multimodal da IA</u>**](https://youtu.be/Te3-B3haeDI)[<u>: Explorando Agentes Inteligentes</u>](https://youtu.be/Te3-B3haeDI)
