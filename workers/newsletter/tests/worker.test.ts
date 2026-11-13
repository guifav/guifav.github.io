// Testes do Worker de inscrição (issue #21, parte A). Cada bloco cobre uma garantia do PR (A1 a A9).
// Rodar em workers/newsletter/: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { handle, contactPath, normalizeEmail, type Env } from '../src/index.ts';
import { signLink, verifyLink } from '../src/token.ts';
import { confirmationEmail } from '../src/email.ts';

const NOW = Date.UTC(2026, 10, 11, 12, 0, 0);
const SECRET = 'segredo-de-teste-com-tamanho-suficiente-0123456789';

type Call = { url: string; method: string; body: unknown };

// Valores reais do wrangler.toml: o teste usa a mesma configuração que vai para o deploy (A9).
const TOML = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf-8');
const tomlVar = (k: string) => {
  const m = TOML.match(new RegExp(`^${k}\\s*=\\s*"([^"]*)"`, 'm'));
  assert.ok(m, `variável ${k} ausente do wrangler.toml`);
  return m[1];
};

type Opts = {
  turnstile?: unknown;
  contact?: 'none' | 'active' | 'unsubscribed';
  rlEmail?: boolean;
  rlIp?: boolean;
  createStatus?: number;
  emailStatus?: number;
  patchStatus?: number;
  segStatus?: number;
  segments?: string[];
  segListStatus?: number;
  topicStatus?: number;
  topic?: 'opt_in' | 'opt_out'; // estado do contato existente no tópico
  throwOn?: string;
  badJsonOn?: string;
};

