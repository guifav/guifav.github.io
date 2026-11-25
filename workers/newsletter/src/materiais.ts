// Materiais dos vídeos do canal @guifav, entregues por email (POST /material e GET /material/download).
// Só o catálogo: os bytes dos arquivos entram pelo src/worker.ts, que os importa como módulos de dados.

export interface Arquivo {
  /** Id do arquivo no link de download. */
  id: string;
  /** Nome com que o arquivo é baixado. */
  nome: string;
  /** Rótulo do botão no email. */
  rotulo: string;
  tipo: string;
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
  EXPERT_INSIGHTS: { id: 'EXPERT_INSIGHTS', rotulo: 'Expert Insights' },
} as const satisfies Record<string, Interesse>;

export interface Material {
  /** Id do material: o campo `material` do formulário e o segmento do caminho em /ferramentas/<id>/ no site. */
  id: string;
  titulo: string;
  arquivos: Arquivo[];
  /** Temas que o download revela, gravados na conversão. */
  interesses: Interesse[];
}

const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

export const MATERIAIS: Material[] = [
  {
    id: 'planilha-custos-ia',
    titulo: 'Planilha de custos de IA em produção',
    arquivos: [{ id: 'planilha-custos-ia', nome: 'planilha-custos-ia.xlsx', rotulo: 'Baixar a planilha (.xlsx)', tipo: XLSX }],
    interesses: [INTERESSES.CUSTO_IA],
  },
  {
    id: 'mapa-decisao-ia',
    titulo: 'Mapa de decisão: RAG, Text-to-SQL ou contexto longo',
    arquivos: [
      { id: 'mapa-decisao-pdf', nome: 'mapa-decisao-rag-sql-contexto-longo.pdf', rotulo: 'Baixar o mapa (PDF)', tipo: 'application/pdf' },
      { id: 'conta-custo-por-pergunta', nome: 'conta-custo-por-pergunta.xlsx', rotulo: 'Baixar a conta do custo por pergunta (.xlsx)', tipo: XLSX },
    ],
    interesses: [INTERESSES.ARQUITETURA_IA, INTERESSES.CUSTO_IA],
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
