// Conversões do site (issue #97, fase 1): o que a pessoa fez com o email comprovado por um link assinado, gravado no
// D1 `blog-admin` para a fase 2 levar ao Twenty. Gravação de melhor esforço: a falha vai para o log, sem o email, e
// nunca impede a entrega do arquivo nem a confirmação da inscrição.
import type { Interesse } from './materiais.ts';

export interface D1Stmt {
  bind(...v: unknown[]): D1Stmt;
  run(): Promise<unknown>;
}
export interface D1Like {
  prepare(sql: string): D1Stmt;
}

export type Acao = 'material_download' | 'newsletter_confirm';

const enc = new TextEncoder();

/** Motivo da falha para o log, sem nada que pareça um email. */
const motivo = (e: unknown) => String((e as Error)?.message ?? e).replace(/[^\s@]+@[^\s@]+/g, '<email>').slice(0, 200);

/**
 * Id da conversão: SHA-256 da ação com a assinatura do link. O mesmo link aberto de novo dá o mesmo id (uma conversão);
 * um pedido novo traz outra validade e, portanto, outra assinatura (outra conversão). Guardar o hash, e não a
 * assinatura, evita que o D1 tenha o que basta para refazer um link ainda válido.
 */
export async function conversionId(acao: Acao, assinatura: string): Promise<string> {
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', enc.encode(`${acao}\n${assinatura}`)));
  return [...h].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export async function recordConversion(
  db: D1Like,
  c: { acao: Acao; assinatura: string; email: string; material: string; interesses: Interesse[]; nowMs: number },
): Promise<void> {
  try {
    const id = await conversionId(c.acao, c.assinatura);
    await db
      .prepare('INSERT OR IGNORE INTO conversions (id, email, action, material, interests, occurred_at) VALUES (?, ?, ?, ?, ?, ?)')
      .bind(id, c.email, c.acao, c.material, JSON.stringify(c.interesses), new Date(c.nowMs).toISOString())
      .run();
  } catch (e) {
    console.error(`conversão não gravada (${c.acao}): ${motivo(e)}`);
  }
}

/** Soma 1 aos pedidos do material no dia (UTC). Só a contagem: o pedido sem clique não comprova o email. */
export async function countRequest(db: D1Like, material: string, nowMs: number): Promise<void> {
  try {
    await db
      .prepare(`INSERT INTO material_requests (material, day, count) VALUES (?, ?, 1)
        ON CONFLICT(material, day) DO UPDATE SET count = count + 1`)
      .bind(material, new Date(nowMs).toISOString().slice(0, 10))
      .run();
  } catch (e) {
    console.error(`contagem do pedido não gravada: ${motivo(e)}`);
  }
}
