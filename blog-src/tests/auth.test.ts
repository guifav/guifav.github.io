// Testes do /assets/auth.js e do carregamento tardio pelo /assets/track.js (épico #197, corte 2).
// O script roda num contexto vm com DOM, storage e SDKs falsos de Firebase/GIS.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const AUTH_SRC = readFileSync(new URL('../../assets/auth.js', import.meta.url), 'utf8');
const TRACK_SRC = readFileSync(new URL('../../assets/track.js', import.meta.url), 'utf8');
const DAY = 24 * 3600 * 1000;
const NOW = Date.UTC(2026, 10, 27, 15, 0, 0);
const TENANT_ID = 'guifav-dev-g6dp4';
const CLIENT_ID = '486000748530-fj4un9nf6q94dcfjkttbrb87v026eboi.apps.googleusercontent.com';
const NOTICE_TEXT =
  'Ao entrar, sua atividade neste site fica ligada ao seu contato: páginas vistas, tempo de leitura, rolagem e cliques em Agende e Contato. Uso para saber quem abordar primeiro. Você pode desligar quando quiser no menu da conta.';

assert.ok(AUTH_SRC.includes('https://www.gstatic.com/firebasejs/12.12.1/firebase-app.js'));
assert.ok(AUTH_SRC.includes('https://www.gstatic.com/firebasejs/12.12.1/firebase-auth.js'));
assert.ok(AUTH_SRC.includes('import(FIREBASE_APP_URL)'));
assert.ok(AUTH_SRC.includes('import(FIREBASE_AUTH_URL)'));

// No Node sem --experimental-vm-modules, dynamic import() em vm.Script lança ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING_FLAG.
// O harness substitui somente os dois imports dinâmicos já verificados acima por um carregador injetado no contexto.
const AUTH_TEST_SRC = AUTH_SRC
  .replace('import(FIREBASE_APP_URL)', '__loadModule(FIREBASE_APP_URL)')
  .replace('import(FIREBASE_AUTH_URL)', '__loadModule(FIREBASE_AUTH_URL)');

class El {
  tag: string;
  attrs: Record<string, string> = {};
  children: El[] = [];
  parent: El | null = null;
  textContent = '';
  hidden = false;
  async = false;
  focused = false;
  handlers: Record<string, Array<{ fn: (e: unknown) => void; once?: boolean }>> = {};
  constructor(tag: string) {
    this.tag = tag;
  }
  setAttribute(k: string, v: string) {
    this.attrs[k] = String(v);
  }
  getAttribute(k: string) {
    return k in this.attrs ? this.attrs[k] : null;
  }
  removeAttribute(k: string) {
    delete this.attrs[k];
  }
  appendChild(c: El) {
    c.parent = this;
    this.children.push(c);
    return c;
  }
  contains(node: unknown): boolean {
    if (node === this) return true;
    return this.children.some((c) => c.contains(node));
  }
  focus() {
    this.focused = true;
  }
  addEventListener(type: string, fn: (e: unknown) => void, opts?: { once?: boolean }) {
    (this.handlers[type] ??= []).push({ fn, once: opts?.once });
  }
  emit(type: string, ev: unknown = {}) {
    const list = [...(this.handlers[type] ?? [])];
    for (const h of list) {
      if (h.once) {
        this.handlers[type] = (this.handlers[type] ?? []).filter((x) => x !== h);
      }
      h.fn(ev);
    }
  }
  findAll(pred: (e: El) => boolean, out: El[] = []): El[] {
    if (pred(this)) out.push(this);
    for (const c of this.children) c.findAll(pred, out);
    return out;
  }
}

class Storage {
  m = new Map<string, string>();
  getItem(k: string) {
    return this.m.has(k) ? this.m.get(k)! : null;
  }
  setItem(k: string, v: string) {
    this.m.set(k, String(v));
  }
  removeItem(k: string) {
    this.m.delete(k);
  }
}

interface FakeUser {
  uid: string;
  tenantId: string | null;
  displayName?: string | null;
  email?: string | null;
  getIdToken?: () => Promise<string>;
}

