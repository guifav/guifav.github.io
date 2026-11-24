// Temas do blog (#104): páginas /blog/tema/<id>/ e o bloco "Leia também" dos posts.
// Funções puras, testadas com node --test (tests/topics.test.ts). Os ids e o campo topics do frontmatter
// estão em schema.ts e ficam congelados depois do merge do PR da #104: o id não acompanha renomeação de título.
// Os textos que aparecem aqui saem em nome do Gui e passam pela aprovação dele no PR.
//
// Contra a canibalização com os posts (avaliação do agy na #104, 24/11): a página de tema é coleção, com o título
// "Artigos sobre ...", abertura curta sobre o problema e a ordem de leitura, e uma pergunta por post. Do post para o
// tema, só links estruturais: a migalha e o título do bloco ("Mais artigos sobre ...").
import { TOPIC_IDS, type TopicId } from './schema.ts';

// Um tema só ganha página com pelo menos este número de posts indexáveis (#104).
export const MIN_TOPIC_POSTS = 5;
// Quantos posts o bloco "Leia também" mostra.
export const RELATED_COUNT = 3;
// O link de agendamento da conversa, o mesmo da seção Consultoria da home.
export const SCHEDULE_URL = 'https://calendar.app.google/VyG67Nq5p6zsstAt8';
// data-cta-location do link de agendamento (lista fechada no check_site.py; Fable 5.2, PR da #104): a frase do topo
// da página de tema é a "hero"; o quadro do fim da página e a linha do bloco no post são a "chamada-final".
export const CTA_LOCATION_TOP = 'hero';
export const CTA_LOCATION_END = 'chamada-final';

/** Trecho de texto da chamada: texto corrido ou um link. */
export type CtaPart = string | { text: string; href: string };

/** Um post do tema: o slug e a pergunta que ele responde, mostrada na página do tema. */
export interface TopicEntry {
  slug: string;
  question: string;
}

export interface Topic {
  id: TopicId;
  /** Nome do tema: h1 e último item da migalha da página. */
  name: string;
  /** Complemento de "Artigos sobre" no <title> da página e de "Mais artigos sobre" no título do bloco. */
  about: string;
  /** Nome na migalha do post e na linha de temas da lista do blog (mais curto só quando o nome quebra no celular). */
  short: string;
  /** Meta description da página do tema, de 120 a 160 caracteres. */
  description: string;
  /** Parágrafo de abertura da página, de 60 a 110 palavras: o problema e como ler a sequência. */
  intro: string;
  /** Chamada da conversão na página do tema: frase do topo, repetida no quadro do fim. */
  pageCta: CtaPart[];
  /** Frase que só o quadro do fim da página acrescenta. */
  pageCtaExtra?: CtaPart[];
  /** Chamada no bloco "Leia também"; null quando a conversão é a inscrição, que o post já tem logo abaixo. */
  blockCta: CtaPart[] | null;
  /** Os posts na ordem de leitura, os pilares primeiro. Precisa ser exatamente o conjunto dos posts indexáveis do tema. */
  posts: TopicEntry[];
}

/** Tema do schema que ainda não tem 5 posts fortes: sem página, e nenhum post pode usá-lo em topics. */
export interface WaitingTopic {
  id: TopicId;
  name: string;
  /** Por que ainda não tem página. */
  reason: string;
}

const MAPA = '/mapa-decisao-ia/';
const PLANILHA = '/planilha-custos-ia/';
const ASSINAR = '/blog/assinar/';

