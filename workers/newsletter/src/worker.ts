// Entrada do Worker no Cloudflare (main do wrangler.toml). Liga o handle aos arquivos dos materiais, importados
// como módulos de dados pela regra [[rules]] do wrangler.toml. Fica fora do index.ts para os testes do Node
// importarem o handle sem os binários.
import { entrypoint } from './index.ts';
import planilha from '../materiais/planilha-custos-ia.xlsx';
import mapaPdf from '../materiais/mapa-decisao-rag-sql-contexto-longo.pdf';
import contaCusto from '../materiais/conta-custo-por-pergunta.xlsx';

const ARQUIVOS: Record<string, ArrayBuffer> = {
  'planilha-custos-ia': planilha,
  'mapa-decisao-pdf': mapaPdf,
  'conta-custo-por-pergunta': contaCusto,
};

export default entrypoint(async (id) => ARQUIVOS[id] ?? null);