interface SetupOpts {
  hostname?: string;
  pathname?: string;
  title?: string;
  consent?: unknown;
  heatingLink?: unknown;
  local?: Storage;
  session?: Storage;
  initialUser?: FakeUser | null;
  firebaseFail?: boolean;
  gisAvailable?: boolean;
  identityStatus?: number;
  identityThrow?: boolean;
}

async function flush() {
  for (let i = 0; i < 10; i++) {
    await new Promise((r) => setImmediate(r));
  }
}

async function setupAuth(opts: SetupOpts = {}) {
  const gtagCalls: unknown[][] = [];
  const clarityCalls: unknown[][] = [];
  const fetchCalls: Array<{ url: string; init?: RequestInit }> = [];
  const warns: string[] = [];
  const oneTapInits: Record<string, unknown>[] = [];
  const oneTapPrompts: number[] = [];
  const oneTapCancels: number[] = [];
  const signInCredCalls: Array<{ tenantId: string | null; cred: unknown }> = [];
  const signInPopupCalls: Array<{ tenantId: string | null }> = [];
  let signOutCalls = 0;
  let initializedConfig: Record<string, unknown> | null = null;
  let authListener: ((u: FakeUser | null) => void) | null = null;
  let promptCb: ((n: Record<string, unknown>) => void) | null = null;

  const html = new El('html');
  const head = html.appendChild(new El('head'));
  const body = html.appendChild(new El('body'));
  const docHandlers: Record<string, Array<(e: unknown) => void>> = {};
  const winHandlers: Record<string, Array<(e: unknown) => void>> = {};

  const local = opts.local ?? new Storage();
  if (opts.consent !== undefined) {
    local.setItem('consent-v1', typeof opts.consent === 'string' ? opts.consent : JSON.stringify(opts.consent));
  }
  if (opts.heatingLink !== undefined) {
    local.setItem('heating-link-v1', typeof opts.heatingLink === 'string' ? opts.heatingLink : JSON.stringify(opts.heatingLink));
  }
  const session = opts.session ?? new Storage();

  const authObj: { tenantId: string | null } = { tenantId: null };
  const fakeModules: Record<string, unknown> = {
    'https://www.gstatic.com/firebasejs/12.12.1/firebase-app.js': {
      getApps: () => [],
      getApp: () => ({ name: '[DEFAULT]' }),
      initializeApp: (cfg: Record<string, unknown>) => {
        initializedConfig = cfg;
        return { name: '[DEFAULT]', cfg };
      },
    },
    'https://www.gstatic.com/firebasejs/12.12.1/firebase-auth.js': {
      getAuth: () => authObj,
      onAuthStateChanged: (_a: unknown, cb: (u: FakeUser | null) => void) => {
        authListener = cb;
        if ('initialUser' in opts) {
          Promise.resolve().then(() => cb(opts.initialUser ?? null));
        }
        return () => {};
      },
      GoogleAuthProvider: Object.assign(
        function GoogleAuthProvider(this: { params?: Record<string, string> }) {
          this.params = {};
          return {
            setCustomParameters: (p: Record<string, string>) => {
              this.params = p;
            },
          };
        },
        {
          credential: (idToken: string) => ({ providerId: 'google.com', idToken }),
        },
      ),
      signInWithCredential: async (a: { tenantId: string | null }, cred: unknown) => {
        signInCredCalls.push({ tenantId: a.tenantId, cred });
        return { user: opts.initialUser };
      },
      signInWithPopup: async (a: { tenantId: string | null }) => {
        signInPopupCalls.push({ tenantId: a.tenantId });
        return { user: opts.initialUser };
      },
      signOut: async () => {
        signOutCalls += 1;
        authListener?.(null);
      },
    },
  };

  const document = {
    documentElement: html,
    head,
    body,
    title: opts.title ?? 'Guilherme Favaron | Builder e CTO',
    createElement: (t: string) => new El(t),
    getElementById: (id: string) => html.findAll((e) => e.attrs.id === id)[0] ?? null,
    querySelector: (sel: string) => {
      const m = sel.match(/^script\[src="([^"]+)"\]$/);
      if (m) return html.findAll((e) => e.tag === 'script' && e.attrs.src === m[1])[0] ?? null;
      return null;
    },
    addEventListener: (t: string, fn: (e: unknown) => void) => {
      (docHandlers[t] ??= []).push(fn);
    },
  };

  const ctx: Record<string, unknown> = {
    document,
    localStorage: local,
    sessionStorage: session,
    location: {
      hostname: opts.hostname ?? 'guifav.dev',
      pathname: opts.pathname ?? '/',
    },
    JSON,
    Promise,
    Date: { now: () => NOW },
    console: { warn: (...a: unknown[]) => warns.push(a.map(String).join(' ')) },
    gtag: (...a: unknown[]) => gtagCalls.push(JSON.parse(JSON.stringify(a))),
    clarity: (...a: unknown[]) => clarityCalls.push(JSON.parse(JSON.stringify(a))),
    fetch: async (url: string, init?: RequestInit) => {
      fetchCalls.push({ url, init: init ? JSON.parse(JSON.stringify(init)) : undefined });
      if (opts.identityThrow) throw new Error('NetworkError');
      const status = opts.identityStatus ?? 204;
      return { ok: status >= 200 && status < 300, status };
    },
    addEventListener: (t: string, fn: (e: unknown) => void) => {
      (winHandlers[t] ??= []).push(fn);
    },
    __loadModule: async (url: string) => {
      if (opts.firebaseFail) throw new Error('import failed');
      const mod = fakeModules[url];
      if (!mod) throw new Error(`unexpected module: ${url}`);
      return mod;
    },
  };
  ctx.window = ctx;

  if (opts.gisAvailable) {
    ctx.google = {
      accounts: {
        id: {
          initialize: (cfg: Record<string, unknown>) => oneTapInits.push(cfg),
          prompt: (cb: (n: Record<string, unknown>) => void) => {
            oneTapPrompts.push(1);
            promptCb = cb;
          },
          cancel: () => oneTapCancels.push(1),
        },
      },
    };
  }

  vm.runInContext(AUTH_TEST_SRC, vm.createContext(ctx));
  await flush();

  const gisScripts = () => html.findAll((e) => e.tag === 'script' && e.attrs.src === 'https://accounts.google.com/gsi/client');
  const finishGisLoad = () => {
    ctx.google = {
      accounts: {
        id: {
          initialize: (cfg: Record<string, unknown>) => oneTapInits.push(cfg),
          prompt: (cb: (n: Record<string, unknown>) => void) => {
            oneTapPrompts.push(1);
            promptCb = cb;
          },
          cancel: () => oneTapCancels.push(1),
        },
      },
    };
    for (const s of gisScripts()) s.emit('load');
  };

  const emitAuth = async (user: FakeUser | null) => {
    authListener?.(user);
    await flush();
  };

  const emitConsent = async (detail: 'granted' | 'denied') => {
    local.setItem('consent-v1', JSON.stringify({ v: detail, t: NOW }));
    for (const fn of winHandlers['guifav-consent'] ?? []) fn({ type: 'guifav-consent', detail });
    await flush();
  };

  const click = async (el: El) => {
    el.emit('click', { target: el });
    for (const fn of docHandlers.click ?? []) fn({ target: el });
    await flush();
  };

  const findClass = (cls: string) => body.findAll((e) => (e.attrs.class ?? '').split(' ').includes(cls))[0] ?? null;

  return {
    body,
    local,
    session,
    authObj,
    gtagCalls,
    clarityCalls,
    fetchCalls,
    warns,
    oneTapInits,
    oneTapPrompts,
    oneTapCancels,
    signInCredCalls,
    signInPopupCalls,
    signOutCalls: () => signOutCalls,
    initializedConfig: () => initializedConfig,
    gisScripts,
    finishGisLoad,
    emitAuth,
    emitConsent,
    click,
    findClass,
    triggerPromptMoment: (n: Record<string, unknown>) => promptCb?.(n),
    keydown: (key: string) => {
      for (const fn of docHandlers.keydown ?? []) fn({ key });
    },
  };
}

