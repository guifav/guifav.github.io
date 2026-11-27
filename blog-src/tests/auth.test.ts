// Testes do /assets/auth.js e do carregamento tardio pelo /assets/track.js (épico #197, corte 2).
// O script roda num contexto vm com DOM, storage, IndexedDB e SDKs falsos de Firebase/GIS.
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
const API_KEY = 'AIzaSyC16_hENAEO4CH6hHDcbFA9St8P1oLdYgI';
const IDB_USER_KEY = `firebase:authUser:${API_KEY}:[DEFAULT]`;
const NOTICE_TEXT =
  'Ao entrar, sua atividade neste site fica ligada ao seu contato: páginas vistas, tempo de leitura, rolagem e cliques em Agende e Contato. Uso para saber quem abordar primeiro. Você pode desligar quando quiser no menu da conta.';
const AD_CONSENT_DENIED = { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' };
const AD_CONSENT_GRANTED = { ad_storage: 'granted', ad_user_data: 'granted', ad_personalization: 'granted' };

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
  onFocus?: (el: El) => void;
  constructor(tag: string, onFocus?: (el: El) => void) {
    this.tag = tag;
    this.onFocus = onFocus;
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
    this.onFocus?.(this);
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
  clear() {
    this.m.clear();
  }
}

interface FakeUser {
  uid: string;
  tenantId: string | null;
  displayName?: string | null;
  email?: string | null;
  getIdToken?: () => Promise<string>;
}

type FakeIdbMode =
  | 'missing'
  | 'error'
  | 'upgrade'
  | 'no-store'
  | 'empty'
  | 'session'
  | 'session-null-value';

interface SetupOpts {
  hostname?: string;
  pathname?: string;
  title?: string;
  consent?: unknown;
  heatingLink?: unknown;
  heatingUid?: unknown;
  local?: Storage;
  session?: Storage;
  initialUser?: FakeUser | null;
  firebaseFail?: boolean;
  gisAvailable?: boolean;
  identityStatus?: number;
  identityThrow?: boolean;
  idbMode?: FakeIdbMode;
  signOutFn?: () => Promise<void>;
}

async function flush() {
  for (let i = 0; i < 12; i++) {
    await new Promise((r) => setImmediate(r));
  }
}

function makeFakeIndexedDb(mode: FakeIdbMode) {
  let upgradeAborted = false;
  let dbClosed = false;
  let requestedKey: string | null = null;
  let openedName: string | null = null;

  if (mode === 'missing') {
    return {
      indexedDB: undefined,
      stats: () => ({ upgradeAborted, dbClosed, requestedKey, openedName }),
    };
  }

  const indexedDB = {
    open(name: string) {
      openedName = name;
      if (mode === 'error') throw new Error('IDB blocked');
      const req: Record<string, unknown> = {
        result: null,
        transaction: {
          abort() {
            upgradeAborted = true;
          },
        },
        onupgradeneeded: null,
        onsuccess: null,
        onerror: null,
        onblocked: null,
      };
      Promise.resolve().then(() => {
        if (mode === 'upgrade') {
          if (typeof req.onupgradeneeded === 'function') (req.onupgradeneeded as () => void)();
          if (typeof req.onerror === 'function') (req.onerror as () => void)();
          return;
        }
        const storeNames = mode === 'no-store' ? [] : ['firebaseLocalStorage'];
        req.result = {
          objectStoreNames: {
            contains: (s: string) => storeNames.includes(s),
          },
          close: () => {
            dbClosed = true;
          },
          transaction: (_store: string, _mode: string) => ({
            onerror: null,
            objectStore: () => ({
              get: (key: string) => {
                requestedKey = key;
                const getReq: Record<string, unknown> = { result: undefined, onsuccess: null, onerror: null };
                Promise.resolve().then(() => {
                  if (mode === 'session' && key === IDB_USER_KEY) {
                    getReq.result = { fbase_key: key, value: { uid: 'UidCurso123ABC' } };
                  } else if (mode === 'session-null-value' && key === IDB_USER_KEY) {
                    getReq.result = { fbase_key: key, value: null };
                  }
                  if (typeof getReq.onsuccess === 'function') (getReq.onsuccess as () => void)();
                });
                return getReq;
              },
            }),
          }),
        };
        if (typeof req.onsuccess === 'function') (req.onsuccess as () => void)();
      });
      return req;
    },
  };
  return {
    indexedDB,
    stats: () => ({ upgradeAborted, dbClosed, requestedKey, openedName }),
  };
}

async function setupAuth(opts: SetupOpts = {}) {
  const gtagCalls: unknown[][] = [];
  const clarityCalls: unknown[][] = [];
  const fetchCalls: Array<{ url: string; init?: RequestInit }> = [];
  const warns: string[] = [];
  const firebaseImports: string[] = [];
  const oneTapInits: Record<string, unknown>[] = [];
  const oneTapPrompts: number[] = [];
  const oneTapCancels: number[] = [];
  const oneTapDisableAutoSelects: number[] = [];
  const signInCredCalls: Array<{ tenantId: string | null; cred: unknown }> = [];
  const signInPopupCalls: Array<{ tenantId: string | null }> = [];
  let signOutCalls = 0;
  let initializedConfig: Record<string, unknown> | null = null;
  let authListener: ((u: FakeUser | null) => void) | null = null;
  let promptCb: ((n: Record<string, unknown>) => void) | null = null;
  let activeElement: El | null = null;

  const setActive = (el: El) => {
    activeElement = el;
  };

  const html = new El('html', setActive);
  const head = html.appendChild(new El('head', setActive));
  const body = html.appendChild(new El('body', setActive));
  const docHandlers: Record<string, Array<(e: unknown) => void>> = {};
  const winHandlers: Record<string, Array<(e: unknown) => void>> = {};

  const local = opts.local ?? new Storage();
  if (opts.consent !== undefined) {
    local.setItem('consent-v1', typeof opts.consent === 'string' ? opts.consent : JSON.stringify(opts.consent));
  }
  if (opts.heatingLink !== undefined) {
    local.setItem('heating-link-v1', typeof opts.heatingLink === 'string' ? opts.heatingLink : JSON.stringify(opts.heatingLink));
  }
  if (opts.heatingUid !== undefined) {
    local.setItem('heating-uid-v1', typeof opts.heatingUid === 'string' ? opts.heatingUid : JSON.stringify(opts.heatingUid));
  }
  const session = opts.session ?? new Storage();

  const idb = makeFakeIndexedDb(opts.idbMode ?? 'empty');

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
        if (opts.signOutFn) return opts.signOutFn();
        authListener?.(null);
      },
    },
  };

  const document = {
    documentElement: html,
    head,
    body,
    title: opts.title ?? 'Guilherme Favaron | Builder e CTO',
    get activeElement() {
      return activeElement;
    },
    createElement: (t: string) => new El(t, setActive),
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
    indexedDB: idb.indexedDB,
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
      firebaseImports.push(url);
      if (opts.firebaseFail) throw new Error('import failed');
      const mod = fakeModules[url];
      if (!mod) throw new Error(`unexpected module: ${url}`);
      return mod;
    },
  };
  ctx.window = ctx;

  const installGis = () => {
    ctx.google = {
      accounts: {
        id: {
          initialize: (cfg: Record<string, unknown>) => oneTapInits.push(cfg),
          prompt: (cb: (n: Record<string, unknown>) => void) => {
            oneTapPrompts.push(1);
            promptCb = cb;
          },
          cancel: () => oneTapCancels.push(1),
          disableAutoSelect: () => oneTapDisableAutoSelects.push(1),
        },
      },
    };
  };

  if (opts.gisAvailable) installGis();

  vm.runInContext(AUTH_TEST_SRC, vm.createContext(ctx));
  await flush();

  const gisScripts = () => html.findAll((e) => e.tag === 'script' && e.attrs.src === 'https://accounts.google.com/gsi/client');
  const finishGisLoad = () => {
    installGis();
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

  const emitStorage = async (key: string | null, newValue: string | null = null) => {
    if (key === null) {
      local.clear();
    } else if (newValue === null) {
      local.removeItem(key);
    } else {
      local.setItem(key, newValue);
    }
    for (const fn of winHandlers.storage ?? []) fn({ type: 'storage', key, newValue });
    await flush();
  };

  const click = async (el: El) => {
    el.emit('click', { target: el });
    for (const fn of docHandlers.click ?? []) fn({ target: el });
    await flush();
  };

  const clickOutside = async () => {
    const outside = new El('main');
    for (const fn of docHandlers.click ?? []) fn({ target: outside });
    await flush();
  };

  const findClass = (cls: string) => body.findAll((e) => (e.attrs.class ?? '').split(' ').includes(cls))[0] ?? null;

  return {
    head,
    body,
    local,
    session,
    authObj,
    gtagCalls,
    clarityCalls,
    fetchCalls,
    warns,
    firebaseImports,
    oneTapInits,
    oneTapPrompts,
    oneTapCancels,
    oneTapDisableAutoSelects,
    signInCredCalls,
    signInPopupCalls,
    signOutCalls: () => signOutCalls,
    initializedConfig: () => initializedConfig,
    idbStats: idb.stats,
    activeElement: () => activeElement,
    gisScripts,
    finishGisLoad,
    emitAuth,
    emitConsent,
    emitStorage,
    click,
    clickOutside,
    findClass,
    triggerPromptMoment: (n: Record<string, unknown>) => promptCb?.(n),
    keydown: (key: string, target?: El) => {
      let prevented = false;
      for (const fn of docHandlers.keydown ?? []) {
        fn({ key, target: target ?? activeElement, preventDefault: () => { prevented = true; } });
      }
      return prevented;
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

test('nada do Google carrega antes da resposta ao banner, sessão salva no IndexedDB ou clique em Entrar', async () => {
  // Sem banner respondido e sem sessão salva: zero import de gstatic, zero GIS, mas botão HTML próprio já aparece.
  for (const idbMode of ['empty', 'missing', 'error', 'upgrade', 'no-store', 'session-null-value'] as FakeIdbMode[]) {
    const s = await setupAuth({ pathname: '/', idbMode, initialUser: null });
    assert.deepEqual(s.firebaseImports, [], `idbMode=${idbMode}: não deve importar Firebase`);
    assert.equal(s.gisScripts().length, 0, `idbMode=${idbMode}: não deve carregar GIS`);
    assert.equal(s.findClass('guifav-auth-btn')?.textContent, 'Entrar com Google', `idbMode=${idbMode}`);
    if (idbMode === 'upgrade') {
      assert.equal(s.idbStats().upgradeAborted, true, 'onupgradeneeded deve abortar para não criar DB vazio');
    }
    if (idbMode === 'empty') {
      assert.equal(s.idbStats().openedName, 'firebaseLocalStorageDb');
      assert.equal(s.idbStats().requestedKey, IDB_USER_KEY);
      assert.equal(s.idbStats().dbClosed, true);
    }
  }

  // (b) Sessão salva no IndexedDB carrega Firebase mesmo antes do banner, mas sem aviso nem One Tap.
  const withSession = await setupAuth({ pathname: '/', idbMode: 'session', initialUser: makeUser() });
  assert.equal(withSession.firebaseImports.length, 2);
  assert.equal(withSession.findClass('guifav-auth-btn')?.textContent, 'Maria');
  assert.equal(withSession.findClass('guifav-auth-notice'), null, 'aviso não aparece antes do banner');
  assert.equal(withSession.gisScripts().length, 0);

  // (c) Sem banner e sem sessão: clicar em "Entrar com Google" carrega Firebase sob demanda e abre popup no tenant.
  const onClick = await setupAuth({ pathname: '/', idbMode: 'empty', initialUser: null });
  assert.equal(onClick.firebaseImports.length, 0);
  await onClick.click(onClick.findClass('guifav-auth-btn')!);
  assert.equal(onClick.firebaseImports.length, 2);
  assert.equal(onClick.signInPopupCalls.length, 1);
  assert.equal(onClick.signInPopupCalls[0].tenantId, TENANT_ID);

  // (a) Responder ao banner ("Não" ou "Sim") carrega Firebase.
  const onBanner = await setupAuth({ pathname: '/', idbMode: 'empty', initialUser: null });
  assert.equal(onBanner.firebaseImports.length, 0);
  await onBanner.emitConsent('denied');
  assert.equal(onBanner.firebaseImports.length, 2);
});

test('One Tap: só carrega depois da resposta ao banner (granted ou denied), nunca em páginas excluídas nem logado, e respeita supressão na sessão', async () => {
  // Sem escolha no banner: GIS e Firebase não carregam.
  const noConsent = await setupAuth({ pathname: '/', initialUser: null });
  assert.equal(noConsent.gisScripts().length, 0);
  assert.equal(noConsent.oneTapPrompts.length, 0);

  // Ao responder "Não" no banner, já respondeu ao aviso de cookies: Firebase e One Tap carregam.
  await noConsent.emitConsent('denied');
  assert.equal(noConsent.gisScripts().length, 1);
  noConsent.finishGisLoad();
  assert.equal(noConsent.oneTapInits.length, 1);
  assert.equal(noConsent.oneTapInits[0].client_id, CLIENT_ID);
  assert.equal(noConsent.oneTapInits[0].itp_support, true);
  assert.equal(noConsent.oneTapInits[0].cancel_on_tap_outside, false);
  assert.equal(noConsent.oneTapPrompts.length, 1);

  // Fechar o One Tap (user_cancel / tap_outside) grava ONE_TAP_SUPPRESSED_KEY e suprime na sessão.
  for (const reason of ['user_cancel', 'tap_outside']) {
    const sess = new Storage();
    const s = await setupAuth({ pathname: '/', consent: { v: 'denied', t: NOW - 1000 }, session: sess, initialUser: null, gisAvailable: true });
    assert.equal(s.oneTapPrompts.length, 1, reason);
    s.triggerPromptMoment({ isSkippedMoment: () => true, getSkippedReason: () => reason });
    assert.equal(sess.getItem('guifav_one_tap_suppressed'), '1', reason);

    const nextPage = await setupAuth({ pathname: '/blog/', consent: { v: 'denied', t: NOW - 1000 }, session: sess, initialUser: null, gisAvailable: true });
    assert.equal(nextPage.oneTapPrompts.length, 0, `${reason}: suprimido na sessão`);
    assert.equal(nextPage.gisScripts().length, 0, `${reason}: não injeta GIS`);
  }

  // Credencial do One Tap chama signInWithCredential no tenant certo e limpa supressão.
  noConsent.session.setItem('guifav_one_tap_suppressed', '1');
  const cb = noConsent.oneTapInits[0].callback as (r: { credential: string }) => void;
  cb({ credential: 'google-id-token-xyz' });
  await flush();
  assert.equal(noConsent.signInCredCalls.length, 1);
  assert.equal(noConsent.signInCredCalls[0].tenantId, TENANT_ID);
  assert.deepEqual(noConsent.signInCredCalls[0].cred, { providerId: 'google.com', idToken: 'google-id-token-xyz' });
  assert.equal(noConsent.session.getItem('guifav_one_tap_suppressed'), null);

  // Quando o usuário loga enquanto GIS está carregado, cancelOneTap() é chamado.
  const cancels = await setupAuth({
    pathname: '/',
    consent: { v: 'granted', t: NOW - 1000 },
    initialUser: null,
    gisAvailable: true,
  });
  assert.equal(cancels.oneTapCancels.length, 0);
  await cancels.emitAuth(makeUser());
  assert.equal(cancels.oneTapCancels.length, 1);

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

test('aviso do login só aparece depois do banner respondido E com consent-v1 granted', async () => {
  // Logado (sessão salva no IndexedDB) sem resposta no banner -> aviso NÃO aparece.
  const preBanner = await setupAuth({ idbMode: 'session', initialUser: makeUser() });
  assert.equal(preBanner.findClass('guifav-auth-notice'), null);
  // Clicar "Não" no banner -> aviso continua sem aparecer.
  await preBanner.emitConsent('denied');
  assert.equal(preBanner.findClass('guifav-auth-notice'), null);
  // Clicar "Sim" no banner -> agora o aviso aparece.
  await preBanner.emitConsent('granted');
  const notice = preBanner.findClass('guifav-auth-notice');
  assert.ok(notice && !notice.hidden);
  assert.equal(notice.findAll((e) => e.tag === 'p')[0].textContent, NOTICE_TEXT);

  // Já com consent-v1 denied -> aviso NÃO aparece.
  const denied = await setupAuth({ consent: { v: 'denied', t: NOW - 1000 }, initialUser: makeUser() });
  assert.equal(denied.findClass('guifav-auth-notice'), null);
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
  // Antes de clicar em "Ligar", não manda user_id, não grava heating-uid-v1 nem chama POST /identidade.
  assert.equal(ok.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(ok.gtagCalls, []);
  assert.deepEqual(ok.fetchCalls, []);
});

test('as três condições do user_id, heating-uid-v1 sincronizado, sinais de anúncio desligados, uma vez por sessão e zero vazamento de email', async () => {
  const local = new Storage();
  const session = new Storage();
  local.setItem('consent-v1', JSON.stringify({ v: 'granted', t: NOW - 1000 }));

  const s = await setupAuth({ local, session, initialUser: makeUser() });
  await s.click(s.findClass('guifav-auth-yes')!);

  // Escolha e heating-uid-v1 salvos com validade.
  assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), {
    v: 'granted',
    t: NOW,
    uid: 'UidCurso123ABC',
  });
  assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), {
    uid: 'UidCurso123ABC',
    t: NOW,
  });

  // Ordem no GA4: consentimento de anúncio negado (sem mexer em analytics_storage) e sinais desligados antes de user_id, seguido de auth_identified.
  assert.deepEqual(s.gtagCalls, [
    ['consent', 'update', AD_CONSENT_DENIED],
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

  // Segunda página na mesma sessão: mantém heating-uid-v1, reaplica consentimento e sinais desligados e user_id, mas NÃO repete auth_identified nem POST.
  const s2 = await setupAuth({ local, session, initialUser: makeUser() });
  assert.equal(s2.findClass('guifav-auth-notice'), null, 'aviso não reaparece para o mesmo uid');
  assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), { uid: 'UidCurso123ABC', t: NOW });
  assert.deepEqual(s2.gtagCalls, [
    ['consent', 'update', AD_CONSENT_DENIED],
    ['set', 'allow_google_signals', false],
    ['set', 'allow_ad_personalization_signals', false],
    ['set', { user_id: 'UidCurso123ABC' }],
  ]);
  assert.equal(s2.fetchCalls.length, 0);

  // Outro uid na mesma sessão não herda heating-link-v1 nem heating-uid-v1 do primeiro uid: apaga heating-uid-v1, manda user_id null e devolve anúncios para granted (consent-v1 continua granted).
  const otherUid = await setupAuth({
    local,
    session,
    initialUser: makeUser({ uid: 'UidDiferente999', displayName: 'Ana Paula' }),
  });
  assert.ok(otherUid.findClass('guifav-auth-notice'));
  assert.equal(local.getItem('heating-uid-v1'), null);
  assert.deepEqual(otherUid.gtagCalls, [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
  ]);
  assert.equal(otherUid.fetchCalls.length, 0);

  // Troca de usuário para outro uid na mesma página sem reload: limpa user_id e devolve os 3 de anúncio para granted.
  const switchUser = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const callsBeforeSwitch = switchUser.gtagCalls.length;
  await switchUser.emitAuth(makeUser({ uid: 'UidDiferente999', displayName: 'Ana Paula' }));
  assert.equal(switchUser.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(switchUser.gtagCalls.slice(callsBeforeSwitch), [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
  ]);

  // Sem consent-v1 granted (cookies negados), mesmo com heating-link-v1 granted e heating-uid-v1 sobrando: apaga heating-uid-v1 e nada vai ao GA4 nem ao Worker.
  const noCookies = await setupAuth({
    consent: { v: 'denied', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
    initialUser: makeUser(),
  });
  assert.equal(noCookies.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(noCookies.gtagCalls, []);
  assert.equal(noCookies.fetchCalls.length, 0);

  // Se heating-uid-v1 existia (snippet do <head> enviou user_id e negou anúncios), mas sessão Firebase não existe mais: limpa a chave, envia user_id null e devolve anúncios para granted.
  const staleSession = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
    initialUser: null,
  });
  assert.equal(staleSession.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(staleSession.gtagCalls, [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
  ]);

  // heating-link-v1 vencido (> 365 dias) não vale, limpa heating-uid-v1, devolve anúncios para granted e reabre o aviso.
  const expired = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 366 * DAY, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
    initialUser: makeUser(),
  });
  assert.ok(expired.findClass('guifav-auth-notice'));
  assert.equal(expired.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(expired.gtagCalls, [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
  ]);
  assert.equal(expired.fetchCalls.length, 0);
});

test('limpeza de heating-uid-v1, user_id null literal e retorno do consentimento de anúncios em: Agora não, desligar no menu, logout e revogação de cookies; Sair suprime One Tap na mesma página', async () => {
  // 1. "Agora não" no aviso (nunca identificado nesta página: limpa user_id e não precisa reenviar consent update).
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
  assert.equal(s1.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s1.gtagCalls, [['set', { user_id: null }]]);
  assert.equal((s1.gtagCalls[0][1] as { user_id: unknown }).user_id, null);

  // 2. Desligar no menu da conta: limpa user_id e devolve os 3 de anúncio para granted (consent-v1 continua granted).
  const s2 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  assert.ok(s2.local.getItem('heating-uid-v1'));
  const callsBeforeOff = s2.gtagCalls.length;
  await s2.click(s2.findClass('guifav-auth-btn')!);
  const toggle = s2.body.findAll((e) => e.attrs.role === 'menuitemcheckbox')[0];
  assert.equal(toggle.attrs['aria-checked'], 'true');
  await s2.click(toggle);
  assert.equal(toggle.attrs['aria-checked'], 'false');
  assert.equal(s2.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s2.gtagCalls.slice(callsBeforeOff), [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
  ]);

  // 3. Logout ("Sair"): limpa heating-uid-v1 e user_id, devolve os 3 de anúncio para granted, marca supressão na sessão, chama disableAutoSelect() e NÃO dispara One Tap.
  const s3 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
    gisAvailable: true,
  });
  assert.ok(s3.local.getItem('heating-uid-v1'));
  const callsBeforeSair = s3.gtagCalls.length;
  await s3.click(s3.findClass('guifav-auth-btn')!);
  const sair = s3.body.findAll((e) => e.textContent === 'Sair')[0];
  await s3.click(sair);
  assert.equal(s3.signOutCalls(), 1);
  assert.equal(s3.local.getItem('heating-uid-v1'), null);
  assert.equal(s3.session.getItem('guifav_one_tap_suppressed'), '1');
  assert.equal(s3.oneTapDisableAutoSelects.length, 1);
  assert.equal(s3.oneTapPrompts.length, 0, 'Sair não pode disparar One Tap na mesma página');
  const sairCalls = s3.gtagCalls.slice(callsBeforeSair);
  assert.deepEqual(sairCalls.slice(0, 2), [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
  ]);
  assert.equal(
    sairCalls.filter((c) => c[0] === 'consent' && c[1] === 'update').length,
    1,
    'devolve consentimento de anúncio uma única vez no logout',
  );

  // 3b. Mesmo com signOut lento/pendente, o clique em "Sair" já limpa heating-uid-v1, manda user_id null e devolve anúncios para granted sincronamente.
  const s3b = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
    signOutFn: () => new Promise<void>(() => {}),
  });
  await s3b.click(s3b.findClass('guifav-auth-btn')!);
  const callsBeforeOut = s3b.gtagCalls.length;
  await s3b.click(s3b.body.findAll((e) => e.textContent === 'Sair')[0]);
  assert.equal(s3b.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s3b.gtagCalls.slice(callsBeforeOut), [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
  ]);

  // 4. Visitante muda banner de cookies para "Não" (guifav-consent denied): limpa user_id e NÃO devolve anúncios para granted.
  const s4 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  assert.ok(s4.local.getItem('heating-uid-v1'));
  const countBefore = s4.fetchCalls.length;
  const callsBeforeDeny = s4.gtagCalls.length;
  await s4.emitConsent('denied');
  assert.equal(s4.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s4.gtagCalls.slice(callsBeforeDeny), [['set', { user_id: null }]]);
  assert.equal(s4.fetchCalls.length, countBefore);

  // 4b. Se aceitar cookies de novo na mesma página com login e heating-link-v1 ativos, volta a negar anúncios antes de setar user_id.
  const callsBeforeRegrant = s4.gtagCalls.length;
  await s4.emitConsent('granted');
  assert.deepEqual(s4.gtagCalls.slice(callsBeforeRegrant), [
    ['consent', 'update', AD_CONSENT_DENIED],
    ['set', 'allow_google_signals', false],
    ['set', 'allow_ad_personalization_signals', false],
    ['set', { user_id: 'UidCurso123ABC' }],
  ]);
});