function setup(opts: Opts = {}) {
  const calls: Call[] = [];
  // Estado do contato no Resend, para provar o efeito e não só as chamadas.
  const exists = Boolean(opts.contact && opts.contact !== 'none');
  const state = { exists, unsubscribed: opts.contact === 'unsubscribed', topic: opts.topic ?? 'opt_in',
    inSegment: exists && (opts.segments ?? []).includes('seg-1'), props: {} as Record<string, string> };
  const rl: string[] = [];
  const fetchFn = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    const method = init?.method ?? 'GET';
    let body: unknown = init?.body;
    if (typeof body === 'string' && (body.startsWith('{') || body.startsWith('['))) body = JSON.parse(body);
    if (body instanceof URLSearchParams) body = Object.fromEntries(body);
    calls.push({ url, method, body });
    if (opts.throwOn && url.includes(opts.throwOn)) throw new TypeError('fetch failed');
    if (opts.badJsonOn && url.includes(opts.badJsonOn)) return new Response('<html>', { status: 200, headers: { 'content-type': 'application/json' } });
    if (url.includes('turnstile')) {
      if (opts.turnstile instanceof Error) throw opts.turnstile;
      return Response.json(opts.turnstile ?? { success: true, hostname: 'guifav.dev' });
    }
    if (url === 'https://api.resend.com/emails') {
      return opts.emailStatus ? new Response('falha', { status: opts.emailStatus }) : Response.json({ id: 'email-1' });
    }
    if (url === 'https://api.resend.com/contacts' && method === 'POST') {
      // Contrato real (sondado em 2026-11-12): email que já existe NÃO dá erro; é upsert, 201 com o mesmo id.
      // Aplica unsubscribed, segments, properties e topics do corpo; sem `topics`, o tópico fica como estava.
      if (opts.createStatus) return Response.json({ name: 'conflict' }, { status: opts.createStatus });
      const b = body as { unsubscribed?: boolean; segments?: { id: string }[]; properties?: Record<string, string>;
        topics?: { id: string; subscription: 'opt_in' | 'opt_out' }[] };
      state.exists = true;
      if (b.unsubscribed !== undefined) state.unsubscribed = b.unsubscribed;
      if (b.segments?.some((x) => x.id === 'seg-1')) state.inSegment = true;
      Object.assign(state.props, b.properties ?? {});
      for (const t of b.topics ?? []) if (t.id === 'topic-1') state.topic = t.subscription;
      return Response.json({ object: 'contact', id: 'c1' }, { status: 201 });
    }
    if (url === 'https://api.resend.com/contacts/c1/segments/seg-1' && method === 'POST') {
      const st = opts.segStatus ?? 201;
      if (st < 300) state.inSegment = true;
      return st < 300 ? Response.json({ id: 'seg-1' }, { status: st }) : Response.json({ name: 'resource_locked' }, { status: st });
    }
    if (url.startsWith('https://api.resend.com/contacts/c1/segments?') && method === 'GET') {
      if (opts.segListStatus) return Response.json({ name: 'internal_server_error' }, { status: opts.segListStatus });
      // Paginação do Resend: limit (sem limit, 20), after = id do último item da página anterior, excluído.
      const q = new URL(url).searchParams;
      const all = opts.segments ?? [];
      const limit = Number(q.get('limit') ?? 20);
      const after = q.get('after');
      const start = after ? all.indexOf(after) + 1 : 0;
      const page = all.slice(start, start + limit);
      return Response.json({ object: 'list', has_more: start + limit < all.length, data: page.map((id) => ({ id, name: 'x' })) });
    }
    if (url === 'https://api.resend.com/contacts/c1/topics' && method === 'PATCH') {
      // Contrato conferido na API em 2026-11-12: corpo é um array puro; {topics: [...]} dá 422.
      if (!Array.isArray(body)) return Response.json({ name: 'validation_error' }, { status: 422 });
      const st = opts.topicStatus ?? 200;
      if (st < 300) for (const t of body as { id: string; subscription: 'opt_in' | 'opt_out' }[]) if (t.id === 'topic-1') state.topic = t.subscription;
      return st < 300 ? Response.json({ object: 'contact_topics', id: 'c1' }) : Response.json({ name: 'internal_server_error' }, { status: st });
    }
    if (url.startsWith('https://api.resend.com/contacts/') && method === 'GET') {
      if (!opts.contact || opts.contact === 'none') return Response.json({ name: 'not_found' }, { status: 404 });
      return Response.json({ id: 'c1', email: 'x', unsubscribed: opts.contact === 'unsubscribed' });
    }
    if (url.startsWith('https://api.resend.com/contacts/') && method === 'PATCH') {
      const st = opts.patchStatus ?? 200;
      if (st < 300) {
        const b = body as { unsubscribed?: boolean; properties?: Record<string, string> };
        if (b.unsubscribed !== undefined) state.unsubscribed = b.unsubscribed;
        Object.assign(state.props, b.properties ?? {});
      }
      return st < 300 ? Response.json({ id: 'c1' }) : Response.json({ name: 'internal_server_error' }, { status: st });
    }
    return new Response('inesperado', { status: 500 });
  };
  const env: Env = {
    RESEND_API_KEY: 're_teste',
    TURNSTILE_SECRET: 'ts_teste',
    CONFIRM_SECRET: SECRET,
    FROM: tomlVar('FROM'),
    REPLY_TO: tomlVar('REPLY_TO'),
    SEGMENT_ID: 'seg-1', // id usado pelos mocks; o real fica no wrangler.toml
    TOPIC_ID: 'topic-1',
    SITE_ORIGIN: tomlVar('SITE_ORIGIN'),
    BLOG_URL: tomlVar('BLOG_URL'),
    WORKER_URL: tomlVar('WORKER_URL'),
    TURNSTILE_HOSTNAME: tomlVar('TURNSTILE_HOSTNAME'),
    RL_EMAIL: { limit: async ({ key }) => (rl.push('email:' + key), { success: opts.rlEmail ?? true }) },
    RL_IP: { limit: async ({ key }) => (rl.push('ip:' + key), { success: opts.rlIp ?? true }) },
  };
  const run = (req: Request) => handle(req, env, { fetch: fetchFn as typeof fetch, now: () => NOW });
  return { env, run, calls, rl, state };
}

