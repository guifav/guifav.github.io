// Testes do vínculo uid -> email (épico #197, corte 2): migração 0013 e POST /identidade.
// Rodar em workers/newsletter/: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { handle, type Env } from '../src/index.ts';
import {
  FIREBASE_ISSUER,
  FIREBASE_PROJECT_ID,
  FIREBASE_TENANT_ID,
  JWKS_URL,
  loginConversionId,
  resetJwksCache,
} from '../src/identidade.ts';
import { heatingOptoutHmac } from '../src/forget.ts';
import { d1, rows, capturaErros } from './d1.ts';

const NOW = Date.UTC(2026, 10, 27, 15, 0, 0);
const NOW_SEC = Math.floor(NOW / 1000);
const SECRET = 'segredo-de-teste-com-tamanho-suficiente-0123456789';
const FORGET_KEY = 'chave-hmac-de-teste-com-32-bytes-ou-mais-0123456789';
const TOML = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf-8');
const tomlVar = (k: string) => TOML.match(new RegExp(`^${k}\\s*=\\s*"([^"]*)"`, 'm'))![1];

const enc = new TextEncoder();

function b64url(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString('base64url');
}

function b64urlJson(obj: unknown): string {
  return Buffer.from(JSON.stringify(obj), 'utf-8').toString('base64url');
}

async function generateRsaPair(kid: string) {
  const pair = await crypto.subtle.generateKey(
    {
      name: 'RSASSA-PKCS1-v1_5',
      modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: 'SHA-256',
    },
    true,
    ['sign', 'verify'],
  );
  const pub = await crypto.subtle.exportKey('jwk', pair.publicKey);
  return {
    kid,
    privateKey: pair.privateKey,
    jwk: { kty: pub.kty, n: pub.n, e: pub.e, alg: 'RS256', use: 'sig', kid },
  };
}

const KEY1 = await generateRsaPair('kid-teste-1');
const KEY2 = await generateRsaPair('kid-teste-2');

async function signJwt(
  payload: Record<string, unknown>,
  opts: { header?: Record<string, unknown>; privateKey?: CryptoKey } = {},
): Promise<string> {
  const header = opts.header ?? { alg: 'RS256', typ: 'JWT', kid: KEY1.kid };
  const input = `${b64urlJson(header)}.${b64urlJson(payload)}`;
  const sig = new Uint8Array(
    await crypto.subtle.sign('RSASSA-PKCS1-v1_5', opts.privateKey ?? KEY1.privateKey, enc.encode(input)),
  );
  return `${input}.${b64url(sig)}`;
}

function validPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    iss: FIREBASE_ISSUER,
    aud: FIREBASE_PROJECT_ID,
    sub: 'UidValido123ABC',
    iat: NOW_SEC - 60,
    exp: NOW_SEC + 3600,
    email: 'Leitor@Exemplo.com',
    email_verified: true,
    firebase: {
      tenant: FIREBASE_TENANT_ID,
      sign_in_provider: 'google.com',
    },
    ...overrides,
  };
}

type SetupOpts = {
  now?: number;
  rlIp?: boolean;
  forgetKey?: string;
  d1Falha?: RegExp;
  d1Mensagem?: string;
  jwksKeys?: unknown[];
  jwksCacheControl?: string | null;
  jwksStatus?: number;
  jwksDelayMs?: number;
  jwksTimeoutMs?: number;
  jwksThrow?: Error;
};