function makeUser(overrides: Partial<FakeUser> = {}): FakeUser {
  return {
    uid: 'UidCurso123ABC',
    tenantId: TENANT_ID,
    displayName: 'Maria Silva',
    email: 'maria.secreta@exemplo.com',
    getIdToken: async () => 'jwt.token.falso',
    ...overrides,
  };
}

test('track.js carrega auth.js com ?v= em idle só em guifav.dev e nunca em prévia local ou /admin', () => {
  function runTrack(hostname: string, pathname: string, useIdle: boolean) {
    const appended: El[] = [];
    const idleCalls: Array<() => void> = [];
    const timers: Array<{ fn: () => void; ms: number }> = [];
    const html = new El('html');
    html.attrs.lang = 'pt-BR';
    const body = html.appendChild(new El('body'));
    body.attrs['data-theme'] = 'light';
    const origAppend = body.appendChild.bind(body);
    body.appendChild = (c: El) => {
      appended.push(c);
      return origAppend(c);
    };
    const ctx: Record<string, unknown> = {
      document: {
        documentElement: html,
        body,
        baseURI: `https://${hostname}${pathname}`,
        referrer: '',
        cookie: '',
        createElement: (t: string) => new El(t),
        querySelector: () => null,
        addEventListener: () => {},
      },
      location: { hostname, pathname, href: `https://${hostname}${pathname}` },
      sessionStorage: new Storage(),
      localStorage: new Storage(),
      URL,
      JSON,
      Date,
      setTimeout: (fn: () => void, ms: number) => timers.push({ fn, ms }),
    };
    if (useIdle) {
      ctx.requestIdleCallback = (fn: () => void) => idleCalls.push(fn);
    }
    ctx.window = ctx;
    vm.runInContext(TRACK_SRC, vm.createContext(ctx));
    return { appended, idleCalls, timers };
  }

  const prod = runTrack('guifav.dev', '/', true);
  assert.equal(prod.idleCalls.length, 1);
  assert.equal(prod.appended.length, 0, 'só injeta quando idle dispara');
  prod.idleCalls[0]();
  assert.equal(prod.appended.length, 1);
  assert.match(prod.appended[0].attrs.src, /^\/assets\/auth\.js\?v=\d{12}$/);

  const fallback = runTrack('guifav.dev', '/blog/', false);
  assert.equal(fallback.timers.length, 1);
  assert.equal(fallback.timers[0].ms, 1500);
  fallback.timers[0].fn();
  assert.equal(fallback.appended.length, 1);

  for (const [host, path] of [
    ['localhost', '/'],
    ['127.0.0.1', '/'],
    ['preview.guifav.dev', '/'],
    ['guifav.dev', '/admin'],
    ['guifav.dev', '/admin/contatos'],
  ]) {
    const r = runTrack(host, path, true);
    assert.equal(r.idleCalls.length, 0, `${host}${path} não deve carregar auth.js`);
    assert.equal(r.timers.length, 0, `${host}${path} não deve agendar fallback`);
  }
});