export const TOPICS: Topic[] = [
  {
    id: 'ia-sobre-dados-da-empresa',
    name: 'IA sobre os dados da empresa',
    about: 'IA e os dados da empresa',
    short: 'IA sobre os dados da empresa',
    description:
      'Artigos de Guilherme Favaron sobre como fazer a IA responder com os dados da empresa: RAG, Text-to-SQL, MCP e análise de planilhas com LLMs.',
    intro:
      'Quase todo projeto de IA que acompanho precisa que o modelo responda com os dados da própria empresa, com cada número conferido na fonte. Os caminhos são vários (RAG sobre documentos, Text-to-SQL sobre o banco, MCP e análise de planilhas), e cada um acerta num tipo de pergunta e falha em outro. Comece pelos seis primeiros artigos, que tratam da escolha de arquitetura e de cada caminho. Os seguintes descem para planilhas e modelos preditivos, e a lista termina nos casos que construí.',
    pageCta: [
      'Antes de escolher a arquitetura, use o ',
      { text: 'mapa de decisão', href: MAPA },
      ': as perguntas que eu faço, a matriz de RAG, Text-to-SQL e contexto longo e a conta do custo por pergunta.',
    ],
    pageCtaExtra: ['Para ver isso funcionando em produto, ', { text: 'conheça o TalkWithData', href: '/portfolio/#talkwithdata' }, '.'],
    blockCta: ['Vai escolher a arquitetura? Use o ', { text: 'mapa de decisão de IA', href: MAPA }, '.'],
    posts: [
      { slug: 'mcp-ou-aplicacao-dados-da-empresa-na-ia', question: 'Servidor MCP aberto ou aplicação própria: quem garante a resposta da IA sobre os dados da empresa?' },
      { slug: 'o-que-e-rag-llms-ia', question: 'O que é RAG e quando ele resolve a pergunta sobre os documentos da empresa?' },
      { slug: 'guia-praticos-implementacao-rag-llms-ai', question: 'Como implementar RAG na prática, a segunda parte da série?' },
      { slug: 'rag-knowledge-bases-corporativas-implementacao-pratica', question: 'Como montar RAG sobre a base de conhecimento corporativa?' },
      { slug: 'llm-sql-nosql-guia-integracao', question: 'Como ligar um LLM aos bancos SQL e NoSQL da empresa?' },
      { slug: 'ferramentas-ia-excel-google-sheets-analise-dados', question: 'Quais ferramentas de IA analisam planilhas do Excel e do Google Sheets?' },
      { slug: 'ia-google-sheets-integracao-llm-codigo-aberto', question: 'Como levar um LLM para dentro da planilha que o time já usa?' },
      { slug: 'tutorial-analise-de-dados-colab-python-ia', question: 'Como um gestor que não programa analisa dados com IA?' },
      { slug: 'como-criar-um-modelo-de-machine-learning', question: 'Como sair de uma planilha bruta para um modelo preditivo?' },
      { slug: 'analise-preditiva-tomada-decisao-producao', question: 'Como usar análise preditiva nas decisões, com a incerteza medida?' },
      { slug: 'machine-learning-producao-modelos-empresariais-roi', question: 'Como levar um modelo de machine learning do experimento para a produção?' },
      { slug: 'customer-analytics-e-segmentacao', question: 'Como segmentar clientes com os dados que a empresa já tem?' },
      { slug: 'analise-vendas-ia-guia-completo', question: 'Como analisar as ligações do time de vendas com IA?' },
      { slug: 'radar-inteligencia-mercado-open-source', question: 'Como cruzar fontes públicas com o CRM usando um LLM?' },
      { slug: 'o-ativo-invisivel-dados-como-vantagem-competitiva', question: 'Quanto valem os dados de uma empresa, segundo 116 mil relatórios anuais?' },
      { slug: 'rag-llm-ai-o-que-e', question: 'Quais são os primeiros passos para implementar RAG?' },
    ],
  },
  {
    id: 'custos-e-modelos-de-ia',
    name: 'Custos, ROI e escolha de modelo',
    about: 'custos, ROI e escolha de modelo',
    short: 'Custos, ROI e escolha de modelo',
    description:
      'Artigos de Guilherme Favaron sobre quanto custa a IA generativa em produção e como escolher o modelo: fronteira, aberto, fine-tuning e GPU própria.',
    intro:
      'Escolher o modelo é uma decisão de custo antes de ser uma decisão técnica, e a conta muda a cada lançamento. Os quatro primeiros artigos tratam do custo de manter IA generativa em produção, da escolha entre fine-tuning e prompt, do uso do Claude na empresa e do dimensionamento de GPU para quem roda o próprio modelo. Os três últimos discutem quando o modelo de fronteira deixa de compensar e um modelo aberto ou especializado resolve melhor.',
    pageCta: [
      'A ',
      { text: 'planilha de custos de IA', href: PLANILHA },
      ' refaz a conta com as suas premissas: API, assinatura por usuário, GPU própria ou plataforma pronta.',
    ],
    blockCta: ['Faça a conta do seu caso na ', { text: 'planilha de custos de IA', href: PLANILHA }, '.'],
    posts: [
      { slug: 'ia-generativa-em-producao-custos', question: 'Quanto custa manter IA generativa em produção, com performance e escala?' },
      { slug: 'fine-tuning-vs-prompt-engineering', question: 'Quando investir em fine-tuning e quando o prompt basta?' },
      { slug: 'claude-o-guia-definitivo-ia-consultoria', question: 'Como usar o Claude na prática, inclusive na empresa?' },
      { slug: 'calculo-memoria-gpu-llm', question: 'Quanta memória de GPU um LLM precisa para rodar?' },
      { slug: 'o-fim-do-tokenmaxxing-por-que-nao-usar-modelo-de-fronteira', question: 'Por que usar o modelo de fronteira para tudo deixou de compensar?' },
      { slug: 'openai-vs-open-source-decisoes-estrategicas-ctos', question: 'Quando um modelo aberto substitui a API da OpenAI?' },
      { slug: 'o-alpha-estava-no-arquivo', question: 'Quando um modelo especializado supera os de fronteira?' },
    ],
  },
  {
    id: 'agentes-de-ia',
    name: 'Agentes de IA em produção',
    about: 'agentes de IA em produção',
    short: 'Agentes de IA em produção',
    description:
      'Artigos de Guilherme Favaron sobre agentes de IA em produção na empresa: como criar, arquitetura, MCP, LangChain, LangGraph e memória.',
    intro:
      'Um agente de IA que funciona na demonstração ainda está longe de funcionar na operação de uma empresa. Os três primeiros artigos tratam de como criar um agente, de como encaixá-lo na arquitetura de sistemas e do protocolo MCP, que liga o agente às ferramentas. Depois vêm a orquestração com LangChain e LangGraph e a memória, que é onde muitos agentes quebram. A lista termina no que aprendi rodando agentes no meu trabalho e no incidente com os agentes internos da OpenAI.',
    pageCta: [
      'Os próximos artigos sobre agentes em produção chegam por email. ',
      { text: 'Assine os Expert Insights', href: ASSINAR },
      '.',
    ],
    blockCta: null,
    posts: [
      { slug: 'como-criar-agentes-ia-guia-passo-a-passo', question: 'Como criar um agente de IA, passo a passo?' },
      { slug: 'arquitetura-sistemas-agentes-ia', question: 'Como tratar agentes como componentes na arquitetura de sistemas da empresa?' },
      { slug: 'mcp-model-context-protocol-revolucao-inteligencia-artificial', question: 'O que é o Model Context Protocol e o que ele muda para os agentes?' },
      { slug: 'mcp-ou-aplicacao-dados-da-empresa-na-ia', question: 'Quando o agente deve chegar aos dados por MCP e quando por uma aplicação?' },
      { slug: 'agentes-ia-por-onde-comecar', question: 'Por onde começar com agentes de IA?' },
      { slug: 'langchain-arquitetura-empresarial-producao', question: 'Como levar uma aplicação com LangChain da POC para a produção?' },
      { slug: 'langgraph-orquestrando-workflows', question: 'Como orquestrar fluxos complexos de agentes com LangGraph?' },
      { slug: 'memoria-agentes-solucao-definitiva', question: 'Por que a memória dos agentes quebra, e como consertar?' },
      { slug: 'a-memoria-dos-meus-agentes-na-pratica', question: 'Como funciona, na prática, a memória dos agentes que uso?' },
      { slug: 'agente-ia-clawdbot-moltbot-configuracao-erros-acertos', question: 'O que aprendi rodando um agente na minha máquina principal?' },
      { slug: 'hermes-agente-nous-research-origem', question: 'Por que a gestão de contexto sustenta um agente como o Hermes?' },
      { slug: 'o-exame-virou-conspiracao-o-incidente', question: 'O que o incidente com os agentes internos da OpenAI ensina sobre controle?' },
    ],
  },
  {
    id: 'implantacao-de-ia',
    name: 'Implantação de IA: sistemas legados e arquitetura de dados',
    about: 'implementação de IA em sistemas legados',
    short: 'Implantação de IA',
    description:
      'Artigos de Guilherme Favaron sobre como implementar IA numa empresa com sistemas legados: plano, integração, time e arquitetura de dados.',
    intro:
      'A maior parte das empresas que querem implementar IA tem anos de sistemas legados e dados espalhados entre eles, e reescrever tudo raramente cabe no prazo ou no orçamento. Os quatro primeiros artigos tratam do plano de implementação com o framework ATLAS e da integração da IA aos sistemas que já existem. Em seguida vêm a arquitetura de IA, a implementação em pequena e média empresa e a montagem do time. A segunda metade cobre as decisões de arquitetura de dados que vêm antes do modelo: pipelines, governança, cloud, microsserviços e APIs.',
    pageCta: [
      'Sua empresa está decidindo como implementar IA sobre sistemas legados? ',
      { text: 'Agende um horário', href: SCHEDULE_URL },
      ' e conversamos sobre o seu caso.',
    ],
    blockCta: ['Implementando IA sobre sistema legado? ', { text: 'Agende um horário', href: SCHEDULE_URL }, ' para falar do seu caso.'],
    posts: [
      { slug: 'framework-atlas-para-implementacao-de-ia-nos-processos-corporativos', question: 'Como planejar a implementação de IA nos processos com o framework ATLAS?' },
      { slug: 'modernizacao-de-sistemas-legados', question: 'Que estratégias de modernização de legados funcionaram em casos reais?' },
      { slug: 'como-integrar-ia-nos-seus-sistemas-legados', question: 'Como integrar IA aos sistemas legados, num workflow real?' },
      { slug: 'arquitetura-consolidacao-dados-ia-sem-reescrever-sistemas-legados', question: 'Como preparar dados espalhados para IA sem reescrever os sistemas legados?' },
      { slug: 'a-arquitetura-definitiva-de-ia-para', question: 'Por que a maioria dos projetos de IA falha, e que arquitetura evita isso?' },
      { slug: 'curso-ia-negocios-gratuito-youtube', question: 'Como uma pequena ou média empresa começa a implementar IA?' },
      { slug: 'construindo-times-de-ia-lideranca', question: 'Como montar e liderar o time que implementa IA?' },
      { slug: 'arquitetura-dados-analytics-ai', question: 'Que arquitetura de dados sustenta analytics e IA?' },
      { slug: 'data-pipelines-empresariais-como', question: 'Como transformar silos de dados em pipelines confiáveis?' },
      { slug: 'streaming-data-em-escala-arquiteturas', question: 'Quando a empresa precisa de dados em streaming?' },
      { slug: 'data-governance-custo-dados-nao-confiaveis', question: 'Quanto custa o dado não confiável, e como governar a qualidade?' },
      { slug: 'microservices-vs-monolith-decisoes', question: 'Microsserviços ou monólito: qual arquitetura escolher?' },
      { slug: 'api-first-architecture-quando-faz-sentido', question: 'Quando a arquitetura API-first compensa?' },
      { slug: 'guia-migracao-cloud-modelos-public-private-hybrid-de-migracao-para-cloud', question: 'Cloud pública, privada ou híbrida: como planejar a migração?' },
      { slug: 'seguranca-arquitetura-cyber-espionage-ia-gtg-1002', question: 'O que a primeira campanha de espionagem orquestrada por IA muda na segurança da arquitetura?' },
    ],
  },
];