function setup(opts: SetupOpts = {}) {
  resetJwksCache();
  let jwksFetches = 0;
  let jwksKeysAtual = opts.jwksKeys;
  const rl: string[] = [];
  const db = d1(opts.d1Falha, opts.d1Mensagem ?? 'D1_ERROR: falha para leitor@exemplo.com');
  const pending: Promise<unknown>[] = [];

  const fetchFn = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url === JWKS_URL) {
      jwksFetches += 1;
      if (opts.jwksThrow) throw opts.jwksThrow;
      if (opts.jwksDelayMs) {
        await new Promise<void>((resolve, reject) => {
          const t = setTimeout(resolve, opts.jwksDelayMs);
          init?.signal?.addEventListener('abort', () => {
            clearTimeout(t);
            reject(new Error('AbortError'));
          });
        });
      }
      const status = opts.jwksStatus ?? 200;
      const headers: Record<string, string> = { 'content-type': 'application/json' };
      const cc = opts.jwksCacheControl === undefined ? 'public, max-age=3600' : opts.jwksCacheControl;
      if (cc !== null) headers['cache-control'] = cc;
      return new Response(JSON.stringify({ keys: jwksKeysAtual ?? [KEY1.jwk] }), { status, headers });
    }
    return new Response('inesperado', { status: 500 });
  };

  const env: Env = {
    RESEND_API_KEY: 're_teste',
    TURNSTILE_SECRET: 'ts_teste',
    CONFIRM_SECRET: SECRET,
    FORGET_HMAC_KEY: 'forgetKey' in opts ? (opts.forgetKey as string) : FORGET_KEY,
    FROM: tomlVar('FROM'),
    MATERIAL_FROM: tomlVar('MATERIAL_FROM'),
    REPLY_TO: tomlVar('REPLY_TO'),
    SEGMENT_ID: 'seg-1',
    TOPIC_ID: 'topic-1',
    SITE_ORIGIN: tomlVar('SITE_ORIGIN'),
    BLOG_URL: tomlVar('BLOG_URL'),
    WORKER_URL: tomlVar('WORKER_URL'),
    TURNSTILE_HOSTNAME: tomlVar('TURNSTILE_HOSTNAME'),
    RL_EMAIL: { limit: async () => ({ success: true }) },
    RL_IP: { limit: async ({ key }) => (rl.push(key), { success: opts.rlIp ?? true }) },
    DB: db,
    GA4_MEASUREMENT_ID: tomlVar('GA4_MEASUREMENT_ID'),
  };

  let clock = opts.now ?? NOW;
  const run = (req: Request) =>
    handle(req, env, {
      fetch: fetchFn as typeof fetch,
      now: () => clock,
      waitUntil: (p) => void pending.push(p),
      jwksTimeoutMs: opts.jwksTimeoutMs,
    });
  return {
    env,
    db: db.raw,
    rl,
    run,
    jwksFetches: () => jwksFetches,
    trocaJwks: (k: JsonWebKey[]) => void (jwksKeysAtual = k as never),
    avanca: (ms: number) => void (clock += ms),
    rows: (t: string) => rows(db.raw, t),
  };
}

function idReq(opts: {
  method?: string;
  origin?: string | null;
  token?: string | null;
  authHeader?: string;
  body?: string | Uint8Array;
  ip?: string;
} = {}): Request {
  const headers: Record<string, string> = {};
  if (opts.origin !== null) headers.origin = opts.origin ?? 'https://guifav.dev';
  if (opts.authHeader !== undefined) headers.authorization = opts.authHeader;
  else if (opts.token !== null && opts.token !== undefined) headers.authorization = `Bearer ${opts.token}`;
  if (opts.ip) headers['cf-connecting-ip'] = opts.ip;
  return new Request('https://insights.guifav.dev/identidade', {
    method: opts.method ?? 'POST',
    headers,
    body: opts.body,
  });
}

test('migração 0013_site_identities: CHECKs de uid, email, linked_at e índice por email', () => {
  const s = setup();
  const iso = '2026-11-27T15:00:00.000Z';
  s.db.prepare('INSERT INTO site_identities (uid, email, linked_at) VALUES (?, ?, ?)').run('AbC0129xyz', 'a@b.co', iso);
  assert.equal(s.rows('site_identities').length, 1);

  for (const badUid of ['', 'a'.repeat(129), 'uid-com-hifen', 'uid_underline', 'uid espaco']) {
    assert.throws(
      () => s.db.prepare('INSERT INTO site_identities (uid, email, linked_at) VALUES (?, ?, ?)').run(badUid, 'a@b.co', iso),
      /CHECK/,
      `uid inválido aceito: ${badUid}`,
    );
  }
  for (const badEmail of ['ab', 'Maiuscula@exemplo.com', 'a'.repeat(255)]) {
    assert.throws(
      () => s.db.prepare('INSERT INTO site_identities (uid, email, linked_at) VALUES (?, ?, ?)').run('uidOk1', badEmail, iso),
      /CHECK/,
      `email inválido aceito: ${badEmail}`,
    );
  }
  for (const badDate of ['2026-11-27T15:00:00Z', '2026-11-27 15:00:00.000Z', '2026-11-27T15:00:00.000+00:00']) {
    assert.throws(
      () => s.db.prepare('INSERT INTO site_identities (uid, email, linked_at) VALUES (?, ?, ?)').run('uidOk2', 'a@b.co', badDate),
      /CHECK/,
      `data inválida aceita: ${badDate}`,
    );
  }
  const idx = s.db
    .prepare("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'site_identities' AND name = 'site_identities_email'")
    .all();
  assert.equal(idx.length, 1);
});