test('outra aba (evento storage): ressincroniza toggle, aviso, heating-uid-v1, user_id null e consentimento de anúncios', async () => {
  // 1. Outra aba desliga heating-link-v1 -> atualiza toggle, manda user_id null e devolve anúncios para granted.
  const s1 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const toggle1 = s1.body.findAll((e) => e.attrs.role === 'menuitemcheckbox')[0];
  assert.equal(toggle1.attrs['aria-checked'], 'true');
  const calls1 = s1.gtagCalls.length;
  await s1.emitStorage('heating-link-v1', JSON.stringify({ v: 'denied', t: NOW, uid: 'UidCurso123ABC' }));
  assert.equal(toggle1.attrs['aria-checked'], 'false');
  assert.equal(s1.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s1.gtagCalls.slice(calls1), [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
  ]);

  // 2. Outra aba revoga consent-v1 -> manda user_id null e NÃO devolve anúncios para granted.
  const s2 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const calls2 = s2.gtagCalls.length;
  await s2.emitStorage('consent-v1', JSON.stringify({ v: 'denied', t: NOW }));
  assert.equal(s2.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s2.gtagCalls.slice(calls2), [['set', { user_id: null }]]);

  // 3. Outra aba remove heating-uid-v1 (ex.: logout lá) -> manda user_id null sem reescrever heating-uid-v1 e devolve anúncios para granted.
  const s3 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const calls3 = s3.gtagCalls.length;
  await s3.emitStorage('heating-uid-v1', null);
  assert.equal(s3.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s3.gtagCalls.slice(calls3), [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
  ]);

  // 4. Outra aba troca heating-uid-v1 para outro uid -> manda user_id null nesta aba sem apagar o uid da outra aba e devolve anúncios para granted.
  const s4 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const calls4 = s4.gtagCalls.length;
  const otherUidVal = JSON.stringify({ uid: 'UidOutraConta9', t: NOW });
  await s4.emitStorage('heating-uid-v1', otherUidVal);
  assert.equal(s4.local.getItem('heating-uid-v1'), otherUidVal);
  assert.deepEqual(s4.gtagCalls.slice(calls4), [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
  ]);

  // 5. Outra aba chama localStorage.clear() (key === null) -> manda user_id null e NÃO devolve anúncios para granted.
  const s5 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const calls5 = s5.gtagCalls.length;
  await s5.emitStorage(null, null);
  assert.deepEqual(s5.gtagCalls.slice(calls5), [['set', { user_id: null }]]);
});