export const WAITING_TOPICS: WaitingTopic[] = [
  {
    id: 'engenharia-de-software-com-ia',
    name: 'Engenharia de software com IA',
    reason:
      'só 3 posts fortes (o-fim-do-codigo-como-ativo-o-novo, self-improvement-architecture-aplicacoes-que-aprendem-com-uso-real e integrar-vscode-cursor-n8n-automacao-workflows); a regra da #104 pede 5 sem contar post curto de 2024 nem de fronteira',
  },
];

/** O mínimo de um post que as funções de tema usam (o Post do Astro tem esse formato). */
export interface TopicPost {
  id: string;
  data: { date: Date; index: boolean; type: 'newsletter' | 'podcast'; topics?: TopicId[] };
}

export function topicPath(base: string, id: TopicId): string {
  return `${base.replace(/\/?$/, '/')}tema/${id}/`;
}

export function topicById(id: TopicId, topics: Topic[] = TOPICS): Topic {
  const t = topics.find((x) => x.id === id);
  if (!t) throw new Error(`tema sem página: ${id}`);
  return t;
}

/** Post que recebe o bloco "Leia também" e entra nos relacionados: indexável e fora dos podcasts. */
export function isListed(p: TopicPost): boolean {
  return p.data.index && p.data.type !== 'podcast';
}