function subscribe(fields: Record<string, string>, headers: Record<string, string> = {}) {
  return new Request('https://insights.guifav.dev/subscribe', {
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
  assert.equal(r.status, 303);
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
  const m = mail.text.match(/https:\/\/insights\.guifav\.dev\/confirm\?\S+/);
  assert.ok(m, mail.text);
  assert.equal(await verifyLink(new URL(m[0]).searchParams, SECRET, NOW), 'leitor@exemplo.com');
});

test('A4: /confirm com link inválido ou expirado não chama o Resend', async () => {
  const q = await signLink('leitor@exemplo.com', NOW - 49 * 3600e3, SECRET);
  for (const url of [`https://insights.guifav.dev/confirm?${q}`, 'https://insights.guifav.dev/confirm?e=abc&x=1&s=zz', 'https://insights.guifav.dev/confirm']) {
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
  const r = await s.run(new Request(`https://insights.guifav.dev/confirm?${await signLink('leitor@exemplo.com', NOW, SECRET)}`));
  assert.equal(r.status, 303);
  assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-confirmada/');
  const create = s.calls.find((c) => c.url === 'https://api.resend.com/contacts' && c.method === 'POST');
  assert.deepEqual(create?.body, {
    email: 'leitor@exemplo.com', unsubscribed: false, segments: [{ id: 'seg-1' }],
    properties: { site_confirmed_at: new Date(NOW).toISOString() },
    topics: [{ id: 'topic-1', subscription: 'opt_in' }],
  });
});

test('T1: email que já existe (POST real é upsert 201): status global, segmento e tópico voltam, com o opt-in registrado', async () => {
  for (const contact of ['active', 'unsubscribed'] as const) {
    for (const topic of ['opt_in', 'opt_out'] as const) {
      const s = setup({ contact, topic });
      const r = await s.run(new Request(`https://insights.guifav.dev/confirm?${await signLink('leitor@exemplo.com', NOW, SECRET)}`));
      const why = `${contact}/${topic}`;
      assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-confirmada/', why);
      assert.deepEqual({ ...s.state, props: { ...s.state.props } }, { exists: true, unsubscribed: false, topic: 'opt_in', inSegment: true,
        props: { site_confirmed_at: new Date(NOW).toISOString() } }, why);
    }
  }
});

test('A5: se a API recusar o POST de um email existente (409/422), o caminho de reserva reativa sem duplicar', async () => {
  for (const [contact, createStatus] of [['active', 409], ['unsubscribed', 422]] as const) {
    const s = setup({ contact, createStatus, topic: 'opt_out' });
    const r = await s.run(new Request(`https://insights.guifav.dev/confirm?${await signLink('leitor@exemplo.com', NOW, SECRET)}`));
    assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-confirmada/', contact);
    const writes = s.calls.filter((c) => c.method !== 'GET' && c.url.includes('/contacts'));
    assert.deepEqual(writes.map((c) => `${c.method} ${c.url}`), [
      'POST https://api.resend.com/contacts',
      'PATCH https://api.resend.com/contacts/leitor@exemplo.com',
      'PATCH https://api.resend.com/contacts/c1/topics',
      'POST https://api.resend.com/contacts/c1/segments/seg-1',
    ], contact);
    // Quem tinha saído só da newsletter (tópico) volta para ela ao confirmar pelo site.
    assert.deepEqual(writes[2].body, [{ id: 'topic-1', subscription: 'opt_in' }], contact);
    // A8: a reativação também registra o opt-in pelo site, que protege o contato da sincronização com o Substack.
    assert.deepEqual(writes[1].body, { unsubscribed: false, properties: { site_confirmed_at: new Date(NOW).toISOString() } }, contact);
    assert.deepEqual([s.state.unsubscribed, s.state.topic, s.state.inSegment], [false, 'opt_in', true], contact);
    assert.deepEqual(s.state.props, { site_confirmed_at: new Date(NOW).toISOString() }, contact); // estado final, não só o corpo
  }
});

test('A5: erro do Resend na confirmação devolve 502 e não finge sucesso', async () => {
  const s = setup({ createStatus: 500 });
  const r = await s.run(new Request(`https://insights.guifav.dev/confirm?${await signLink('leitor@exemplo.com', NOW, SECRET)}`));
  assert.equal(r.status, 502);
});

// ---------------------------------------------------------------- A6

test('A6: limite por email e por IP: mesma resposta, sem envio', async () => {
  for (const o of [{ rlEmail: false }, { rlIp: false }]) {
    const s = setup(o);
    const r = await s.run(subscribe(ok));
    assert.equal(r.status, 303);
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
  const noOrigin = new Request('https://insights.guifav.dev/subscribe', { method: 'POST', body: 'email=a@b.com' });
  assert.equal((await s.run(noOrigin)).status, 403);
  assert.equal((await s.run(new Request('https://insights.guifav.dev/subscribe'))).status, 405);
  assert.equal((await s.run(new Request('https://insights.guifav.dev/confirm', { method: 'POST', headers: { origin: 'https://guifav.dev' } }))).status, 405);
  assert.equal((await s.run(new Request('https://insights.guifav.dev/'))).status, 404);
  assert.equal((await s.run(new Request('https://insights.guifav.dev/admin'))).status, 404);
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
  assert.equal(mail.from, 'Guilherme Favaron | Expert Insights <insights@guifav.dev>');
  assert.equal(mail.reply_to, 'contact@guifav.dev');
  assert.match(String(mail.subject), /Confirme/);
  const links = String(mail.html).match(/href="[^"]+"/g) ?? [];
  assert.equal(links.length, 1, String(mail.html));
  assert.match(links[0], /^href="https:\/\/insights\.guifav\.dev\/confirm\?/);
});

test('A9: endereço com caracteres de HTML é escapado no corpo', () => {
  const { html } = confirmationEmail('a"<b>@exemplo.com', 'https://insights.guifav.dev/confirm?e=1&x=2&s=3');
  assert.ok(!html.includes('<b>@'), html);
  assert.ok(html.includes('&lt;b&gt;'), html);
  assert.ok(html.includes('href="https://insights.guifav.dev/confirm?e=1&amp;x=2&amp;s=3"'), html);
});

test('contactPath: @ literal, o resto codificado', () => {
  assert.equal(contactPath('a+b@x.com'), 'https://api.resend.com/contacts/a%2Bb@x.com');
});

test('normalizeEmail: minúsculas e sem espaço nas pontas; recusa cabeçalho e controle', () => {
  assert.equal(normalizeEmail('  A.B+tag@Exemplo.COM.br '), 'a.b+tag@exemplo.com.br');
  for (const bad of ['a@b', '@b.com', 'a@-b.com', 'a@b.com\nbcc@x.com', 'a@b..com', 'a<b>@c.com', 'a@b.com,c@d.com']) assert.equal(normalizeEmail(bad), null, bad);
});

// ---------------------------------------------------------------- rodada 1 do gate (#31)

const confirmUrl = async () => `https://insights.guifav.dev/confirm?${await signLink('leitor@exemplo.com', NOW, SECRET)}`;

test('A5: erro do Resend ao incluir no segmento não vira sucesso (409 resource_locked, 422, 500)', async () => {
  for (const segStatus of [409, 422, 500]) {
    const s = setup({ contact: 'active', createStatus: 409, segStatus });
    const r = await s.run(new Request(await confirmUrl()));
    assert.equal(r.status, 502, String(segStatus));
    assert.ok(s.calls.some((c) => c.url.startsWith('https://api.resend.com/contacts/c1/segments?') && c.method === 'GET'), String(segStatus));
  }
});

test('A5: inclusão que falha, mas a lista do contato mostra o segmento, é sucesso', async () => {
  const s = setup({ contact: 'active', createStatus: 409, segStatus: 409, segments: ['outro', 'seg-1'] });
  const r = await s.run(new Request(await confirmUrl()));
  assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-confirmada/');
});

test('T1: contato novo entra no tópico pelo próprio POST, sem chamada extra', async () => {
  const s = setup({ contact: 'none' });
  const r = await s.run(new Request(await confirmUrl()));
  assert.equal(r.status, 303);
  assert.equal(s.calls.filter((c) => c.url.includes('/topics')).length, 0);
});

test('T1: falha ao devolver o contato ao tópico vira 502, sem incluir no segmento', async () => {
  for (const topicStatus of [422, 500]) {
    const s = setup({ contact: 'unsubscribed', createStatus: 409, topicStatus });
    const r = await s.run(new Request(await confirmUrl()));
    assert.equal(r.status, 502, String(topicStatus));
    assert.equal(s.calls.filter((c) => c.url.includes('/segments/')).length, 0, String(topicStatus));
  }
});

test('T1: sem TOPIC_ID o Worker não cadastra ninguém (500), e o wrangler.toml traz o tópico real', async () => {
  const s = setup({ contact: 'none' });
  s.env.TOPIC_ID = '';
  const r = await s.run(new Request(await confirmUrl()));
  assert.equal(r.status, 500);
  assert.equal(s.calls.length, 0);
  assert.equal(tomlVar('TOPIC_ID'), '77f2a0b8-16aa-4ac6-9840-c352559edb51');
});

test('A5: falha ao reativar o contato devolve 502', async () => {
  const s = setup({ contact: 'unsubscribed', createStatus: 409, patchStatus: 500 });
  const r = await s.run(new Request(await confirmUrl()));
  assert.equal(r.status, 502);
});

test('A1/A7: HEAD /confirm com link válido responde 405 e não chama o Resend', async () => {
  const s = setup({ contact: 'none' });
  const r = await s.run(new Request(await confirmUrl(), { method: 'HEAD' }));
  assert.equal(r.status, 405);
  assert.equal(s.calls.length, 0);
});

test('A4: validade adulterada para outro horário numérico é recusada pela assinatura, também na rota', async () => {
  const p = new URLSearchParams(await signLink('leitor@exemplo.com', NOW, SECRET));
  p.set('x', String(Number(p.get('x')) + 365 * 86400));
  assert.equal(await verifyLink(p, SECRET, NOW), null);
  const s = setup({ contact: 'none' });
  const r = await s.run(new Request(`https://insights.guifav.dev/confirm?${p}`));
  assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-invalida/');
  assert.equal(s.calls.length, 0);
});

test('A6: limites do wrangler.toml são 5 por IP e 1 por email, a cada 60 s', () => {
  const block = (name: string) => {
    const m = TOML.match(new RegExp(`\\[\\[ratelimits\\]\\]\\s*\\nname = "${name}"\\s*\\nnamespace_id = "\\d+"\\s*\\nsimple = \\{ limit = (\\d+), period = (\\d+) \\}`));
    assert.ok(m, name);
    return [Number(m[1]), Number(m[2])];
  };
  assert.deepEqual(block('RL_IP'), [5, 60]);
  assert.deepEqual(block('RL_EMAIL'), [1, 60]);
});

test('A6: o limite por IP vale antes do Turnstile; acima dele, nem o siteverify é chamado', async () => {
  const s = setup({ rlIp: false });
  const r = await s.run(subscribe(ok));
  assert.equal(r.status, 303);
  assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-enviada/');
  assert.equal(s.calls.length, 0);
  const t = setup({ turnstile: { success: false } });
  await t.run(subscribe(ok));
  assert.deepEqual(t.rl, ['ip:203.0.113.7']);
});

test('A8: nenhum arquivo do Worker atribui segredo, com chave TOML entre aspas ou não', () => {
  const root = new URL('../', import.meta.url);
  const files: string[] = [];
  const walk = (dir: URL) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      if (e.name === 'node_modules' || e.name === 'tests') continue;
      const u = new URL(e.name + (e.isDirectory() ? '/' : ''), dir);
      if (e.isDirectory()) walk(u);
      else files.push(u.pathname);
    }
  };
  walk(root);
  assert.ok(files.some((f) => f.endsWith('wrangler.toml')));
  const secret = /^\s*["']?(RESEND_API_KEY|TURNSTILE_SECRET|CONFIRM_SECRET)["']?\s*[=:]\s*["'][^"']+/m;
  for (const f of files) {
    const t = readFileSync(f, 'utf-8');
    assert.doesNotMatch(t, secret, f);
    assert.doesNotMatch(t, /\bre_[A-Za-z0-9_]{16,}/, f);
  }
  assert.ok(secret.test('"CONFIRM_SECRET" = "segredo-real-com-mais-de-32-caracteres"'));
  assert.ok(secret.test("RESEND_API_KEY = 're_xxx'"));
  const gitignore = readFileSync(new URL('../../../.gitignore', import.meta.url), 'utf-8');
  assert.match(gitignore, /^\.dev\.vars$/m);
});

test('A9: remetente e reply-to vêm do wrangler.toml real; um único link, o de confirmação, no HTML e no texto', async () => {
  assert.equal(tomlVar('FROM'), 'Guilherme Favaron | Expert Insights <insights@guifav.dev>');
  assert.equal(tomlVar('REPLY_TO'), 'contact@guifav.dev');
  const s = setup();
  await s.run(subscribe(ok));
  const mail = s.calls.find((c) => c.url.endsWith('/emails'))!.body as { html: string; text: string };
  assert.equal(typeof mail.text, 'string');
  const textUrls = mail.text.match(/https?:\/\/\S+/g) ?? [];
  const hrefs = [...mail.html.matchAll(/href="([^"]+)"/g)].map((m) => m[1].replace(/&amp;/g, '&'));
  const htmlUrls = mail.html.match(/https?:\/\/[^\s"'<>]+/g) ?? [];
  assert.equal(textUrls.length, 1, mail.text);
  assert.equal(hrefs.length, 1, mail.html);
  assert.equal(htmlUrls.length, 1, mail.html);
  assert.equal(hrefs[0], textUrls[0]);
  assert.ok(textUrls[0].startsWith('https://insights.guifav.dev/confirm?'));
  assert.equal(await verifyLink(new URL(textUrls[0]).searchParams, SECRET, NOW), 'leitor@exemplo.com');
});

// ---------------------------------------------------------------- rodada 2 do gate (#31), P3 do Astra

test('A5: inclusão recusada e o contato só em outro segmento: 502, nunca sucesso', async () => {
  const s = setup({ contact: 'active', createStatus: 409, segStatus: 409, segments: ['outro'] });
  const r = await s.run(new Request(await confirmUrl()));
  assert.equal(r.status, 502);
  assert.equal(loc(r), null);
});

test('A5: o segmento na segunda página da lista conta (paginação por after, 100 por página)', async () => {
  const many = Array.from({ length: 130 }, (_, i) => `s${i}`);
  many.splice(110, 0, 'seg-1');
  const s = setup({ contact: 'active', createStatus: 409, segStatus: 409, segments: many });
  const r = await s.run(new Request(await confirmUrl()));
  assert.equal(loc(r), 'https://guifav.dev/blog/inscricao-confirmada/');
  const pages = s.calls.filter((c) => c.url.startsWith('https://api.resend.com/contacts/c1/segments?'));
  assert.equal(pages.length, 2);
  assert.equal(new URL(pages[0].url).searchParams.get('limit'), '100');
  assert.equal(new URL(pages[1].url).searchParams.get('after'), 's99');
});

test('A5: lista sem o segmento em nenhuma página, ou página que falha: 502', async () => {
  const many = Array.from({ length: 150 }, (_, i) => `s${i}`);
  const s = setup({ contact: 'active', createStatus: 409, segStatus: 409, segments: many });
  assert.equal((await s.run(new Request(await confirmUrl()))).status, 502);
  const t = setup({ contact: 'active', createStatus: 409, segStatus: 409, segments: many, segListStatus: 500 });
  assert.equal((await t.run(new Request(await confirmUrl()))).status, 502);
});

test('A5: a leitura dos segmentos para em 20 páginas; o segmento na 21ª não conta (502)', async () => {
  // P3 do Astra no #34: sem este teste, trocar o teto de 20 páginas passava em silêncio.
  const many = Array.from({ length: 2100 }, (_, i) => `s${i}`);
  many.splice(2050, 0, 'seg-1');
  const s = setup({ contact: 'active', createStatus: 409, segStatus: 409, segments: many });
  const r = await s.run(new Request(await confirmUrl()));
  assert.equal(r.status, 502);
  assert.notEqual(loc(r), 'https://guifav.dev/blog/inscricao-confirmada/');
  assert.equal(s.calls.filter((c) => c.url.startsWith('https://api.resend.com/contacts/c1/segments?')).length, 20);
});

test('A5: falha de rede ou JSON inválido do Resend na confirmação viram 502, sem rejeitar o handler', async () => {
  const cases: Opts[] = [
    { contact: 'none', throwOn: 'api.resend.com/contacts' },
    { contact: 'active', createStatus: 409, throwOn: 'api.resend.com/contacts/c1/segments' },
    { contact: 'active', createStatus: 409, badJsonOn: 'api.resend.com/contacts/leitor' },
    { contact: 'active', createStatus: 409, segStatus: 409, badJsonOn: 'api.resend.com/contacts/c1/segments?' },
  ];
  for (const o of cases) {
    const s = setup(o);
    const r = await s.run(new Request(await confirmUrl()));
    assert.equal(r.status, 502, JSON.stringify(o));
  }
});

test('nome da lista: Expert Insights, de Guilherme Favaron, sem "newsletter" nem "IA Aplicada" (Gui, 12/11/2026)', () => {
  const m = confirmationEmail('a@b.com', 'https://insights.guifav.dev/confirm?x=1');
  assert.equal(m.subject, 'Confirme sua inscrição nos Expert Insights');
  for (const body of [m.text, m.html]) {
    assert.match(body, /nos Expert Insights, os artigos de Guilherme Favaron por email\./);
    assert.doesNotMatch(body, /newsletter/i);
    assert.doesNotMatch(body, /IA Aplicada/);
  }
});
