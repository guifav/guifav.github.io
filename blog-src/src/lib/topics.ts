// Temas do blog (#104): páginas /blog/tema/<id>/ e o bloco "Leia também" dos posts.
// Funções puras, testadas com node --test (tests/topics.test.ts). Os ids e o campo topics do frontmatter
// estão em schema.ts. Os textos que aparecem aqui saem em nome do Gui e passam pela aprovação dele no PR.
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

export interface Topic {
  id: TopicId;
  /** Nome do tema: h1 da página e texto do link no bloco. */
  name: string;
  /** Nome na migalha do post e na linha de temas da lista do blog (mais curto só quando o nome quebra no celular). */
  short: string;
  /** Meta description da página do tema, de 120 a 160 caracteres. */
  description: string;
  /** Parágrafo de abertura da página. */
  intro: string;
  /** Chamada da conversão na página do tema: frase do topo, repetida no quadro do fim. */
  pageCta: CtaPart[];
  /** Frase que só o quadro do fim da página acrescenta. */
  pageCtaExtra?: CtaPart[];
  /** Chamada no bloco "Leia também"; null quando a conversão é a inscrição, que o post já tem logo abaixo. */
  blockCta: CtaPart[] | null;
  /** Slugs dos posts na ordem de leitura. Precisa ser exatamente o conjunto dos posts indexáveis do tema. */
  order: string[];
}

const MAPA = '/mapa-decisao-ia/';
const PLANILHA = '/planilha-custos-ia/';
const ASSINAR = '/blog/assinar/';