/**
 * Confere os temas contra os posts e devolve os problemas (vazio quando está tudo certo). O build falha com
 * qualquer um: tema do schema sem definição ou definido duas vezes, post com tema em espera, post fora do índice ou
 * podcast com tema, ordem de leitura que não bate com os posts do tema e tema com menos de MIN_TOPIC_POSTS posts.
 */
export function topicProblems<P extends TopicPost>(
  posts: P[],
  topics: Topic[] = TOPICS,
  waiting: WaitingTopic[] = WAITING_TOPICS,
): string[] {
  const problems: string[] = [];
  const ids = [...topics.map((t) => t.id), ...waiting.map((t) => t.id)];
  for (const id of TOPIC_IDS) if (!ids.includes(id)) problems.push(`tema ${id} sem definição em TOPICS nem em WAITING_TOPICS`);
  if (new Set(ids).size !== ids.length) problems.push('tema repetido em TOPICS e WAITING_TOPICS');
  for (const p of posts) {
    if (p.data.topics?.length && !isListed(p)) problems.push(`${p.id}: post fora do índice ou podcast com topics`);
    for (const w of waiting) {
      if (p.data.topics?.includes(w.id)) problems.push(`${p.id}: tema ${w.id} ainda sem página (${w.reason})`);
    }
  }
  for (const t of topics) {
    const tagged = posts.filter((p) => isListed(p) && p.data.topics?.includes(t.id)).map((p) => p.id);
    const slugs = t.posts.map((e) => e.slug);
    const order = new Set(slugs);
    if (order.size !== slugs.length) problems.push(`tema ${t.id}: post repetido na ordem de leitura`);
    for (const s of tagged) if (!order.has(s)) problems.push(`tema ${t.id}: ${s} tem o tema mas falta na ordem de leitura`);
    for (const s of order) if (!tagged.includes(s)) problems.push(`tema ${t.id}: ${s} está na ordem de leitura sem ter o tema`);
    if (tagged.length < MIN_TOPIC_POSTS) {
      problems.push(`tema ${t.id}: ${tagged.length} posts indexáveis, o mínimo para ter página é ${MIN_TOPIC_POSTS}`);
    }
  }
  return problems;
}

