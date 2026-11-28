// Conversões do site (issues #97 e #99): o pedido de material (email ainda não comprovado), o download e a confirmação
// da inscrição (email comprovado pelo link assinado), gravados no D1 `blog-admin` para a fase 2 levar ao Twenty. Gravação de melhor esforço, depois da resposta (deps.waitUntil): a
// falha vai para o log, sem o email, e nunca atrasa nem impede a entrega do arquivo nem a confirmação da inscrição.
// As funções daqui nunca rejeitam.
import type { Interesse } from './materiais.ts';
import { CANAIS, FONTES, UTM, entradaValida, type Origem } from './origem.ts';

export interface D1RunResult {
  meta?: { changes?: number };
}
export interface D1Stmt {
  bind(...v: unknown[]): D1Stmt;
  run(): Promise<D1RunResult>;
  first<T = Record<string, unknown>>(): Promise<T | null>;
}
export interface D1Like {
  prepare(sql: string): D1Stmt;
}

export type Acao = 'material_request' | 'material_download' | 'newsletter_confirm' | 'site_login';

const enc = new TextEncoder();

/** Motivo da falha para o log, sem nada que pareça um email. */
export const motivo = (e: unknown) => String((e as Error)?.message ?? e).replace(/[^\s@]+@[^\s@]+/g, '<email>').slice(0, 200);

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
    /** No download: o id do arquivo baixado. */
    file?: string | null;
    /** Origem da visita (issue #114); nula nas conversões de links emitidos antes dela. */
    origem?: Origem | null;
  },
): Promise<void> {
  const o = c.origem ?? null;
  try {
    await db
      // Só a repetição do id (o mesmo link aberto de novo) é ignorada. Um CHECK violado (issue #114) é erro e vai para o
      // log: o OR IGNORE de antes descartaria a linha em silêncio.
      .prepare(`INSERT INTO conversions
        (id, email, action, material, interests, occurred_at, verified, ga_client_id, request_id, file,
         origin_channel, origin_source, utm_source, utm_medium, utm_campaign, landing_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING`)
      .bind(c.id, c.email, c.acao, c.material, JSON.stringify(c.interesses), new Date(c.nowMs).toISOString(),
        c.verified ? 1 : 0, c.gaClientId ?? null, c.requestId ?? null, c.file ?? null,
        o?.canal ?? null, o?.fonte ?? null, o?.utmSource ?? null, o?.utmMedium ?? null, o?.utmCampaign ?? null, o?.entrada ?? null)
      .run();
  } catch (e) {
    console.error(`conversão não gravada (${c.acao}): ${motivo(e)}`);
  }
}

interface PedidoRow {
  ga_client_id: string | null;
  origin_channel: string | null;
  origin_source: string | null;
  utm_source: string | null;
  utm_medium: string | null;
  utm_campaign: string | null;
  landing_path: string | null;
}

/** Origem guardada numa linha, revalidada (o CHECK do D1 já garante o formato; isto é defesa a mais), ou null. */
function origemDaLinha(r: PedidoRow): Origem | null {
  const canal = (CANAIS as readonly string[]).includes(r.origin_channel ?? '') ? (r.origin_channel as Origem['canal']) : null;
  if (!canal) return null;
  const casa = (rx: RegExp, v: string | null) => (v !== null && rx.test(v) ? v : null);
  return {
    canal,
    // Direto e desconhecida não têm fonte, como no parseOrigem e no sync: o CHECK do D1 aceita a combinação, a regra não.
    fonte: canal === 'direto' || canal === 'desconhecida' ? null
      : (FONTES as readonly string[]).includes(r.origin_source ?? '') ? (r.origin_source as Origem['fonte']) : null,
    utmSource: casa(UTM, r.utm_source), utmMedium: casa(UTM, r.utm_medium), utmCampaign: casa(UTM, r.utm_campaign),
    entrada: entradaValida(r.landing_path) ? r.landing_path : null,
  };
}

/**
 * O que o download herda do pedido que gerou o link: o client_id do GA4 (nulo sem consentimento, em pedido anterior ao
 * #99 ou com o D1 em falha) e a origem da visita (issue #114; nula em pedido anterior a ela ou com o D1 em falha).
 */
export async function requestInfo(db: D1Like, id: string): Promise<{ clientId: string | null; origem: Origem | null }> {
  try {
    const r = await db.prepare(`SELECT ga_client_id, origin_channel, origin_source, utm_source, utm_medium, utm_campaign, landing_path
      FROM conversions WHERE id = ? AND action = 'material_request'`).bind(id).first<PedidoRow>();
    return { clientId: r?.ga_client_id ?? null, origem: r ? origemDaLinha(r) : null };
  } catch (e) {
    console.error(`pedido não lido: ${motivo(e)}`);
    return { clientId: null, origem: null };
  }
}
