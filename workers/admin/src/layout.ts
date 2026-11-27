// Cabeçalho de navegação, escape de HTML e respostas HTTP comuns do painel (issue #193).

export type Aba = 'resumo' | 'assinantes' | 'rascunhos' | 'contatos' | 'conteudos';

const ABAS: { id: Aba; label: string; href: string }[] = [
  { id: 'resumo', label: 'Resumo', href: '/admin' },
  { id: 'assinantes', label: 'Assinantes', href: '/admin/assinantes' },
  { id: 'rascunhos', label: 'Rascunhos', href: '/admin/rascunhos' },
  { id: 'contatos', label: 'Contatos', href: '/admin/contatos' },
  { id: 'conteudos', label: 'Conteúdos', href: '/admin/conteudos' },
];

export const SECURITY_HEADERS: Record<string, string> = {
  'cache-control': 'no-store',
  'x-robots-tag': 'noindex, nofollow',
  'x-frame-options': 'DENY',
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'same-origin', // no-referrer faz o POST do formulário sair com Origin: null (403)
  'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
};

export const respond = (status: number, body: string, type: string, extra: Record<string, string> = {}) =>
  new Response(body, { status, headers: { 'content-type': type, ...SECURITY_HEADERS, ...extra } });

export const text = (status: number, body: string) => respond(status, body, 'text/plain; charset=utf-8');

export const redirect = (location: string) => new Response(null, { status: 303, headers: { location, ...SECURITY_HEADERS } });

export function esc(s: unknown): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
}

/** Barra de navegação comum: a tela atual sem link, as outras com link. */
export function nav(atual: Aba): string {
  return `<p>${ABAS.map((a) => (a.id === atual ? a.label : `<a href="${a.href}">${a.label}</a>`)).join(' · ')}</p>`;
}

export const BASE_STYLE = `body{font:15px/1.45 system-ui,sans-serif;margin:1.5rem;color:#111}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #ddd;padding:.35rem .5rem;text-align:left;vertical-align:top}
.k{display:flex;flex-wrap:wrap;gap:1rem;margin:.5rem 0 1rem}.k div{background:#f4f4f4;padding:.4rem .7rem;border-radius:6px}.erro{color:#a00}.ok{color:#060}.muted{color:#555}form.f{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:1rem}
td form{margin:0}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem;margin:1rem 0}.card{border:1px solid #ddd;border-radius:6px;padding:.8rem 1rem}.card h2{margin:0 0 .4rem;font-size:1.05rem}.card p{margin:.25rem 0}
tr.sub td{background:#fafafa}tr.sub td:first-child{padding-left:1.75rem}`;

export function htmlPage(opts: { title: string; atual: Aba; body: string; extraStyle?: string }): string {
  const style = opts.extraStyle ? `${BASE_STYLE}\n${opts.extraStyle}` : BASE_STYLE;
  return `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>${esc(opts.title)}</title>
<style>${style}</style></head><body>
${nav(opts.atual)}
${opts.body}
</body></html>`;
}

/** Formata data ISO UTC para "AAAA-MM-DD HH:MM" (sem o sufixo UTC, para tabelas). */
export function formatDateTimeUtc(iso: string | null | undefined): string {
  if (!iso) return '';
  return iso.length >= 16 ? iso.slice(0, 16).replace('T', ' ') : iso;
}
