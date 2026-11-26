// Marcador mínimo de remoção a pedido do titular (issue #134): impede que links assinados emitidos antes do --forget
// recriem conversões no D1, contatos no Resend ou eventos no GA4. O D1 guarda só o HMAC-SHA256 do email normalizado
// com FORGET_HMAC_KEY (nunca o email em claro) e a data em ISO UTC com milissegundos, por 8 dias.
import { MIN_SECRET } from './token.ts';
import { normalizeEmail } from './email.ts';
import { motivo, type D1Like } from './conversoes.ts';

const enc = new TextEncoder();
const FORGET_PREFIX = 'forget-v1\n';
const ISO_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/;

/** HMAC-SHA256 em hexadecimal minúsculo de `forget-v1\n` + email normalizado, igual ao `conversoes_sync.py`. */
export async function forgetHmac(email: string, secret: string): Promise<string> {
  if (!secret || secret.length < MIN_SECRET) throw new Error('FORGET_HMAC_KEY ausente ou curta demais');
  const norm = normalizeEmail(email);
  if (!norm) throw new Error('email inválido');
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = new Uint8Array(await crypto.subtle.sign('HMAC', key, enc.encode(`${FORGET_PREFIX}${norm}`)));
  return [...sig].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export type ForgetStatus = 'ok' | 'forgotten' | 'error';

/**
 * Consulta a tabela `forgotten` para um link emitido em `issuedAtMs` (issue #134).
 * - 'forgotten': existe marcador com `forgotten_at >= issuedAtMs` (link emitido antes ou no instante da remoção).
 * - 'ok': sem marcador, ou link emitido depois da remoção.
 * - 'error': falha na leitura do D1 ou data inválida (logado sem o email).
 */
export async function checkForgotten(db: D1Like, email: string, secret: string, issuedAtMs: number): Promise<ForgetStatus> {
  try {
    const hmac = await forgetHmac(email, secret);
    const row = await db.prepare('SELECT forgotten_at FROM forgotten WHERE email_hmac = ?').bind(hmac).first<{ forgotten_at: unknown }>();
    if (!row) return 'ok';
    if (typeof row.forgotten_at !== 'string' || !ISO_UTC.test(row.forgotten_at)) {
      console.error('marcador de remoção inválido');
      return 'error';
    }
    const forgottenMs = Date.parse(row.forgotten_at);
    if (!Number.isFinite(forgottenMs)) {
      console.error('marcador de remoção inválido');
      return 'error';
    }
    return forgottenMs >= issuedAtMs ? 'forgotten' : 'ok';
  } catch (e) {
    console.error(`marcador de remoção não lido: ${motivo(e)}`);
    return 'error';
  }
}