test('One Tap: só carrega depois da resposta ao banner (granted ou denied), nunca em páginas excluídas nem logado', async () => {
  // Sem escolha no banner: GIS não carrega.
  const noConsent = await setupAuth({ pathname: '/', initialUser: null });
  assert.equal(noConsent.gisScripts().length, 0);
  assert.equal(noConsent.oneTapPrompts.length, 0);

  // Ao responder "Não" no banner, já respondeu ao aviso de cookies: One Tap pode carregar.
  await noConsent.emitConsent('denied');
  assert.equal(noConsent.gisScripts().length, 1);
  noConsent.finishGisLoad();
  assert.equal(noConsent.oneTapInits.length, 1);
  assert.equal(noConsent.oneTapInits[0].client_id, CLIENT_ID);
  assert.equal(noConsent.oneTapInits[0].itp_support, true);
  assert.equal(noConsent.oneTapInits[0].cancel_on_tap_outside, false);
  assert.equal(noConsent.oneTapPrompts.length, 1);

  // Credencial do One Tap chama signInWithCredential no tenant certo.
  const cb = noConsent.oneTapInits[0].callback as (r: { credential: string }) => void;
  cb({ credential: 'google-id-token-xyz' });
  await flush();
  assert.equal(noConsent.signInCredCalls.length, 1);
  assert.equal(noConsent.signInCredCalls[0].tenantId, TENANT_ID);
  assert.deepEqual(noConsent.signInCredCalls[0].cred, { providerId: 'google.com', idToken: 'google-id-token-xyz' });

  // Páginas excluídas: nunca carregam GIS nem chamam prompt.
  for (const [pathname, title] of [
    ['/privacidade/', 'Política de privacidade'],
    ['/termos/', 'Termos de uso'],
    ['/prioridade/', 'Não quero ser pontuado'],
    ['/404.html', 'Página não encontrada | Guilherme Favaron'],
    ['/inexistente/', 'Página não encontrada | Guilherme Favaron'],
  ]) {
    const s = await setupAuth({
      pathname,
      title,
      consent: { v: 'granted', t: NOW - 1000 },
      initialUser: null,
      gisAvailable: true,
    });
    assert.equal(s.gisScripts().length, 0, pathname);
    assert.equal(s.oneTapPrompts.length, 0, pathname);
  }

  // Já logado: nunca carrega One Tap.
  const logged = await setupAuth({
    pathname: '/',
    consent: { v: 'granted', t: NOW - 1000 },
    initialUser: makeUser(),
    gisAvailable: true,
  });
  assert.equal(logged.gisScripts().length, 0);
  assert.equal(logged.oneTapPrompts.length, 0);
  assert.ok(!AUTH_SRC.includes('getNotDisplayedReason'), 'não deve depender de getNotDisplayedReason por causa do FedCM');
});

