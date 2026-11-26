// Materiais do guifav.dev entregues por email (POST /material e GET /material/download).
// Só o catálogo: arquivos pequenos entram no bundle pelo src/worker.ts; arquivos grandes vêm do R2 (r2Key).

export interface Arquivo {
  /** Id do arquivo no link de download. */
  id: string;
  /** Nome com que o arquivo é baixado. */
  nome: string;
  /** Rótulo do botão no email. */
  rotulo: string;
  tipo: string;
  /** Chave no bucket R2 MATERIAIS_R2, quando o arquivo não entra no bundle do Worker. */
  r2Key?: string;
}

/** Tema de interesse que uma ação no site revela. O id vira a opção da multi-seleção no Twenty (fase 2 da issue #97). */
export interface Interesse {
  id: string;
  rotulo: string;
}

/** Catálogo dos interesses. Id em maiúsculas com sublinhado (formato das opções do Twenty); nunca renomeie um id já gravado. */
export const INTERESSES = {
  CUSTO_IA: { id: 'CUSTO_IA', rotulo: 'Custo de IA' },
  ARQUITETURA_IA: { id: 'ARQUITETURA_IA', rotulo: 'Arquitetura de IA (RAG, SQL, contexto longo)' },
  ESTRATEGIA_IA: { id: 'ESTRATEGIA_IA', rotulo: 'Estratégia de IA para empresas' },
  EXPERT_INSIGHTS: { id: 'EXPERT_INSIGHTS', rotulo: 'Expert Insights' },
} as const satisfies Record<string, Interesse>;

export interface Material {
  /** Id do material: valor enviado no campo `material` do formulário. */
  id: string;
  /** Caminho da página do material no site, começando e terminando com "/". */
  pagina: string;
  titulo: string;
  arquivos: Arquivo[];
  /** Temas que o download revela, gravados na conversão. */
  interesses: Interesse[];
}

const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

export const MATERIAIS: Material[] = [
  {
    id: 'planilha-custos-ia',
    pagina: '/ferramentas/planilha-custos-ia/',
    titulo: 'Planilha de custos de IA em produção',
    arquivos: [{ id: 'planilha-custos-ia', nome: 'planilha-custos-ia.xlsx', rotulo: 'Baixar a planilha (.xlsx)', tipo: XLSX }],
    interesses: [INTERESSES.CUSTO_IA],
  },
  {
    id: 'mapa-decisao-ia',
    pagina: '/ferramentas/mapa-decisao-ia/',
    titulo: 'Mapa de decisão: RAG, Text-to-SQL ou contexto longo',
    arquivos: [
      { id: 'mapa-decisao-pdf', nome: 'mapa-decisao-rag-sql-contexto-longo.pdf', rotulo: 'Baixar o mapa (PDF)', tipo: 'application/pdf' },
      { id: 'conta-custo-por-pergunta', nome: 'conta-custo-por-pergunta.xlsx', rotulo: 'Baixar a conta do custo por pergunta (.xlsx)', tipo: XLSX },
    ],
    interesses: [INTERESSES.ARQUITETURA_IA, INTERESSES.CUSTO_IA],
  },
  {
    id: 'livro-desbloqueando-a-inteligencia-artificial',
    pagina: '/materiais/livro-desbloqueando-a-inteligencia-artificial/',
    titulo: 'Desbloqueando a Inteligência Artificial',
    arquivos: [
      {
        id: 'livro-desbloqueando-ia-pdf',
        nome: 'desbloqueando-a-inteligencia-artificial-guilherme-favaron.pdf',
        rotulo: 'Baixar o livro (PDF)',
        tipo: 'application/pdf',
        r2Key: 'livro/desbloqueando-a-inteligencia-artificial.pdf',
      },
    ],
    interesses: [INTERESSES.ESTRATEGIA_IA],
  },
];

export const material = (id: string): Material | undefined => MATERIAIS.find((m) => m.id === id);

/** O arquivo e o material a que ele pertence. */
export function arquivo(id: string): { material: Material; arquivo: Arquivo } | undefined {
  for (const m of MATERIAIS) {
    const a = m.arquivos.find((x) => x.id === id);
    if (a) return { material: m, arquivo: a };
  }
  return undefined;
}