test('CORS preflight OPTIONS: aceita https://guifav.dev sem credenciais e recusa outra origem', async () => {
  const s = setup();
  const ok = await s.run(idReq({ method: 'OPTIONS' }));
  assert.equal(ok.status, 204);
  assert.equal(ok.headers.get('access-control-allow-origin'), 'https://guifav.dev');
  assert.equal(ok.headers.get('access-control-allow-methods'), 'POST');
  assert.equal(ok.headers.get('access-control-allow-headers'), 'authorization, content-type');
  assert.equal(ok.headers.get('access-control-max-age'), '600');
  assert.equal(ok.headers.get('access-control-allow-credentials'), null);

  for (const badOrigin of ['https://evil.example', 'http://guifav.dev', 'https://guifav.dev.evil.com', null]) {
    const res = await s.run(idReq({ method: 'OPTIONS', origin: badOrigin }));
    assert.equal(res.status, 403, `origin=${String(badOrigin)}`);
    assert.equal(res.headers.get('access-control-allow-origin'), null);
  }
});

test('método diferente de POST/OPTIONS devolve 405; Origin errado no POST devolve 403', async () => {
  const s = setup();
  const getRes = await s.run(idReq({ method: 'GET' }));
  assert.equal(getRes.status, 405);
  assert.match(getRes.headers.get('allow') ?? '', /POST/);

  const token = await signJwt(validPayload());
  for (const badOrigin of ['https://outro.dev', 'http://localhost:4321', null]) {
    const res = await s.run(idReq({ origin: badOrigin, token }));
    assert.equal(res.status, 403);
    assert.equal(res.headers.get('access-control-allow-origin'), null);
  }
  assert.equal(s.jwksFetches(), 0);
  assert.deepEqual(s.rows('site_identities'), []);
});

test('limite por IP (RL_IP) devolve 429; corpo acima de 1 KiB devolve 413 e UTF-8 inválido 400', async () => {
  const token = await signJwt(validPayload());
  const limited = setup({ rlIp: false });
  const r429 = await limited.run(idReq({ token, ip: '203.0.113.10' }));
  assert.equal(r429.status, 429);
  assert.deepEqual(limited.rl, ['203.0.113.10']);
  assert.equal(limited.jwksFetches(), 0);

  // Corpo opcional com a origem da visita: acima de 1 KiB responde 413, UTF-8 inválido 400, sem buscar o JWKS.
  const s = setup();
  const r413 = await s.run(idReq({ token, body: 'origem_canal=busca&x=' + 'a'.repeat(1024) }));
  assert.equal(r413.status, 413);
  const r400 = await s.run(idReq({ token, body: new Uint8Array([0xff, 0xfe, 0xfd]) }));
  assert.equal(r400.status, 400);
  assert.equal(s.jwksFetches(), 0);
});

test('login vira conversão site_login comprovada, uma por pessoa, com a origem validada do corpo', async () => {
  const s = setup();
  const t1 = await signJwt(validPayload({ sub: 'UidLogin1', email: 'Pessoa@Dominio.com' }));
  const body = 'origem_canal=busca&origem_fonte=google&utm_source=news&utm_medium=email&utm_campaign=lanc-1&origem_entrada=/blog/x/';
  assert.equal((await s.run(idReq({ token: t1, body }))).status, 204);
  const convs = s.rows('conversions');
  assert.equal(convs.length, 1);
  const c = convs[0] as Record<string, unknown>;
  assert.equal(c.id, await loginConversionId('pessoa@dominio.com'));
  assert.deepEqual(
    [c.email, c.action, c.material, c.interests, c.verified, c.origin_channel, c.origin_source, c.utm_source, c.utm_medium, c.utm_campaign, c.landing_path, c.synced_at],
    ['pessoa@dominio.com', 'site_login', null, '[]', 1, 'busca', 'google', 'news', 'email', 'lanc-1', '/blog/x/', null],
  );
  assert.equal(c.occurred_at, new Date(NOW).toISOString());

  // Outro login da mesma pessoa (outro uid, outra origem): nenhuma conversão nova, a primeira fica como estava.
  const t2 = await signJwt(validPayload({ sub: 'UidLogin2', email: 'pessoa@dominio.com' }));
  assert.equal((await s.run(idReq({ token: t2, body: 'origem_canal=direto' }))).status, 204);
  assert.equal(s.rows('conversions').length, 1);
  assert.equal((s.rows('conversions')[0] as Record<string, unknown>).origin_channel, 'busca');

  // Sem corpo ou com campos fora do padrão: canal "desconhecida", campo inválido some sem levar os outros.
  const t3 = await signJwt(validPayload({ sub: 'UidLogin3', email: 'outra@dominio.com' }));
  assert.equal((await s.run(idReq({ token: t3, body: 'origem_canal=hacker&utm_source=OK!&utm_medium=social' }))).status, 204);
  const c3 = s.rows('conversions').find((r) => (r as Record<string, unknown>).email === 'outra@dominio.com') as Record<string, unknown>;
  assert.deepEqual([c3.origin_channel, c3.origin_source, c3.utm_source, c3.utm_medium], ['desconhecida', null, null, 'social']);
  const t4 = await signJwt(validPayload({ sub: 'UidLogin4', email: 'semcorpo@dominio.com' }));
  assert.equal((await s.run(idReq({ token: t4 }))).status, 204);
  const c4 = s.rows('conversions').find((r) => (r as Record<string, unknown>).email === 'semcorpo@dominio.com') as Record<string, unknown>;
  assert.equal(c4.origin_channel, 'desconhecida');
});