test('tenant errado é ignorado; tenant certo mostra primeiro nome e aviso do login', async () => {
  const wrongTenant = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidOutroTenant1' },
    initialUser: makeUser({ uid: 'UidOutroTenant1', tenantId: 'outro-tenant' }),
  });
  assert.equal(wrongTenant.authObj.tenantId, TENANT_ID);
  assert.equal(wrongTenant.findClass('guifav-auth-btn')?.textContent, 'Entrar com Google');
  assert.equal(wrongTenant.findClass('guifav-auth-notice'), null);
  assert.deepEqual(wrongTenant.gtagCalls, []);
  assert.deepEqual(wrongTenant.fetchCalls, []);

  const ok = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    initialUser: makeUser(),
  });
  assert.equal(ok.findClass('guifav-auth-btn')?.textContent, 'Maria');
  const notice = ok.findClass('guifav-auth-notice');
  assert.ok(notice && !notice.hidden);
  assert.equal(notice.findAll((e) => e.tag === 'p')[0].textContent, NOTICE_TEXT);
  assert.equal(ok.findClass('guifav-auth-yes')?.textContent, 'Ligar');
  assert.equal(ok.findClass('guifav-auth-no')?.textContent, 'Agora não');
  // Antes de clicar em "Ligar", não manda user_id nem POST /identidade.
  assert.deepEqual(ok.gtagCalls, []);
  assert.deepEqual(ok.fetchCalls, []);
});

