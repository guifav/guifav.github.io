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
      return new Response(JSON.stringify({ keys: opts.jwksKeys ?? [KEY1.jwk] }), { status, headers });
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
    avanca: (ms: number) => void (clock += ms),
    rows: (t: string) => rows(db.raw, t),
  };
}

function idReq(opts: {
  method?: string;
  origin?: string | null;
  token?: string | null;
  authHeader?: string;
  body?: string;
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

test('limite por IP (RL_IP) devolve 429; corpo não vazio devolve 400', async () => {
  const token = await signJwt(validPayload());
  const limited = setup({ rlIp: false });
  const r429 = await limited.run(idReq({ token, ip: '203.0.113.10' }));
  assert.equal(r429.status, 429);
  assert.deepEqual(limited.rl, ['203.0.113.10']);
  assert.equal(limited.jwksFetches(), 0);

  const s = setup();
  const r400 = await s.run(idReq({ token, body: '{"a":1}' }));
  assert.equal(r400.status, 400);
  assert.equal(s.jwksFetches(), 0);
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

test('oposição ao heating (heating_optout): responde 204 e não grava em site_identities', async () => {
  const s = setup();
  const hmac = await heatingOptoutHmac('leitor@exemplo.com', FORGET_KEY);
  s.db.prepare('INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)').run(hmac, new Date(NOW).toISOString());

  const token = await signJwt(validPayload({ sub: 'UidOptOut1', email: 'LEITOR@exemplo.com' }));
  const res = await s.run(idReq({ token }));
  assert.equal(res.status, 204);
  assert.deepEqual(s.rows('site_identities'), []);
});

test('regras do ID token: rejeita cada falha com 401 sem detalhe e log curto sem email nem token', async () => {
  const s = setup();
  const cases: Array<[string, () => Promise<Request>]> = [
    ['sem Authorization', async () => idReq({ token: null })],
    ['Authorization sem Bearer', async () => idReq({ authHeader: 'Basic abc' })],
    ['token malformado', async () => idReq({ token: 'nao-e-jwt' })],
    ['alg none', async () => {
      const h = b64urlJson({ alg: 'none', kid: KEY1.kid });
      const p = b64urlJson(validPayload());
      return idReq({ token: `${h}.${p}.AAAA` });
    }],
    ['alg HS256', async () => {
      const h = b64urlJson({ alg: 'HS256', kid: KEY1.kid });
      const p = b64urlJson(validPayload());
      return idReq({ token: `${h}.${p}.AAAA` });
    }],
    ['kid ausente', async () => idReq({ token: await signJwt(validPayload(), { header: { alg: 'RS256' } }) })],
    ['kid desconhecido', async () => idReq({ token: await signJwt(validPayload(), { header: { alg: 'RS256', kid: 'desconhecido' } }) })],
    ['assinatura de outra chave', async () => idReq({ token: await signJwt(validPayload(), { privateKey: KEY2.privateKey }) })],
    ['tenant errado', async () => idReq({ token: await signJwt(validPayload({ firebase: { tenant: 'outro-app' } })) })],
    ['tenant ausente', async () => idReq({ token: await signJwt(validPayload({ firebase: { sign_in_provider: 'google.com' } })) })],
    ['campo firebase ausente', async () => {
      const p = validPayload();
      delete p.firebase;
      return idReq({ token: await signJwt(p) });
    }],
    ['email não verificado', async () => idReq({ token: await signJwt(validPayload({ email_verified: false })) })],
    ['email_verified ausente', async () => {
      const p = validPayload();
      delete p.email_verified;
      return idReq({ token: await signJwt(p) });
    }],
    ['email inválido', async () => idReq({ token: await signJwt(validPayload({ email: 'invalido@' })) })],
    ['iss errado', async () => idReq({ token: await signJwt(validPayload({ iss: 'https://securetoken.google.com/outro' })) })],
    ['aud errado', async () => idReq({ token: await signJwt(validPayload({ aud: 'outro-projeto' })) })],
    ['exp expirado (> 5 min)', async () => idReq({ token: await signJwt(validPayload({ exp: NOW_SEC - 301 })) })],
    ['iat no futuro (> 5 min)', async () => idReq({ token: await signJwt(validPayload({ iat: NOW_SEC + 301 })) })],
    ['sub com caracteres inválidos', async () => idReq({ token: await signJwt(validPayload({ sub: 'uid-com-traco' })) })],
    ['sub longo demais', async () => idReq({ token: await signJwt(validPayload({ sub: 'a'.repeat(129) })) })],
  ];

  for (const [label, makeReq] of cases) {
    const req = await makeReq();
    const rawAuth = req.headers.get('authorization') ?? '';
    let res!: Response;
    const logs = await capturaErros(async () => {
      res = await s.run(req);
    });
    assert.equal(res.status, 401, `${label}: esperado 401`);
    assert.equal(await res.text(), '', `${label}: 401 não deve expor detalhe`);
    assert.equal(logs.length, 1, `${label}: esperado 1 log curto`);
    const line = logs[0];
    assert.ok(!line.toLowerCase().includes('leitor@exemplo.com'), `${label}: log vazou email`);
    assert.ok(!line.includes('@'), `${label}: log contém @`);
    if (rawAuth.startsWith('Bearer ')) {
      assert.ok(!line.includes(rawAuth.slice(7)), `${label}: log vazou token`);
    }
  }
  assert.deepEqual(s.rows('site_identities'), []);
});

test('tolerância de 5 min em exp e iat aceita desvios dentro do limite', async () => {
  const s = setup();
  const tExp = await signJwt(validPayload({ sub: 'UidSkewExp1', exp: NOW_SEC - 120, iat: NOW_SEC - 600 }));
  assert.equal((await s.run(idReq({ token: tExp }))).status, 204);

  const tIat = await signJwt(validPayload({ sub: 'UidSkewIat2', iat: NOW_SEC + 120, exp: NOW_SEC + 3600 }));
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