test('login com oposição não grava conversão; falha ao gravar a conversão responde 500 com log curto', async () => {
  const s = setup();
  s.db.prepare('INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)')
    .run(await heatingOptoutHmac('leitor@exemplo.com', FORGET_KEY), new Date(NOW).toISOString());
  const tok = await signJwt(validPayload({ sub: 'UidOposto1', email: 'leitor@exemplo.com' }));
  assert.equal((await s.run(idReq({ token: tok, body: 'origem_canal=busca' }))).status, 409);
  assert.deepEqual(s.rows('conversions'), []);

  const f = setup({ d1Falha: /INSERT INTO conversions/ });
  const tf = await signJwt(validPayload({ sub: 'UidFalha1', email: 'falha@exemplo.com' }));
  let res!: Response;
  const logs = await capturaErros(async () => { res = await f.run(idReq({ token: tf })); });
  assert.equal(res.status, 500);
  assert.equal(logs.length, 1);
  assert.match(logs[0], /^conversão de login não gravada: /);
  assert.ok(!logs[0].includes('falha@exemplo.com'), 'log sem email');
  assert.equal(f.rows('site_identities').length, 1, 'o vínculo fica gravado; a repetição é idempotente');
});

test('POST válido grava uid -> email normalizado, faz upsert no mesmo uid e aceita outro uid com mesmo email', async () => {
  const s = setup();
  const t1 = await signJwt(validPayload({ sub: 'UidPrimeira1', email: '  Pessoa@Dominio.COM  ' }));
  const r1 = await s.run(idReq({ token: t1, ip: '203.0.113.1' }));
  assert.equal(r1.status, 204);
  assert.equal(await r1.text(), '');
  assert.equal(r1.headers.get('access-control-allow-origin'), 'https://guifav.dev');
  assert.equal(r1.headers.get('access-control-allow-credentials'), null);

  assert.deepEqual(s.rows('site_identities'), [
    { uid: 'UidPrimeira1', email: 'pessoa@dominio.com', linked_at: new Date(NOW).toISOString() },
  ]);

  // Mesmo uid mais tarde: atualiza linked_at e email.
  s.avanca(60_000);
  const t1b = await signJwt(validPayload({ sub: 'UidPrimeira1', email: 'novo@dominio.com' }));
  assert.equal((await s.run(idReq({ token: t1b }))).status, 204);
  assert.deepEqual(s.rows('site_identities'), [
    { uid: 'UidPrimeira1', email: 'novo@dominio.com', linked_at: new Date(NOW + 60_000).toISOString() },
  ]);

  // Mesmo email com outro uid: grava linha nova (uid é a chave).
  const t2 = await signJwt(validPayload({ sub: 'UidSegunda2', email: 'novo@dominio.com' }));
  assert.equal((await s.run(idReq({ token: t2 }))).status, 204);
  assert.deepEqual(s.rows('site_identities'), [
    { uid: 'UidPrimeira1', email: 'novo@dominio.com', linked_at: new Date(NOW + 60_000).toISOString() },
    { uid: 'UidSegunda2', email: 'novo@dominio.com', linked_at: new Date(NOW + 60_000).toISOString() },
  ]);
});

test('oposição ao heating (heating_optout): responde 409 com CORS, log curto e não grava em site_identities', async () => {
  const s = setup();
  const hmac = await heatingOptoutHmac('leitor@exemplo.com', FORGET_KEY);
  s.db.prepare('INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)').run(hmac, new Date(NOW).toISOString());

  const token = await signJwt(validPayload({ sub: 'UidOptOut1', email: 'LEITOR@exemplo.com' }));
  let res: Response | undefined;
  const logs = await capturaErros(async () => {
    res = await s.run(idReq({ token }));
  });
  assert.equal(res!.status, 409);
  assert.equal(res!.headers.get('access-control-allow-origin'), 'https://guifav.dev');
  assert.equal(await res!.text(), '');
  assert.deepEqual(logs, ['identidade recusada: oposicao']);
  assert.deepEqual(s.rows('site_identities'), []);

  // Vínculo gravado antes da oposição: a nova chamada não atualiza a linha (o job a apaga) e também recebe 409.
  const t = setup();
  const tok = await signJwt(validPayload({ sub: 'UidAntes1', email: 'antes@exemplo.com' }));
  assert.equal((await t.run(idReq({ token: tok }))).status, 204);
  const antes = t.rows('site_identities');
  t.db.prepare('INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)')
    .run(await heatingOptoutHmac('antes@exemplo.com', FORGET_KEY), new Date(NOW).toISOString());
  assert.equal((await t.run(idReq({ token: tok }))).status, 409);
  assert.deepEqual(t.rows('site_identities'), antes);
});

