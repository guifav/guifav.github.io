---
title: "[atualizado] Melhores práticas para uso do ChatGPT e outros LLMs"
description: "Ao seguir essas orientações e melhores práticas, você pode criar prompts eficazes que aproveitem ao máximo as capacidades dos LLMs."
date: 2024-05-31T03:00:00.000Z
updated: 2024-08-26T10:40:57.525Z
slug: melhores-praticas-para-uso-do-chatgpt-e-outros-llms
cover: img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/capa.jpg
type: newsletter
---

### Ao seguir essas orientações e melhores práticas, você pode criar prompts eficazes que aproveitem ao máximo as capacidades dos LLMs.

Este post esta com flag de "atualizado" pois há alguns meses escrevi um outro sobre este mesmo tema.

O ChatGPT muito em breve irá liberar para uso gratuito grande parte dos seus recursos que hoje são exclusivos para os pagantes. E por isso, a newsletter de hoje é um pouco diferente.

Hoje, estou aqui para te ajudar a tirar o máximo proveito destes LLMs conversacionais (Large Language Models - Grande Modelo de Linguagem), como o:

- [ChatGPT](https://chatgpt.com/)
- [Claude](https://claude.ai/)
- [Gemini](https://gemini.google.com/app)
- [HuggingChat](https://huggingface.co/chat/)
- [Groq](https://groq.com/)
- [Grok](https://grok.x.ai/)
- [Perplexity](https://www.perplexity.ai/)
- [Maritaca](https://chat.maritaca.ai/) (Brazuca) e muitos outros.

(as duas últimas dicas são para *devs*!)

## Seja claro e específico em suas instruções

<figure>
<img src="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/4fe71dfef8-1000.webp" srcset="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/4fe71dfef8-728.webp 728w, /blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/4fe71dfef8-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="571" alt="Seja claro e específico em suas instruções" loading="lazy" decoding="async">
</figure>

Defina de forma precisa a tarefa ou o objetivo que você deseja que o LLM realize. Evite ambiguidades ou instruções vagas que possam levar a interpretações equivocadas. Forneça contexto suficiente para que o modelo compreenda plenamente o que você está pedindo. Inclua detalhes relevantes, como o público-alvo, o formato desejado da resposta e quaisquer restrições ou requisitos específicos. Use linguagem simples e direta, evitando jargões ou terminologia excessivamente técnica, a menos que seja absolutamente necessário para a tarefa em questão.

## Forneça exemplos ilustrativos (n-shot prompts)

<figure>
<img src="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/36a84bf118-1000.webp" srcset="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/36a84bf118-728.webp 728w, /blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/36a84bf118-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="571" alt="Forneça exemplos ilustrativos (n-shot prompts)" loading="lazy" decoding="async">
</figure>

Inclua alguns exemplos (geralmente 5 ou mais) que demonstrem o tipo de resposta que você espera. Isso é conhecido como "n-shot prompting" e permite que o modelo aprenda com o contexto fornecido. Certifique-se de que os exemplos sejam representativos da distribuição de entradas esperada. Por exemplo, se você estiver construindo um sumarizador de filmes, inclua amostras de diferentes gêneros nas proporções aproximadas que você espera ver na prática. Você não precisa necessariamente fornecer pares completos de entrada-saída. Em muitos casos, exemplos das saídas desejadas são suficientes para orientar o modelo.

## Utilize o chain-of-thought prompting

<figure>
<img src="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/173c959b96-1000.webp" srcset="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/173c959b96-728.webp 728w, /blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/173c959b96-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="571" alt="chain-of-thought prompting" loading="lazy" decoding="async">
</figure>

Encoraje o LLM a explicar seu processo de pensamento antes de fornecer a resposta final. Isso pode ajudar a reduzir as taxas de "alucinação" e melhorar a qualidade dos resultados. Adicione uma frase como "Vamos pensar passo a passo" como parte das instruções. No entanto, é útil tornar o *chain-of-thought* mais específico, adicionando uma ou duas frases extras para reduzir significativamente as taxas de alucinação. Por exemplo, ao pedir a um LLM para resumir uma transcrição de reunião, você pode ser explícito sobre as etapas, como: (1) listar as principais decisões, itens de acompanhamento e responsáveis; (2) verificar se os detalhes são factualmente consistentes com a transcrição; (3) sintetizar os pontos-chave em um resumo conciso.

## Forneça recursos relevantes

<figure>
<img src="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/0fb2c12abe-1000.webp" srcset="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/0fb2c12abe-728.webp 728w, /blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/0fb2c12abe-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="571" alt="relevant resources for ai" loading="lazy" decoding="async">
</figure>

Quando possível, inclua *snippets* de texto, artigos, imagens, fotos, ou outros materiais que possam ajudar o LLM a gerar uma resposta mais embasada e precisa. Indique claramente que o modelo deve priorizar o uso desses recursos, referindo-se a eles diretamente na resposta. Isso pode ajudar a "fundamentar" as respostas do modelo e reduzir a ocorrência de informações imprecisas ou inventadas.

## Mantenha os prompts pequenos e focados

<figure>
<img src="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/a988f63a25-1000.webp" srcset="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/a988f63a25-728.webp 728w, /blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/a988f63a25-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="571" alt="small and focused prompts" loading="lazy" decoding="async">
</figure>

Evite sobrecarregar um único prompt com várias tarefas ou instruções complexas. Isso pode levar a um desempenho inferior e dificuldade de manutenção. Em vez disso, quebre tarefas complexas em prompts menores e mais focados. Por exemplo, para um sumarizador de transcrições de reuniões, você pode ter prompts separados para: (1) extrair decisões, itens de ação e responsáveis; (2) verificar a consistência dos detalhes extraídos; (3) gerar um resumo conciso a partir dos detalhes estruturados. Prompts pequenos e focados são mais fáceis de entender, testar e iterar individualmente.

## Otimize seus tokens de contexto

<figure>
<img src="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/769d9328cc-1000.webp" srcset="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/769d9328cc-728.webp 728w, /blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/769d9328cc-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="571" alt="token optimization" loading="lazy" decoding="async">
</figure>

Avalie cuidadosamente quanta informação de contexto é realmente necessária para a tarefa em questão. Remova material supérfluo (docs, pdfs, imagens, fotos, etc) para manter o prompt enxuto e eficiente. Considere a estrutura do contexto fornecido. Uma representação de "*bag-of-docs*" pode não ser tão útil quanto uma estrutura que destaque as relações entre as partes do contexto e facilite a extração de informações relevantes. Leia atentamente o prompt final enviado ao modelo, colocando-o em uma página em branco. Isso pode ajudar a identificar redundâncias, linguagem autocontraditória ou formatação inadequada.

## Faça experimentos e itere

<figure>
<img src="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/6578c9d00b-1000.webp" srcset="/blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/6578c9d00b-728.webp 728w, /blog/img/melhores-praticas-para-uso-do-chatgpt-e-outros-llms/6578c9d00b-1000.webp 1000w" sizes="(max-width: 760px) 100vw, 720px" width="1000" height="571" alt="do experiments and iterations" loading="lazy" decoding="async">
</figure>

O desenvolvimento de prompts eficazes é um processo iterativo. Não tenha medo de experimentar diferentes abordagens e fazer ajustes com base nos resultados obtidos, em uma nova janela de diálogo com o LLM (ou seja, comece de novo, incluindo ou removendo informações ajustando conforme o resultado que você espera). Teste seu prompt com uma variedade de entradas possíveis e analise a qualidade das respostas geradas. Identifique padrões de erros ou áreas que precisam de aprimoramento. Se possível, solicite *feedback* de usuários ou especialistas no domínio (aqueles que entendem bem do assunto) para obter insights valiosos sobre a eficácia de seus prompts e identificar oportunidades de melhoria.

E aproveito o ensejo para colocar mais duas dicas, agora para **desenvolvedores que criam aplicações baseando-se em Inteligência Artificial**:

- **Monitore e ajuste continuamente:** Acompanhe regularmente o desempenho de seus prompts em um ambiente de produção. Colete métricas relevantes, como taxas de satisfação do usuário, precisão das respostas e eficiência computacional. Esteja preparado para fazer ajustes e atualizações nos prompts à medida que novos dados se tornam disponíveis ou as necessidades dos usuários evoluem ao longo do tempo. Mantenha um registro das alterações feitas nos prompts e documente as lógicas por trás dessas modificações. Isso facilita a colaboração com a equipe e a resolução de problemas futuros.
- **Estruture seus inputs e outputs:** Use formatação e estruturação adequadas para os inputs, como XML, JSON ou Markdown (dependendo das preferências do modelo). Isso ajuda o LLM a entender melhor as relações entre os tokens no contexto e a associar a solicitação a exemplos semelhantes em seus dados de treinamento. Para os outputs, especifique o formato desejado, como um parágrafo de texto, uma lista com marcadores, uma tabela, etc. Isso facilita a integração das respostas do modelo em componentes *downstream* de seu sistema. Ferramentas como o Instructor (para SDKs de API de LLM) ou o Outlines (para modelos self-hosted do Huggingface) podem ser úteis para estruturar os outputs de forma eficiente.

Ao seguir essas orientações e melhores práticas, você pode criar prompts eficazes que aproveitem ao máximo as capacidades dos LLMs. Lembre-se de que a elaboração de prompts é uma habilidade que se aprimora com a prática e a experimentação constantes. Esteja aberto a aprender com cada interação e a adaptar suas estratégias conforme necessário para obter resultados ideais.
