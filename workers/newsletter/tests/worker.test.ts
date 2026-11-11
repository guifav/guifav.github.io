// Testes do Worker de inscrição (issue #21, parte A). Cada bloco cobre uma garantia do PR (A1 a A9).
// Rodar em workers/newsletter/: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { handle, contactPath, normalizeEmail, type Env } from '../src/index.ts';
import { signLink, verifyLink } from '../src/token.ts';
import { confirmationEmail } from '../src/email.ts';

const NOW = Date.UTC(2026, 10, 11, 12, 0, 0);
const SECRET = 'segredo-de-teste-com-tamanho-suficiente-0123456789';

type Call = { url: string; method: string; body: unknown };

function setup(opts: { turnstile?: unknown; contact?: 'none' | 'active' | 'unsubscribed'; rlEmail?: boolean; rlIp?: boolean; createStatus?: number; emailStatus?: number } = {}) {
  const calls: Call[] = [];
  const rl: string[] = [];
  const fetchFn = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    const method = init?.method ?? 'GET';
    let body: unknown = init?.body;
    if (typeof body === 'string' && body.startsWith('{')) body = JSON.parse(body);
    if (body instanceof URLSearchParams) body = Object.fromEntries(body);
    calls.push({ url, method, body });
    if (url.includes('turnstile')) {
      if (opts.turnstile instanceof Error) throw opts.turnstile;
      return Response.json(opts.turnstile ?? { success: true, hostname: 'guifav.dev' });
    }
    if (url === 'https://api.resend.com/emails') {
      return opts.emailStatus ? new Response('falha', { status: opts.emailStatus }) : Response.json({ id: 'email-1' });
    }
    if (url === 'https://api.resend.com/contacts' && method === 'POST') {
      const st = opts.createStatus ?? (opts.contact && opts.contact !== 'none' ? 409 : 200);
      return st === 200 ? Response.json({ id: 'c1' }) : Response.json({ name: 'conflict' }, { status: st });
    }
    if (url.startsWith('https://api.resend.com/contacts/') && method === 'GET') {
      if (!opts.contact || opts.contact === 'none') return Response.json({ name: 'not_found' }, { status: 404 });
      return Response.json({ id: 'c1', email: 'x', unsubscribed: opts.contact === 'unsubscribed' });
    }
    if (url.startsWith('https://api.resend.com/contacts/')) return Response.json({ id: 'c1' });
    return new Response('inesperado', { status: 500 });
  };
  const env: Env = {
    RESEND_API_KEY: 're_teste',
    TURNSTILE_SECRET: 'ts_teste',
    CONFIRM_SECRET: SECRET,
    FROM: 'IA Aplicada <newsletter@guifav.dev>',
    REPLY_TO: 'contact@guifav.dev',
    SEGMENT_ID: 'seg-1',
    SITE_ORIGIN: 'https://guifav.dev',
    BLOG_URL: 'https://guifav.dev/blog',
    WORKER_URL: 'https://newsletter.guifav.dev',
    TURNSTILE_HOSTNAME: 'guifav.dev',
    RL_EMAIL: { limit: async ({ key }) => (rl.push('email:' + key), { success: opts.rlEmail ?? true }) },
    RL_IP: { limit: async ({ key }) => (rl.push('ip:' + key), { success: opts.rlIp ?? true }) },
  };
  const run = (req: Request) => handle(req, env, { fetch: fetchFn as typeof fetch, now: () => NOW });
  return { env, run, calls, rl };
}

function subscribe(fields: Record<string, string>, headers: Record<string, string> = {}) {
  return new Request('https://newsletter.guifav.dev/subscribe', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: 'https://guifav.dev', 'cf-connecting-ip': '203.0.113.7', ...headers },
    body: new URLSearchParams(fields).toString(),
  });
}

const ok = { email: 'Leitor@Exemplo.com ', 'cf-turnstile-response': 'tok' };
const loc = (r: Response) => r.headers.get('location');

// ---------------------------------------------------------------- A1 e A2