test('regras do ID token: rejeita cada falha com 401 sem detalhe e log curto com motivo exato sem email nem token', async () => {
  const s = setup();
  const cases: Array<[string, string, () => Promise<Request>]> = [
    ['sem Authorization', 'authorization_ausente', async () => idReq({ token: null })],
    ['Authorization sem Bearer', 'authorization_ausente', async () => idReq({ authHeader: 'Basic abc' })],
    ['token malformado', 'token_invalido', async () => idReq({ token: 'nao-e-jwt' })],
    ['alg none com assinatura RS256 válida', 'alg_invalido', async () => idReq({ token: await signJwt(validPayload(), { header: { alg: 'none', typ: 'JWT', kid: KEY1.kid } }) })],
    ['alg HS256 com assinatura RS256 válida', 'alg_invalido', async () => idReq({ token: await signJwt(validPayload(), { header: { alg: 'HS256', typ: 'JWT', kid: KEY1.kid } }) })],
    ['alg RS512 com assinatura RS256 válida', 'alg_invalido', async () => idReq({ token: await signJwt(validPayload(), { header: { alg: 'RS512', typ: 'JWT', kid: KEY1.kid } }) })],
    ['kid ausente', 'kid_ausente', async () => idReq({ token: await signJwt(validPayload(), { header: { alg: 'RS256' } }) })],
    ['kid desconhecido', 'kid_desconhecido', async () => idReq({ token: await signJwt(validPayload(), { header: { alg: 'RS256', kid: 'desconhecido' } }) })],
    ['assinatura de outra chave', 'assinatura_invalida', async () => idReq({ token: await signJwt(validPayload(), { privateKey: KEY2.privateKey }) })],
    ['tenant errado', 'tenant_invalido', async () => idReq({ token: await signJwt(validPayload({ firebase: { tenant: 'outro-app' } })) })],
    ['tenant ausente', 'tenant_invalido', async () => idReq({ token: await signJwt(validPayload({ firebase: { sign_in_provider: 'google.com' } })) })],
    ['campo firebase ausente', 'tenant_invalido', async () => {
      const p = validPayload();
      delete p.firebase;
      return idReq({ token: await signJwt(p) });
    }],
    ['email não verificado', 'email_nao_verificado', async () => idReq({ token: await signJwt(validPayload({ email_verified: false })) })],
    ['email_verified ausente', 'email_nao_verificado', async () => {
      const p = validPayload();
      delete p.email_verified;
      return idReq({ token: await signJwt(p) });
    }],
    ['email inválido', 'email_invalido', async () => idReq({ token: await signJwt(validPayload({ email: 'invalido@' })) })],
    ['iss errado', 'iss_invalido', async () => idReq({ token: await signJwt(validPayload({ iss: 'https://securetoken.google.com/outro' })) })],
    ['aud errado', 'aud_invalido', async () => idReq({ token: await signJwt(validPayload({ aud: 'outro-projeto' })) })],
    ['exp no instante atual (folga zero)', 'expirado', async () => idReq({ token: await signJwt(validPayload({ exp: NOW_SEC })) })],
    ['exp expirado há 1 s (folga zero)', 'expirado', async () => idReq({ token: await signJwt(validPayload({ exp: NOW_SEC - 1 })) })],
    ['iat no futuro (> 5 min)', 'iat_futuro', async () => idReq({ token: await signJwt(validPayload({ iat: NOW_SEC + 301 })) })],
    ['sub com caracteres inválidos', 'sub_invalido', async () => idReq({ token: await signJwt(validPayload({ sub: 'uid-com-traco' })) })],
    ['sub longo demais', 'sub_invalido', async () => idReq({ token: await signJwt(validPayload({ sub: 'a'.repeat(129) })) })],
  ];

  for (const [label, expectedReason, makeReq] of cases) {
    const req = await makeReq();
    const rawAuth = req.headers.get('authorization') ?? '';
    const fetchesBefore = s.jwksFetches();
    let res!: Response;
    const logs = await capturaErros(async () => {
      res = await s.run(req);
    });
    assert.equal(res.status, 401, `${label}: esperado 401`);
    assert.equal(await res.text(), '', `${label}: 401 não deve expor detalhe`);
    assert.equal(logs.length, 1, `${label}: esperado 1 log curto`);
    const line = logs[0];
    assert.equal(line, `identidade recusada: ${expectedReason}`, `${label}: motivo inesperado no log`);
    if (expectedReason === 'alg_invalido') {
      assert.equal(s.jwksFetches(), fetchesBefore, `${label}: alg inválido não deve buscar JWKS`);
    }
    assert.ok(!line.toLowerCase().includes('leitor@exemplo.com'), `${label}: log vazou email`);
    assert.ok(!line.includes('@'), `${label}: log contém @`);
    if (rawAuth.startsWith('Bearer ')) {
      assert.ok(!line.includes(rawAuth.slice(7)), `${label}: log vazou token`);
    }
  }
  assert.deepEqual(s.rows('site_identities'), []);
});

