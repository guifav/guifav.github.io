// Conversões do site (issues #97 e #99): o pedido de material (email ainda não comprovado), o download e a confirmação
// da inscrição (email comprovado pelo link assinado), gravados no D1 `blog-admin` para a fase 2 levar ao Twenty. Gravação de melhor esforço, depois da resposta (deps.waitUntil): a
// falha vai para o log, sem o email, e nunca atrasa nem impede a entrega do arquivo nem a confirmação da inscrição.
// As funções daqui nunca rejeitam.
import type { Interesse } from './materiais.ts';

export interface D1Stmt {
  bind(...v: unknown[]): D1Stmt;
  run(): Promise<unknown>;
  first<T = Record<string, unknown>>(): Promise<T | null>;
}
export interface D1Like {
  prepare(sql: string): D1Stmt;
}

export type Acao = 'material_request' | 'material_download' | 'newsletter_confirm';

const enc = new TextEncoder();

/** Motivo da falha para o log, sem nada que pareça um email. */
const motivo = (e: unknown) => String((e as Error)?.message ?? e).replace(/[^\s@]+@[^\s@]+/g, '<email>').slice(0, 200);

/**
 * Id da conversão: SHA-256 da ação com a assinatura do link, já verificada e canônica (token.ts recusa as variantes).
 * O mesmo link aberto de novo dá o mesmo id (uma conversão); um pedido novo traz outra validade e, portanto, outra
 * assinatura (outra conversão). Dois pedidos do mesmo endereço no mesmo segundo produzem o mesmo link, com os mesmos
 * bytes, e contam como uma conversão: a conversão é o clique num link. Guardar o hash, e não a assinatura, evita que o
 * D1 tenha o que basta para refazer um link ainda válido.
 */
export async function conversionId(acao: Exclude<Acao, 'material_request'>, assinatura: string): Promise<string> {
  return sha256Hex(`${acao}\n${assinatura}`);
}

/**
 * Id do pedido de material: SHA-256 do material, do email e da validade dos links (segundos Unix), que é a mesma em
 * todos os arquivos do pedido. O download recalcula o mesmo id a partir do link verificado e grava a ligação em
 * request_id. Como no conversionId, dois pedidos do mesmo endereço no mesmo segundo são um pedido só.
 */
export async function requestId(material: string, email: string, exp: number): Promise<string> {
  return sha256Hex(`material_request\n${material}\n${email}\n${exp}`);
}

async function sha256Hex(s: string): Promise<string> {
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', enc.encode(s)));
  return [...h].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export async function recordConversion(
  db: D1Like,
  c: {
    id: string; acao: Acao; email: string; material: string | null; interesses: Interesse[]; nowMs: number;
    /** false só no pedido: o email ainda não foi comprovado por um link aberto. */
    verified: boolean;
    gaClientId?: string | null;
    requestId?: string | null;
  },
): Promise<void> {
  try {
    await db
      .prepare(`INSERT OR IGNORE INTO conversions
        (id, email, action, material, interests, occurred_at, verified, ga_client_id, request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`)
      .bind(c.id, c.email, c.acao, c.material, JSON.stringify(c.interesses), new Date(c.nowMs).toISOString(),
        c.verified ? 1 : 0, c.gaClientId ?? null, c.requestId ?? null)
      .run();
  } catch (e) {
    console.error(`conversão não gravada (${c.acao}): ${motivo(e)}`);
  }
}

/** client_id do GA4 guardado no pedido, ou null (pedido sem consentimento, anterior ao #99 ou D1 com falha). */
export async function requestClientId(db: D1Like, id: string): Promise<string | null> {
  try {
    const r = await db.prepare(`SELECT ga_client_id FROM conversions WHERE id = ? AND action = 'material_request'`)
      .bind(id).first<{ ga_client_id: string | null }>();
    return r?.ga_client_id ?? null;
  } catch (e) {
    console.error(`client_id do pedido não lido: ${motivo(e)}`);
    return null;
  }
}