test('A1/A2: inscrição válida envia só o email de confirmação e redireciona para inscricao-enviada', async () => {
  const s = setup();
  const r = await s.run(subscribe(ok));
  assert.equal(r.status, 303);
  assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-enviada/');
  const resend = s.calls.filter((c) => c.url.includes('resend.com'));
  assert.deepEqual(resend.map((c) => c.url), ['https://api.resend.com/emails']);
  const mail = resend[0].body as { to: string[] };
  assert.deepEqual(mail.to, ['leitor@exemplo.com']);
});

test('A2: email inválido tem a mesma resposta, sem envio', async () => {
  for (const email of ['', 'sem-arroba', 'a@b', 'a b@c.com', 'x'.repeat(250) + '@e.com', 'a@b.com\r\nBcc: z@z.com']) {
    const s = setup();
    const r = await s.run(subscribe({ ...ok, email }));
    assert.equal(r.status, 303, email);
    assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-enviada/', email);
    assert.equal(s.calls.filter((c) => c.url.includes('resend.com')).length, 0, email);
  }
});

test('A2: email já inscrito tem a mesma resposta (o Worker não consulta a lista no /subscribe)', async () => {
  const s = setup({ contact: 'active' });
  const r = await s.run(subscribe(ok));
  assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-enviada/');
  assert.ok(!s.calls.some((c) => c.url.includes('/contacts')));
});

test('A1/A2: falha do Resend no envio não muda a resposta nem cria contato', async () => {
  const s = setup({ emailStatus: 500 });
  const r = await s.run(subscribe(ok));
  assert.equal(r.status, 303);
  assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-enviada/');
  assert.ok(!s.calls.some((c) => c.url.includes('/contacts')));
});

// ---------------------------------------------------------------- A3

test('A3: sem token, token reprovado, hostname errado ou siteverify fora do ar: erro de verificação, sem envio', async () => {
  const cases: Array<[string, Parameters<typeof setup>[0], Record<string, string>]> = [
    ['sem token', {}, { email: 'a@b.com' }],
    ['reprovado', { turnstile: { success: false, 'error-codes': ['invalid-input-response'] } }, ok],
    ['outro host', { turnstile: { success: true, hostname: 'evil.example' } }, ok],
    ['fora do ar', { turnstile: new Error('rede') }, ok],
  ];
  for (const [name, o, fields] of cases) {
    const s = setup(o);
    const r = await s.run(subscribe(fields));
    assert.equal(r.status, 303, name);
    assert.equal(loc(r), 'https://guifav.dev/blog/assinar/#erro-verificacao', name);
    assert.equal(s.calls.filter((c) => c.url.includes('resend.com')).length, 0, name);
  }
});

test('A3: o siteverify recebe o segredo, o token e o IP', async () => {
  const s = setup();
  await s.run(subscribe(ok));
  const v = s.calls.find((c) => c.url.includes('siteverify'));
  assert.equal(v?.method, 'POST');
  assert.deepEqual(v?.body, { secret: 'ts_teste', response: 'tok', remoteip: '203.0.113.7' });
});

// ---------------------------------------------------------------- A4

test('A4: link assinado vale por 48 h e verifica email, validade e assinatura', async () => {
  const q = await signLink('leitor@exemplo.com', NOW, SECRET);
  const p = new URLSearchParams(q);
  assert.equal(await verifyLink(p, SECRET, NOW + 47 * 3600e3), 'leitor@exemplo.com');
  assert.equal(await verifyLink(p, SECRET, NOW + 48 * 3600e3 + 1000), null, 'expirado');
  assert.equal(await verifyLink(p, 'outro-segredo-0123456789-0123456789', NOW), null, 'outro segredo');
  for (const k of ['e', 'x', 's']) {
    const t = new URLSearchParams(q);
    const v = t.get(k)!;
    t.set(k, (v[0] === 'A' ? 'B' : 'A') + v.slice(1));
    assert.equal(await verifyLink(t, SECRET, NOW), null, `adulterado em ${k}`);
    const m = new URLSearchParams(q);
    m.delete(k);
    assert.equal(await verifyLink(m, SECRET, NOW), null, `sem ${k}`);
  }
  for (const bad of ['x=abc', 'x=1e20', 'x=-1', 'x=12.5']) {
    const t = new URLSearchParams(q);
    const [k, v] = bad.split('=');
    t.set(k, v);
    assert.equal(await verifyLink(t, SECRET, NOW), null, bad);
  }
});