test('JWKS ignora chaves com alg diferente de RS256', async () => {
  const s = setup({ jwksKeys: [{ ...KEY1.jwk, alg: 'RS512' }, KEY2.jwk] });
  const tok = await signJwt(validPayload());
  let res!: Response;
  const logs = await capturaErros(async () => {
    res = await s.run(idReq({ token: tok }));
  });
  assert.equal(res.status, 401);
  assert.equal(logs[0], 'identidade recusada: kid_desconhecido');
  assert.deepEqual(s.rows('site_identities'), []);
});

test('exp exige folga zero e iat mantém tolerância de 5 min', async () => {
  const s = setup();
  const tExpOk = await signJwt(validPayload({ sub: 'UidExpOk1', exp: NOW_SEC + 1, iat: NOW_SEC - 60 }));
  assert.equal((await s.run(idReq({ token: tExpOk }))).status, 204);

  const tExpZero = await signJwt(validPayload({ sub: 'UidExpZero', exp: NOW_SEC - 1, iat: NOW_SEC - 60 }));
  const logs = await capturaErros(async () => {
    assert.equal((await s.run(idReq({ token: tExpZero }))).status, 401);
  });
  assert.equal(logs[0], 'identidade recusada: expirado');

  const tIat = await signJwt(validPayload({ sub: 'UidSkewIat2', iat: NOW_SEC + 300, exp: NOW_SEC + 3600 }));
  assert.equal((await s.run(idReq({ token: tIat }))).status, 204);
  assert.equal(s.rows('site_identities').length, 2);
});

test('cache do JWKS: respeita max-age do Cache-Control e renova ao vencer; timeout e HTTP 500 dão 401', async () => {
  const s = setup({ jwksCacheControl: 'public, max-age=120' });
  const tok = await signJwt(validPayload());

  assert.equal((await s.run(idReq({ token: tok }))).status, 204);
  assert.equal(s.jwksFetches(), 1);

  s.avanca(60_000);
  assert.equal((await s.run(idReq({ token: tok }))).status, 204);
  assert.equal(s.jwksFetches(), 1, 'dentro do max-age usa o cache');

  s.avanca(61_000);
  assert.equal((await s.run(idReq({ token: tok }))).status, 204);
  assert.equal(s.jwksFetches(), 2, 'depois do max-age busca de novo');

  // Sem max-age não cacheia.
  const noCache = setup({ jwksCacheControl: 'no-store' });
  assert.equal((await noCache.run(idReq({ token: tok }))).status, 204);
  assert.equal((await noCache.run(idReq({ token: tok }))).status, 204);
  assert.equal(noCache.jwksFetches(), 2);

  // Timeout na busca do JWKS -> 401 sem detalhe.
  const slow = setup({ jwksDelayMs: 200, jwksTimeoutMs: 25 });
  const logsSlow = await capturaErros(async () => {
    const r = await slow.run(idReq({ token: tok }));
    assert.equal(r.status, 401);
    assert.equal(await r.text(), '');
  });
  assert.equal(logsSlow.length, 1);

  // Erro HTTP do JWKS -> 401 sem detalhe.
  const broken = setup({ jwksStatus: 503 });
  const logsBroken = await capturaErros(async () => {
    const r = await broken.run(idReq({ token: tok }));
    assert.equal(r.status, 401);
  });
  assert.equal(logsBroken.length, 1);
});