/** Os posts do tema na ordem de leitura, cada um com a pergunta. Chamar depois de topicProblems sem problemas. */
export function topicPosts<P extends TopicPost>(posts: P[], topic: Topic): { post: P; question: string }[] {
  const byId = new Map(posts.map((p) => [p.id, p]));
  return topic.posts.map(({ slug, question }) => {
    const post = byId.get(slug);
    if (!post) throw new Error(`tema ${topic.id}: post ${slug} não existe`);
    return { post, question };
  });
}

/**
 * Os posts do bloco "Leia também". Com tema, os seguintes na ordem de leitura do tema principal (o primeiro de
 * topics), voltando ao começo. Sem tema, os indexáveis mais próximos por data (empate: o mais recente, depois o
 * slug). Post fora do índice ou podcast não tem bloco: devolve [].
 */
export function relatedPosts<P extends TopicPost>(post: P, posts: P[], topics: Topic[] = TOPICS, n = RELATED_COUNT): P[] {
  if (!isListed(post)) return [];
  const main = post.data.topics?.[0];
  if (main) {
    const list = topicPosts(posts, topicById(main, topics)).map((e) => e.post);
    const i = list.findIndex((p) => p.id === post.id);
    const out: P[] = [];
    for (let k = 1; k < list.length && out.length < n; k++) out.push(list[(i + k) % list.length]);
    return out;
  }
  const t = post.data.date.getTime();
  return posts
    .filter((p) => p.id !== post.id && isListed(p))
    .sort(
      (a, b) =>
        Math.abs(a.data.date.getTime() - t) - Math.abs(b.data.date.getTime() - t) ||
        b.data.date.getTime() - a.data.date.getTime() ||
        a.id.localeCompare(b.id),
    )
    .slice(0, n);
}