test('A4: o email de confirmação leva o link assinado do Worker', async () => {
  const s = setup();
  await s.run(subscribe(ok));
  const mail = s.calls.find((c) => c.url.endsWith('/emails'))!.body as { html: string; text: string };
  const m = mail.text.match(/https:\/\/newsletter\.guifav\.dev\/confirm\?\S+/);
  assert.ok(m, mail.text);
  assert.equal(await verifyLink(new URL(m[0]).searchParams, SECRET, NOW), 'leitor@exemplo.com');
});

test('A4: /confirm com link inválido ou expirado não chama o Resend', async () => {
  const q = await signLink('leitor@exemplo.com', NOW - 49 * 3600e3, SECRET);
  for (const url of [`https://newsletter.guifav.dev/confirm?${q}`, 'https://newsletter.guifav.dev/confirm?e=abc&x=1&s=zz', 'https://newsletter.guifav.dev/confirm']) {
    const s = setup();
    const r = await s.run(new Request(url));
    assert.equal(r.status, 303, url);
    assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-invalida/', url);
    assert.equal(s.calls.length, 0, url);
  }
});

// ---------------------------------------------------------------- A1 e A5

test('A1: /confirm válido cria o contato no segmento e redireciona para inscricao-confirmada', async () => {
  const s = setup({ contact: 'none' });
  const r = await s.run(new Request(`https://newsletter.guifav.dev/confirm?${await signLink('leitor@exemplo.com', NOW, SECRET)}`));
  assert.equal(r.status, 303);
  assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-confirmada/');
  const create = s.calls.find((c) => c.url === 'https://api.resend.com/contacts' && c.method === 'POST');
  assert.deepEqual(create?.body, { email: 'leitor@exemplo.com', unsubscribed: false, segments: [{ id: 'seg-1' }] });
});

test('A5: confirmação repetida e contato descadastrado reativado, sem duplicar', async () => {
  for (const contact of ['active', 'unsubscribed'] as const) {
    const s = setup({ contact });
    const r = await s.run(new Request(`https://newsletter.guifav.dev/confirm?${await signLink('leitor@exemplo.com', NOW, SECRET)}`));
    assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-confirmada/', contact);
    const writes = s.calls.filter((c) => c.method !== 'GET' && c.url.includes('/contacts'));
    assert.deepEqual(writes.map((c) => `${c.method} ${c.url}`), [
      'POST https://api.resend.com/contacts',
      'PATCH https://api.resend.com/contacts/leitor@exemplo.com',
      'POST https://api.resend.com/contacts/leitor@exemplo.com/segments/seg-1',
    ], contact);
    assert.deepEqual(writes[1].body, { unsubscribed: false }, contact);
  }
});

test('A5: erro do Resend na confirmação devolve 502 e não finge sucesso', async () => {
  const s = setup({ createStatus: 500 });
  const r = await s.run(new Request(`https://newsletter.guifav.dev/confirm?${await signLink('leitor@exemplo.com', NOW, SECRET)}`));
  assert.equal(r.status, 502);
});

// ---------------------------------------------------------------- A6

test('A6: limite por email e por IP: mesma resposta, sem envio', async () => {
  for (const o of [{ rlEmail: false }, { rlIp: false }]) {
    const s = setup(o);
    const r = await s.run(subscribe(ok));
    assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-enviada/');
    assert.equal(s.calls.filter((c) => c.url.includes('resend.com')).length, 0);
  }
  const s = setup();
  await s.run(subscribe(ok));
  assert.deepEqual(s.rl, ['ip:203.0.113.7', 'email:leitor@exemplo.com']);
});

// ---------------------------------------------------------------- A7