test('rotacao de chaves: kid fora do cache forca uma busca nova do JWKS, limitada a uma por minuto', async () => {
  // O Google anuncia max-age de horas; sem a busca forcada, a chave nova seria recusada ate o cache vencer.
  const s = setup({ jwksCacheControl: 'public, max-age=19962' });
  assert.equal((await s.run(idReq({ token: await signJwt(validPayload()) }))).status, 204);
  assert.equal(s.jwksFetches(), 1);

  // O Google gira: o JWKS passa a ter a chave nova (KEY2) e o token novo vem assinado por ela.
  s.trocaJwks([KEY1.jwk, KEY2.jwk]);
  s.avanca(10 * 60_000);
  const tokNovo = await signJwt(validPayload(), { header: { alg: 'RS256', typ: 'JWT', kid: KEY2.kid }, privateKey: KEY2.privateKey });
  assert.equal((await s.run(idReq({ token: tokNovo }))).status, 204, 'token da chave nova aceito com o cache ainda valido');
  assert.equal(s.jwksFetches(), 2, 'uma busca forcada pelo kid novo');

  // kid inventado logo depois: recusado sem nova busca (limite de uma por minuto).
  const tokFalso = await signJwt(validPayload(), { header: { alg: 'RS256', typ: 'JWT', kid: 'kid-inventado' } });
  const logs = await capturaErros(async () => {
    assert.equal((await s.run(idReq({ token: tokFalso }))).status, 401);
    assert.equal((await s.run(idReq({ token: tokFalso }))).status, 401);
  });
  assert.equal(s.jwksFetches(), 2, 'kid desconhecido dentro do minuto nao busca de novo');
  assert.ok(logs.every((l) => l === 'identidade recusada: kid_desconhecido'));

  // Passado o minuto, um kid desconhecido pode forcar uma busca de novo.
  s.avanca(61_000);
  await capturaErros(async () => {
    assert.equal((await s.run(idReq({ token: tokFalso }))).status, 401);
  });
  assert.equal(s.jwksFetches(), 3);
});

test('falha do D1 responde 500 e remove qualquer email do log', async () => {
  const s = setup({
    d1Falha: /site_identities/,
    d1Mensagem: 'D1_ERROR: disk full ao gravar leitor@exemplo.com',
  });
  const token = await signJwt(validPayload());
  let res!: Response;
  const logs = await capturaErros(async () => {
    res = await s.run(idReq({ token }));
  });
  assert.equal(res.status, 500);
  assert.equal(res.headers.get('access-control-allow-origin'), 'https://guifav.dev');
  assert.equal(logs.length, 1);
  assert.ok(!logs[0].includes('leitor@exemplo.com'));
  assert.ok(logs[0].includes('<email>'));
});

test('D1 sem contagem de linhas (sem meta.changes): 500 com log curto, nunca 204', async () => {
  const s = setup();
  const real = s.env.DB;
  s.env.DB = {
    prepare: (sql: string) => {
      const st = real.prepare(sql);
      const wrap = (b: typeof st): typeof st => ({
        ...b,
        bind: (...v: unknown[]) => wrap(b.bind(...v)),
        run: async () => {
          await b.run();
          return { success: true } as Awaited<ReturnType<typeof b.run>>;
        },
      });
      return wrap(st);
    },
  } as typeof real;
  const token = await signJwt(validPayload({ sub: 'UidSemMeta1' }));
  let res!: Response;
  const logs = await capturaErros(async () => {
    res = await s.run(idReq({ token }));
  });
  assert.equal(res.status, 500);
  assert.deepEqual(logs, ['identidade sem contagem de linhas do D1']);
});

test('README: o contrato de POST /identidade documenta 409 para quem tem oposição (o auth.js depende desse status)', () => {
  const readme = readFileSync(new URL('../README.md', import.meta.url), 'utf-8');
  const row = readme.split('\n').find((l) => l.startsWith('| `POST /identidade` |'));
  assert.ok(row, 'linha de POST /identidade no README');
  assert.match(row!, /oposição em `heating_optout`[^|]*responde `409`/);
  assert.doesNotMatch(row!, /oposição[^|;]*não grava e responde `204`/);
});

test('oposição gravada entre o vínculo e a conversão (corrida com --forget): a guarda do INSERT não grava a conversão', async () => {
  const s = setup();
  const hmac = await heatingOptoutHmac('corrida@exemplo.com', FORGET_KEY);
  const real = s.env.DB;
  s.env.DB = {
    prepare: (sql: string) => {
      if (/INSERT INTO conversions/.test(sql)) {
        // O --forget (ou o POST /prioridade) grava a oposição logo depois do upsert do vínculo.
        s.db.prepare('INSERT OR IGNORE INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)').run(hmac, new Date(NOW).toISOString());
      }
      return real.prepare(sql);
    },
  } as typeof real;
  const tok = await signJwt(validPayload({ sub: 'UidCorrida1', email: 'corrida@exemplo.com' }));
  const res = await s.run(idReq({ token: tok, body: 'origem_canal=busca' }));
  assert.equal(res.status, 204);
  assert.deepEqual(s.rows('conversions'), []);
});