test('getIdToken pendente com toggle desligado no meio não faz POST /identidade', async () => {
  let resolveToken!: (tok: string) => void;
  const user = makeUser({
    getIdToken: () => new Promise<string>((resolve) => {
      resolveToken = resolve;
    }),
  });
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: user,
  });
  assert.equal(s.fetchCalls.length, 0);

  // Desliga o toggle no menu enquanto getIdToken ainda está pendente.
  await s.click(s.findClass('guifav-auth-btn')!);
  const toggle = s.body.findAll((e) => e.attrs.role === 'menuitemcheckbox')[0];
  await s.click(toggle);

  // Resolve o token depois de desligar: nenhum fetch deve sair.
  resolveToken('jwt.tardio.desligado');
  await flush();
  assert.equal(s.fetchCalls.length, 0);
});

test('Clarity mask e acessibilidade: ordem no DOM, menuitemcheckbox, aria-label com nome, setas, clique fora e Escape no menu e aviso', async () => {
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    initialUser: makeUser(),
  });

  // Clarity mask no container do botão, no menu e no aviso.
  const root = s.findClass('guifav-auth')!;
  const btn = s.findClass('guifav-auth-btn')!;
  const menu = s.findClass('guifav-auth-menu')!;
  const notice = s.findClass('guifav-auth-notice')!;
  assert.equal(root.attrs['data-clarity-mask'], 'true');
  assert.equal(menu.attrs['data-clarity-mask'], 'true');
  assert.equal(notice.attrs['data-clarity-mask'], 'true');

  // Ordem no DOM: botão antes do menu; CSS usa flex-direction:column-reverse.
  assert.deepEqual(root.children.map((c) => c.attrs.class), ['guifav-auth-btn', 'guifav-auth-menu']);
  const style = s.head.findAll((e) => e.attrs.id === 'guifav-auth-style')[0];
  assert.ok(style.textContent.includes('flex-direction:column-reverse'));

  // aria-label contém o texto visível (WCAG 2.5.3).
  assert.equal(btn.textContent, 'Maria');
  assert.ok(btn.attrs['aria-label'].includes('Maria'));

  // Aviso: aria-describedby aponta para o <p> e Escape fecha o aviso devolvendo foco ao botão.
  const descId = notice.attrs['aria-describedby'];
  assert.ok(descId);
  const descP = notice.findAll((e) => e.attrs.id === descId)[0];
  assert.equal(descP.textContent, NOTICE_TEXT);
  assert.equal(notice.hidden, false);
  s.keydown('Escape');
  assert.equal(notice.hidden, true);
  assert.equal(s.activeElement(), btn);

  // Menu: role="menuitemcheckbox" com aria-checked, foco ao abrir, setas, clique fora e Escape.
  const toggle = menu.findAll((e) => e.attrs.role === 'menuitemcheckbox')[0];
  const sair = menu.findAll((e) => e.attrs.role === 'menuitem')[0];
  assert.ok(toggle);
  assert.equal(toggle.attrs['aria-checked'], 'false');
  assert.equal(menu.findAll((e) => e.attrs.role === 'switch').length, 0);

  await s.click(btn);
  assert.equal(menu.hidden, false);
  assert.equal(s.activeElement(), toggle, 'abrir menu foca primeiro item');

  s.keydown('ArrowDown');
  assert.equal(s.activeElement(), sair);
  s.keydown('ArrowDown');
  assert.equal(s.activeElement(), toggle, 'ArrowDown cicla ao início');
  s.keydown('ArrowUp');
  assert.equal(s.activeElement(), sair, 'ArrowUp cicla ao fim');
  s.keydown('Home');
  assert.equal(s.activeElement(), toggle);
  s.keydown('End');
  assert.equal(s.activeElement(), sair);

  // Escape fecha menu e devolve foco ao botão.
  s.keydown('Escape');
  assert.equal(menu.hidden, true);
  assert.equal(btn.attrs['aria-expanded'], 'false');
  assert.equal(s.activeElement(), btn);

  // Clique fora fecha o menu.
  await s.click(btn);
  assert.equal(menu.hidden, false);
  await s.clickOutside();
  assert.equal(menu.hidden, true);
});

test('falha do Firebase não quebra a página e emite só log curto sem dado pessoal', async () => {
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    firebaseFail: true,
    initialUser: null,
  });
  assert.equal(s.warns.length, 1);
  assert.match(s.warns[0], /guifav-auth:/);
  assert.ok(!s.warns[0].includes('@'));
  assert.deepEqual(s.gtagCalls, []);
});