test('A7: origem, método e rota', async () => {
  const s = setup();
  assert.equal((await s.run(subscribe(ok, { origin: 'https://evil.example' }))).status, 403);
  const noOrigin = new Request('https://newsletter.guifav.dev/subscribe', { method: 'POST', body: 'email=a@b.com' });
  assert.equal((await s.run(noOrigin)).status, 403);
  assert.equal((await s.run(new Request('https://newsletter.guifav.dev/subscribe'))).status, 405);
  assert.equal((await s.run(new Request('https://newsletter.guifav.dev/confirm', { method: 'POST', headers: { origin: 'https://guifav.dev' } }))).status, 405);
  assert.equal((await s.run(new Request('https://newsletter.guifav.dev/'))).status, 404);
  assert.equal((await s.run(new Request('https://newsletter.guifav.dev/admin'))).status, 404);
  assert.equal(s.calls.length, 0);
});

test('A7: corpo grande demais é recusado', async () => {
  const s = setup();
  const r = await s.run(subscribe({ ...ok, lixo: 'x'.repeat(20_000) }));
  assert.equal(r.status, 413);
  assert.equal(s.calls.length, 0);
});

// ---------------------------------------------------------------- A8

test('A8: wrangler.toml sem segredos e com os nomes dos segredos documentados', () => {
  const toml = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf-8');
  assert.doesNotMatch(toml, /re_[A-Za-z0-9]{8,}|0x[0-9A-Fa-f]{20,}|RESEND_API_KEY\s*=|TURNSTILE_SECRET\s*=|CONFIRM_SECRET\s*=/);
  for (const k of ['RESEND_API_KEY', 'TURNSTILE_SECRET', 'CONFIRM_SECRET']) assert.match(toml, new RegExp(`#.*${k}`));
});

test('A8: segredo de confirmação curto demais derruba o Worker em vez de assinar fraco', async () => {
  const s = setup();
  s.env.CONFIRM_SECRET = 'curto';
  const r = await s.run(subscribe(ok));
  assert.equal(r.status, 500);
  assert.equal(s.calls.filter((c) => c.url.includes('resend.com')).length, 0);
});

// ---------------------------------------------------------------- A9

test('A9: email de confirmação com remetente, reply-to, HTML e texto, e um único link', async () => {
  const s = setup();
  await s.run(subscribe(ok));
  const mail = s.calls.find((c) => c.url.endsWith('/emails'))!.body as Record<string, unknown>;
  assert.equal(mail.from, 'IA Aplicada <newsletter@guifav.dev>');
  assert.equal(mail.reply_to, 'contact@guifav.dev');
  assert.match(String(mail.subject), /Confirme/);
  const links = String(mail.html).match(/href="[^"]+"/g) ?? [];
  assert.equal(links.length, 1, String(mail.html));
  assert.match(links[0], /^href="https:\/\/newsletter\.guifav\.dev\/confirm\?/);
});

test('A9: endereço com caracteres de HTML é escapado no corpo', () => {
  const { html } = confirmationEmail('a"<b>@exemplo.com', 'https://newsletter.guifav.dev/confirm?e=1&x=2&s=3');
  assert.ok(!html.includes('<b>@'), html);
  assert.ok(html.includes('&lt;b&gt;'), html);
  assert.ok(html.includes('href="https://newsletter.guifav.dev/confirm?e=1&amp;x=2&amp;s=3"'), html);
});

test('contactPath: @ literal, o resto codificado', () => {
  assert.equal(contactPath('a+b@x.com'), 'https://api.resend.com/contacts/a%2Bb@x.com');
});

test('normalizeEmail: minúsculas e sem espaço nas pontas; recusa cabeçalho e controle', () => {
  assert.equal(normalizeEmail('  A.B+tag@Exemplo.COM.br '), 'a.b+tag@exemplo.com.br');
  for (const bad of ['a@b', '@b.com', 'a@-b.com', 'a@b.com\nbcc@x.com', 'a@b..com', 'a<b>@c.com', 'a@b.com,c@d.com']) assert.equal(normalizeEmail(bad), null, bad);
});