test('D1 sem contagem de linhas só na conversão de login: 500 com log curto (Kimi P2, rodada 8 do #206)', async () => {
  const s = setup();
  const real = s.env.DB;
  s.env.DB = {
    prepare: (sql: string) => {
      const st = real.prepare(sql);
      if (!/INSERT INTO conversions/.test(sql)) return st;
      const wrap = (b: typeof st): typeof st => ({
        ...b,
        bind: (...v: unknown[]) => wrap(b.bind(...v)),
        run: async () => {
          await b.run();
          return { success: true } as Awaited<ReturnType<typeof b.run>>;
        },
      });
      return wrap(st);
    },
  } as typeof real;
  const token = await signJwt(validPayload({ sub: 'UidSemMetaConv1' }));
  let res!: Response;
  const logs = await capturaErros(async () => {
    res = await s.run(idReq({ token }));
  });
  assert.equal(res.status, 500);
  assert.deepEqual(logs, ['conversão de login sem contagem de linhas do D1']);
});

test('origem desconhecida do primeiro login é trocada pela conhecida antes do sync, nunca o contrário (Kimi P2, rodada 9 do #206)', async () => {
  const s = setup();
  // auth.js antigo em cache (sem corpo) durante a publicação: a conversão nasce com origem desconhecida.
  const t1 = await signJwt(validPayload({ sub: 'UidRollout1', email: 'rollout@exemplo.com' }));
  assert.equal((await s.run(idReq({ token: t1 }))).status, 204);
  const conv = () => s.rows('conversions').find((r) => (r as Record<string, unknown>).email === 'rollout@exemplo.com') as Record<string, unknown>;
  assert.equal(conv().origin_channel, 'desconhecida');
  const primeira = conv().occurred_at;
  // O próximo login, já com o auth.js novo, traz a origem: ela substitui a desconhecida, sem mudar a data.
  s.avanca(60_000);
  const t2 = await signJwt(validPayload({ sub: 'UidRollout1', email: 'rollout@exemplo.com' }));
  assert.equal((await s.run(idReq({ token: t2, body: 'origem_canal=busca&origem_fonte=google&origem_entrada=/blog/x/' }))).status, 204);
  assert.deepEqual([conv().origin_channel, conv().origin_source, conv().landing_path, conv().occurred_at], ['busca', 'google', '/blog/x/', primeira]);
  assert.equal(s.rows('conversions').length, 1);
  // Origem conhecida não é sobrescrita por outra, nem por uma desconhecida.
  const t3 = await signJwt(validPayload({ sub: 'UidRollout2', email: 'rollout@exemplo.com' }));
  assert.equal((await s.run(idReq({ token: t3, body: 'origem_canal=social&origem_fonte=linkedin' }))).status, 204);
  assert.equal((await s.run(idReq({ token: t3 }))).status, 204);
  assert.equal(conv().origin_channel, 'busca');
  // Depois do sync (synced_at preenchido), nem a desconhecida é trocada: o Twenty já recebeu a conversão.
  const t4 = await signJwt(validPayload({ sub: 'UidSync1', email: 'sync@exemplo.com' }));
  assert.equal((await s.run(idReq({ token: t4 }))).status, 204);
  s.db.prepare("UPDATE conversions SET synced_at = ? WHERE email = 'sync@exemplo.com'").run(new Date(NOW).toISOString());
  assert.equal((await s.run(idReq({ token: t4, body: 'origem_canal=busca' }))).status, 204);
  const cs = s.rows('conversions').find((r) => (r as Record<string, unknown>).email === 'sync@exemplo.com') as Record<string, unknown>;
  assert.equal(cs.origin_channel, 'desconhecida');
  // Desconhecida não troca desconhecida (nem os UTM que vieram com ela).
  const t5 = await signJwt(validPayload({ sub: 'UidUtm1', email: 'utm@exemplo.com' }));
  assert.equal((await s.run(idReq({ token: t5, body: 'utm_source=primeira' }))).status, 204);
  assert.equal((await s.run(idReq({ token: t5, body: 'utm_source=segunda' }))).status, 204);
  const cu = s.rows('conversions').find((r) => (r as Record<string, unknown>).email === 'utm@exemplo.com') as Record<string, unknown>;
  assert.deepEqual([cu.origin_channel, cu.utm_source], ['desconhecida', 'primeira']);
});