test('as três condições do user_id, sinais de anúncio desligados, uma vez por sessão e zero vazamento de email', async () => {
  const local = new Storage();
  const session = new Storage();
  local.setItem('consent-v1', JSON.stringify({ v: 'granted', t: NOW - 1000 }));

  const s = await setupAuth({ local, session, initialUser: makeUser() });
  await s.click(s.findClass('guifav-auth-yes')!);

  // Escolha salva por uid com validade.
  assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), {
    v: 'granted',
    t: NOW,
    uid: 'UidCurso123ABC',
  });

  // Ordem no GA4: sinais de anúncio desligados antes de user_id, seguido de auth_identified.
  assert.deepEqual(s.gtagCalls, [
    ['set', 'allow_google_signals', false],
    ['set', 'allow_ad_personalization_signals', false],
    ['set', { user_id: 'UidCurso123ABC' }],
    ['event', 'auth_identified'],
  ]);

  // POST /identidade com Bearer e sem corpo.
  assert.equal(s.fetchCalls.length, 1);
  assert.equal(s.fetchCalls[0].url, 'https://insights.guifav.dev/identidade');
  assert.equal(s.fetchCalls[0].init?.method, 'POST');
  assert.deepEqual(s.fetchCalls[0].init?.headers, { Authorization: 'Bearer jwt.token.falso' });
  assert.equal(s.fetchCalls[0].init?.body, undefined);

  // Nunca chama Clarity nem vaza email em nenhum canal ou DOM.
  assert.deepEqual(s.clarityCalls, []);
  const dump = JSON.stringify({
    gtag: s.gtagCalls,
    fetch: s.fetchCalls,
    warns: s.warns,
    dom: s.body.findAll(() => true).map((e) => e.textContent),
  });
  assert.ok(!dump.includes('maria.secreta@exemplo.com'));
  assert.ok(!dump.includes('@'));

  // Segunda página na mesma sessão: reaplica sinais desligados e user_id, mas NÃO repete auth_identified nem POST.
  const s2 = await setupAuth({ local, session, initialUser: makeUser() });
  assert.equal(s2.findClass('guifav-auth-notice'), null, 'aviso não reaparece para o mesmo uid');
  assert.deepEqual(s2.gtagCalls, [
    ['set', 'allow_google_signals', false],
    ['set', 'allow_ad_personalization_signals', false],
    ['set', { user_id: 'UidCurso123ABC' }],
  ]);
  assert.equal(s2.fetchCalls.length, 0);

  // Outro uid na mesma sessão não herda heating-link-v1 do primeiro uid: mostra aviso e não identifica.
  const otherUid = await setupAuth({
    local,
    session,
    initialUser: makeUser({ uid: 'UidDiferente999', displayName: 'Ana Paula' }),
  });
  assert.ok(otherUid.findClass('guifav-auth-notice'));
  assert.deepEqual(otherUid.gtagCalls, []);
  assert.equal(otherUid.fetchCalls.length, 0);

  // Sem consent-v1 granted (cookies negados), mesmo com heating-link-v1 granted, nada vai ao GA4 nem ao Worker.
  const noCookies = await setupAuth({
    consent: { v: 'denied', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  assert.deepEqual(noCookies.gtagCalls, []);
  assert.equal(noCookies.fetchCalls.length, 0);

  // heating-link-v1 vencido (> 365 dias) não vale e reabre o aviso.
  const expired = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 366 * DAY, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  assert.ok(expired.findClass('guifav-auth-notice'));
  assert.deepEqual(expired.gtagCalls, []);
  assert.equal(expired.fetchCalls.length, 0);
});

test('limpeza com user_id null literal em: Agora não, desligar no menu, logout e revogação de cookies', async () => {
  // 1. "Agora não" no aviso.
  const s1 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    initialUser: makeUser(),
  });
  await s1.click(s1.findClass('guifav-auth-no')!);
  assert.deepEqual(JSON.parse(s1.local.getItem('heating-link-v1')!), {
    v: 'denied',
    t: NOW,
    uid: 'UidCurso123ABC',
  });
  assert.deepEqual(s1.gtagCalls.at(-1), ['set', { user_id: null }]);
  assert.equal((s1.gtagCalls.at(-1)![1] as { user_id: unknown }).user_id, null);

  // 2. Desligar no menu da conta.
  const s2 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  await s2.click(s2.findClass('guifav-auth-btn')!);
  const toggle = s2.body.findAll((e) => e.attrs.role === 'switch')[0];
  assert.equal(toggle.attrs['aria-checked'], 'true');
  await s2.click(toggle);
  assert.equal(toggle.attrs['aria-checked'], 'false');
  assert.deepEqual(s2.gtagCalls.at(-1), ['set', { user_id: null }]);

  // 3. Logout ("Sair").
  const s3 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  await s3.click(s3.findClass('guifav-auth-btn')!);
  const sair = s3.body.findAll((e) => e.textContent === 'Sair')[0];
  await s3.click(sair);
  assert.equal(s3.signOutCalls(), 1);
  assert.deepEqual(s3.gtagCalls.at(-1), ['set', { user_id: null }]);

  // 4. Visitante muda banner de cookies para "Não" (guifav-consent denied).
  const s4 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const countBefore = s4.fetchCalls.length;
  await s4.emitConsent('denied');
  assert.deepEqual(s4.gtagCalls.at(-1), ['set', { user_id: null }]);
  assert.equal(s4.fetchCalls.length, countBefore);
});

test('falha do Firebase não quebra a página e emite só log curto sem dado pessoal', async () => {
  const s = await setupAuth({ firebaseFail: true, initialUser: null });
  assert.equal(s.warns.length, 1);
  assert.match(s.warns[0], /guifav-auth:/);
  assert.ok(!s.warns[0].includes('@'));
  assert.deepEqual(s.gtagCalls, []);
});