export const TOPICS: Topic[] = [
  {
    id: 'ia-sobre-dados-da-empresa',
    name: 'IA sobre os dados da empresa',
    short: 'IA sobre os dados da empresa',
    description:
      'Como fazer a IA responder com os dados da empresa: RAG, Text-to-SQL, MCP e análise de planilhas com LLMs, nos artigos de Guilherme Favaron.',
    intro:
      'Quase todo projeto de IA que acompanho precisa que o modelo responda com os dados da própria empresa, com cada número conferido na fonte. Reuni aqui o que escrevi sobre os caminhos para isso (RAG, Text-to-SQL, MCP e análise de planilhas com LLMs) e sobre quando cada um compensa. Os primeiros artigos tratam da decisão de arquitetura; os últimos, de casos que construí.',
    pageCta: [
      'Antes de escolher a arquitetura, use o ',
      { text: 'mapa de decisão', href: MAPA },
      ': as perguntas que eu faço, a matriz de RAG, Text-to-SQL e contexto longo e a conta do custo por pergunta.',
    ],
    pageCtaExtra: ['Para ver isso funcionando em produto, ', { text: 'conheça o TalkWithData', href: '/portfolio/#talkwithdata' }, '.'],
    blockCta: ['Vai escolher a arquitetura? Use o ', { text: 'mapa de decisão de IA', href: MAPA }, '.'],
    order: [
      'o-ativo-invisivel-dados-como-vantagem-competitiva',
      'mcp-ou-aplicacao-dados-da-empresa-na-ia',
      'rag-llm-ai-o-que-e',
      'o-que-e-rag-llms-ia',
      'guia-praticos-implementacao-rag-llms-ai',
      'rag-knowledge-bases-corporativas-implementacao-pratica',
      'llm-sql-nosql-guia-integracao',
      'ferramentas-ia-excel-google-sheets-analise-dados',
      'ia-google-sheets-integracao-llm-codigo-aberto',
      'tutorial-analise-de-dados-colab-python-ia',
      'como-criar-um-modelo-de-machine-learning',
      'analise-preditiva-tomada-decisao-producao',
      'machine-learning-producao-modelos-empresariais-roi',
      'customer-analytics-e-segmentacao',
      'analise-vendas-ia-guia-completo',
      'radar-inteligencia-mercado-open-source',
    ],
  },
  {
    id: 'custos-roi-e-escolha-de-modelo',
    name: 'Custos, ROI e escolha de modelo',
    short: 'Custos, ROI e escolha de modelo',
    description:
      'Quanto custa a IA generativa em produção e como escolher o modelo: fronteira, aberto, fine-tuning e GPU própria, nos artigos de Guilherme Favaron.',
    intro:
      'Escolher o modelo é uma decisão de custo antes de ser uma decisão técnica, e a conta muda a cada lançamento. Reuni aqui o que escrevi sobre quanto custa colocar IA generativa em produção, quando um modelo aberto ou especializado resolve melhor que o de fronteira e como dimensionar a GPU quando a opção é rodar o próprio modelo.',
    pageCta: [
      'A ',
      { text: 'planilha de custos de IA', href: PLANILHA },
      ' refaz a conta com as suas premissas: API, assinatura por usuário, GPU própria ou plataforma pronta.',
    ],
    blockCta: ['Faça a conta do seu caso na ', { text: 'planilha de custos de IA', href: PLANILHA }, '.'],
    order: [
      'o-fim-do-tokenmaxxing-por-que-nao-usar-modelo-de-fronteira',
      'ia-generativa-em-producao-custos',
      'openai-vs-open-source-decisoes-estrategicas-ctos',
      'fine-tuning-vs-prompt-engineering',
      'o-alpha-estava-no-arquivo',
      'claude-o-guia-definitivo-ia-consultoria',
      'calculo-memoria-gpu-llm',
    ],
  },
  {
    id: 'agentes-de-ia-em-producao',
    name: 'Agentes de IA em produção',
    short: 'Agentes de IA em produção',
    description:
      'Agentes de IA em produção na empresa: arquitetura, MCP, LangChain, LangGraph e memória, com os casos e os erros de Guilherme Favaron.',
    intro:
      'Um agente de IA que funciona na demonstração ainda está longe de funcionar na operação de uma empresa. Aqui estão os artigos sobre arquitetura de agentes, o protocolo MCP, orquestração com LangChain e LangGraph, memória, o que aprendi rodando agentes no meu próprio trabalho e o incidente com os agentes internos da OpenAI.',
    pageCta: [
      'Os próximos artigos sobre agentes em produção chegam por email. ',
      { text: 'Assine os Expert Insights', href: ASSINAR },
      '.',
    ],
    blockCta: null,
    order: [
      'agentes-ia-por-onde-comecar',
      'como-criar-agentes-ia-guia-passo-a-passo',
      'arquitetura-sistemas-agentes-ia',
      'mcp-model-context-protocol-revolucao-inteligencia-artificial',
      'langchain-arquitetura-empresarial-producao',
      'langgraph-orquestrando-workflows',
      'memoria-agentes-solucao-definitiva',
      'a-memoria-dos-meus-agentes-na-pratica',
      'agente-ia-clawdbot-moltbot-configuracao-erros-acertos',
      'hermes-agente-nous-research-origem',
      'o-exame-virou-conspiracao-o-incidente',
    ],
  },
  {
    id: 'implantacao-de-ia-legados-e-arquitetura',
    name: 'Implantação de IA: sistemas legados e arquitetura de dados',
    short: 'Implantação de IA',
    description:
      'Como implantar IA numa empresa com sistemas legados: plano, time, integração e arquitetura de dados, nos artigos de Guilherme Favaron.',
    intro:
      'A maior parte das empresas que querem usar IA tem anos de sistemas legados e dados espalhados entre eles, e reescrever tudo raramente cabe no prazo ou no orçamento. Neste tema estão o framework ATLAS para planejar a implantação, a montagem do time, a integração com os sistemas que já existem e as decisões de arquitetura de dados que vêm antes do modelo: pipelines, governança, cloud, microsserviços e APIs.',
    pageCta: [
      'Sua empresa está decidindo como implantar IA sobre sistemas legados? ',
      { text: 'Agende um horário', href: SCHEDULE_URL },
      ' e conversamos sobre o seu caso.',
    ],
    blockCta: ['Implantando IA sobre sistema legado? ', { text: 'Agende um horário', href: SCHEDULE_URL }, ' para falar do seu caso.'],
    order: [
      'framework-atlas-para-implementacao-de-ia-nos-processos-corporativos',
      'a-arquitetura-definitiva-de-ia-para',
      'curso-ia-negocios-gratuito-youtube',
      'construindo-times-de-ia-lideranca',
      'modernizacao-de-sistemas-legados',
      'como-integrar-ia-nos-seus-sistemas-legados',
      'arquitetura-consolidacao-dados-ia-sem-reescrever-sistemas-legados',
      'arquitetura-dados-analytics-ai',
      'data-pipelines-empresariais-como',
      'streaming-data-em-escala-arquiteturas',
      'data-governance-custo-dados-nao-confiaveis',
      'microservices-vs-monolith-decisoes',
      'api-first-architecture-quando-faz-sentido',
      'guia-migracao-cloud-modelos-public-private-hybrid-de-migracao-para-cloud',
      'seguranca-arquitetura-cyber-espionage-ia-gtg-1002',
    ],
  },
  {
    id: 'engenharia-de-software-com-ia',
    name: 'Engenharia de software com IA',
    short: 'Engenharia de software com IA',
    description:
      'Engenharia de software com IA: o novo perfil do desenvolvedor, a IA no editor e na automação e o pipeline de deploy, nos artigos de Guilherme Favaron.',
    intro:
      'Com a IA escrevendo boa parte do código, o trabalho do time de engenharia passa a ser decidir o que construir e verificar o que foi gerado. Reuni aqui o que escrevi sobre o novo perfil do desenvolvedor, o fluxo com IA no editor e na automação, o pipeline de deploy e um padrão para aplicações que aprendem com o uso.',
    pageCta: [
      'Os próximos artigos sobre engenharia de software com IA chegam por email. ',
      { text: 'Assine os Expert Insights', href: ASSINAR },
      '.',
    ],
    blockCta: null,
    order: [
      'o-fim-do-codigo-como-ativo-o-novo',
      'a-era-dos-prototipos-acabou-como-a-ia-esta-revolucionando-o-desenvolvimento-de-software',
      'desenvolvimento-moderno-ia-criar-aplicacoes-zero-deploy',
      'integrar-vscode-cursor-n8n-automacao-workflows',
      'devops-e-deployment-pipeline-design',
      'self-improvement-architecture-aplicacoes-que-aprendem-com-uso-real',
      'candy-crush-ia',
    ],
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
  if (!t) throw new Error(`tema desconhecido: ${id}`);
  return t;
}

/** Post que recebe o bloco "Leia também" e entra nos relacionados: indexável e fora dos podcasts. */
export function isListed(p: TopicPost): boolean {
  return p.data.index && p.data.type !== 'podcast';
}

/**
 * Confere os temas contra os posts e devolve os problemas (vazio quando está tudo certo). O build falha com
 * qualquer um: tema da lista TOPICS faltando ou repetido, post com tema fora do índice, ordem de leitura que não
 * bate com os posts do tema e tema com menos de MIN_TOPIC_POSTS posts indexáveis.
 */
export function topicProblems<P extends TopicPost>(posts: P[], topics: Topic[] = TOPICS): string[] {
  const problems: string[] = [];
  const ids = topics.map((t) => t.id);
  for (const id of TOPIC_IDS) if (!ids.includes(id)) problems.push(`tema ${id} sem definição em TOPICS`);
  if (new Set(ids).size !== ids.length) problems.push('tema repetido em TOPICS');
  for (const p of posts) {
    if (p.data.topics?.length && !isListed(p)) problems.push(`${p.id}: post fora do índice ou podcast com topics`);
  }
  for (const t of topics) {
    const tagged = posts.filter((p) => isListed(p) && p.data.topics?.includes(t.id)).map((p) => p.id);
    const order = new Set(t.order);
    if (order.size !== t.order.length) problems.push(`tema ${t.id}: post repetido na ordem de leitura`);
    for (const s of tagged) if (!order.has(s)) problems.push(`tema ${t.id}: ${s} tem o tema mas falta na ordem de leitura`);
    for (const s of order) if (!tagged.includes(s)) problems.push(`tema ${t.id}: ${s} está na ordem de leitura sem ter o tema`);
    if (tagged.length < MIN_TOPIC_POSTS) {
      problems.push(`tema ${t.id}: ${tagged.length} posts indexáveis, o mínimo para ter página é ${MIN_TOPIC_POSTS}`);
    }
  }
  return problems;
}

/** Os posts do tema na ordem de leitura. Chamar depois de topicProblems sem problemas. */
export function topicPosts<P extends TopicPost>(posts: P[], topic: Topic): P[] {
  const byId = new Map(posts.map((p) => [p.id, p]));
  return topic.order.map((s) => {
    const p = byId.get(s);
    if (!p) throw new Error(`tema ${topic.id}: post ${s} não existe`);
    return p;
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
    const list = topicPosts(posts, topicById(main, topics));
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
