// Testes do /assets/auth.js e do carregamento tardio pelo /assets/track.js (épico #197, corte 2).
// O script roda num contexto vm com DOM, storage, IndexedDB e SDKs falsos de Firebase/GIS.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const AUTH_SRC = readFileSync(new URL('../../assets/auth.js', import.meta.url), 'utf8');
const CONSENT_SRC = readFileSync(new URL('../../assets/consent.js', import.meta.url), 'utf8');
const TRACK_SRC = readFileSync(new URL('../../assets/track.js', import.meta.url), 'utf8');
const DAY = 24 * 3600 * 1000;
const MIN = 60 * 1000;
const NOW = Date.UTC(2026, 10, 27, 15, 0, 0);
const TENANT_ID = 'guifav-dev-g6dp4';
const CLIENT_ID = '486000748530-fj4un9nf6q94dcfjkttbrb87v026eboi.apps.googleusercontent.com';
const API_KEY = 'AIzaSyC16_hENAEO4CH6hHDcbFA9St8P1oLdYgI';
const IDB_USER_KEY = `firebase:authUser:${API_KEY}:[DEFAULT]`;
const NOTICE_TEXT =
  'Ao entrar, sua atividade neste site fica ligada ao seu contato: páginas vistas, tempo de leitura, rolagem e cliques em Agende e Contato. Uso para saber quem abordar primeiro. Você pode desligar quando quiser no menu da conta.';
const AD_CONSENT_DENIED = { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' };
const AD_CONSENT_GRANTED = { ad_storage: 'granted', ad_user_data: 'granted', ad_personalization: 'granted' };
// Sessão logada sem identificar (aviso pendente ou recusado): anúncios e sinais negados (desenho do #197, item 4).
const LOGIN_AD_DENY = [
  ['consent', 'update', { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' }],
  ['set', 'allow_google_signals', false],
  ['set', 'allow_ad_personalization_signals', false],
];
const INDEXED_DB_PERSISTENCE = { type: 'INDEXED_DB' };
const BROWSER_LOCAL_PERSISTENCE = { type: 'LOCAL' };
const BROWSER_POPUP_RESOLVER = { type: 'POPUP_REDIRECT_RESOLVER' };

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
  now?: number;
  consent?: unknown;
  heatingLink?: unknown;
  heatingUid?: unknown;
  local?: Storage;
  session?: Storage;
  initialUser?: FakeUser | null;
  firebaseFail?: boolean;
  gisAvailable?: boolean;
  gisThrowOn?: 'initialize' | 'prompt';
  identityStatus?: number;
  identityThrow?: boolean;
  identityFetch?: () => Promise<{ ok: boolean; status: number }>;
  idbMode?: FakeIdbMode;
  signOutFn?: () => Promise<void>;
  signInPopupFn?: () => Promise<unknown>;
  withConsentScript?: boolean;
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
  const signInPopupCalls: Array<{ tenantId: string | null; resolver: unknown }> = [];
  const initializeAuthCalls: Array<{ app: unknown; deps: Record<string, unknown> | undefined }> = [];
  const apiJsRequests: string[] = [];
  let getAuthCalls = 0;
  let signOutCalls = 0;
  let currentNow = opts.now ?? NOW;
  let initializedConfig: Record<string, unknown> | null = null;
  let authListener: ((u: FakeUser | null) => void) | null = null;
  let promptCb: ((n: Record<string, unknown>) => void) | null = null;
  let activeElement: El | null = null;
  let visibilityState = 'visible';
  let nextTimerId = 1;
  const pendingTimers = new Map<number, { fn: () => void; at: number }>();

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
      indexedDBLocalPersistence: INDEXED_DB_PERSISTENCE,
      browserLocalPersistence: BROWSER_LOCAL_PERSISTENCE,
      browserPopupRedirectResolver: BROWSER_POPUP_RESOLVER,
      // Simula firebase-auth 12.12.1 em Safari/iOS/celular: getAuth() ou initializeAuth com popupRedirectResolver
      // dispara carregamento proativo de https://apis.google.com/js/api.js.
      getAuth: () => {
        getAuthCalls += 1;
        apiJsRequests.push('https://apis.google.com/js/api.js');
        return authObj;
      },
      initializeAuth: (app: unknown, deps?: Record<string, unknown>) => {
        initializeAuthCalls.push({ app, deps });
        if (deps && deps.popupRedirectResolver) {
          apiJsRequests.push('https://apis.google.com/js/api.js');
        }
        return authObj;
      },
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
      signInWithPopup: async (a: { tenantId: string | null }, _provider: unknown, resolver?: unknown) => {
        signInPopupCalls.push({ tenantId: a.tenantId, resolver });
        if (!resolver) {
          throw Object.assign(new Error('missing popupRedirectResolver'), { code: 'auth/argument-error' });
        }
        apiJsRequests.push('https://apis.google.com/js/api.js');
        if (opts.signInPopupFn) return opts.signInPopupFn();
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
    cookie: '',
    title: opts.title ?? 'Guilherme Favaron | Builder e CTO',
    get activeElement() {
      return activeElement;
    },
    get visibilityState() {
      return visibilityState;
    },
    createElement: (t: string) => new El(t, setActive),
    createTextNode: (t: string) => {
      const n = new El('#text', setActive);
      n.textContent = t;
      return n;
    },
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
      reload: () => {},
    },
    JSON,
    Promise,
    Date: { now: () => currentNow },
    setTimeout: (fn: () => void, ms: number) => {
      const id = nextTimerId++;
      pendingTimers.set(id, { fn, at: currentNow + Math.max(0, Number(ms) || 0) });
      return id;
    },
    clearTimeout: (id: number) => {
      pendingTimers.delete(id);
    },
    console: { warn: (...a: unknown[]) => warns.push(a.map(String).join(' ')) },
    gtag: (...a: unknown[]) => gtagCalls.push(JSON.parse(JSON.stringify(a))),
    clarity: (...a: unknown[]) => clarityCalls.push(JSON.parse(JSON.stringify(a))),
    guifavClarity: () => {},
    CustomEvent: class FakeCustomEvent {
      type: string;
      detail: unknown;
      constructor(type: string, init?: { detail?: unknown }) {
        this.type = type;
        this.detail = init?.detail;
      }
    },
    dispatchEvent: (ev: { type?: string; detail?: unknown }) => {
      for (const fn of winHandlers[String(ev?.type || '')] ?? []) fn(ev);
      return true;
    },
    fetch: async (url: string, init?: RequestInit) => {
      fetchCalls.push({ url, init: init ? JSON.parse(JSON.stringify(init)) : undefined });
      if (opts.identityThrow) throw new Error('NetworkError');
      if (opts.identityFetch) return opts.identityFetch();
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
          initialize: (cfg: Record<string, unknown>) => {
            if (opts.gisThrowOn === 'initialize') throw new Error('GIS init broken');
            oneTapInits.push(cfg);
          },
          prompt: (cb: (n: Record<string, unknown>) => void) => {
            if (opts.gisThrowOn === 'prompt') throw new Error('FedCM prompt error');
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

  const vmCtx = vm.createContext(ctx);
  if (opts.withConsentScript) {
    html.attrs.lang = 'pt-BR';
    const cookieLink = body.appendChild(new El('a', setActive));
    cookieLink.setAttribute('href', '/privacidade/#cookies');
    cookieLink.setAttribute('data-consent-open', '');
    (cookieLink as El & { closest?: (sel: string) => El | null }).closest = (sel: string) =>
      sel === '[data-consent-open]' ? cookieLink : null;
    vm.runInContext(CONSENT_SRC, vmCtx);
  }
  vm.runInContext(AUTH_TEST_SRC, vmCtx);
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
    local.setItem('consent-v1', JSON.stringify({ v: detail, t: currentNow }));
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
    initializeAuthCalls,
    apiJsRequests,
    getAuthCalls: () => getAuthCalls,
    signOutCalls: () => signOutCalls,
    setNow: (ms: number) => {
      currentNow = ms;
    },
    pendingTimers: () => pendingTimers.size,
    win: ctx,
    createElement: (t: string) => new El(t, setActive),
    // Avança o relógio rodando, em ordem, os temporizadores que vencem no caminho (inclusive os agendados por eles).
    advance: async (ms: number) => {
      const end = currentNow + ms;
      for (;;) {
        let next: [number, { fn: () => void; at: number }] | null = null;
        for (const e of pendingTimers) if (e[1].at <= end && (!next || e[1].at < next[1].at)) next = e;
        if (!next) break;
        pendingTimers.delete(next[0]);
        currentNow = Math.max(currentNow, next[1].at);
        next[1].fn();
        await flush();
      }
      currentNow = end;
      await flush();
    },
    // Simula aba em segundo plano com temporizador atrasado: muda o relógio sem rodar temporizadores.
    emitVisibility: async (state: 'visible' | 'hidden') => {
      visibilityState = state;
      for (const fn of docHandlers.visibilitychange ?? []) fn({ type: 'visibilitychange' });
      await flush();
    },
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
  // Antes de clicar em "Ligar", não manda user_id, não grava heating-uid-v1 nem chama POST /identidade; a sessão logada
  // já nega anúncios e sinais e grava a marca de login (sem uid nem email) para o <head> das próximas páginas.
  assert.equal(ok.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(ok.gtagCalls, LOGIN_AD_DENY);
  assert.deepEqual(JSON.parse(ok.local.getItem('heating-login-v1')!), { t: NOW });
  assert.equal(wrongTenant.local.getItem('heating-login-v1'), null, 'outro tenant não marca login');
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

  // Ordem no GA4: a sessão logada já negou anúncios ao entrar; no "Ligar", consentimento de anúncio negado (sem mexer em
  // analytics_storage) e sinais desligados antes de user_id, seguido de auth_identified.
  assert.deepEqual(s.gtagCalls, [
    ...LOGIN_AD_DENY,
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

  // Outro uid na mesma sessão não herda heating-link-v1 nem heating-uid-v1 do primeiro uid: apaga heating-uid-v1 e manda user_id null; os anúncios continuam negados porque o vínculo segue ligado neste navegador.
  const otherUid = await setupAuth({
    local,
    session,
    initialUser: makeUser({ uid: 'UidDiferente999', displayName: 'Ana Paula' }),
  });
  assert.ok(otherUid.findClass('guifav-auth-notice'));
  assert.equal(local.getItem('heating-uid-v1'), null);
  assert.deepEqual(otherUid.gtagCalls, [...LOGIN_AD_DENY, ['set', { user_id: null }]]);
  assert.equal(otherUid.fetchCalls.length, 0);

  // Troca de usuário para outro uid na mesma página sem reload: limpa user_id; anúncios seguem negados (vínculo ligado no navegador).
  const switchUser = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const callsBeforeSwitch = switchUser.gtagCalls.length;
  await switchUser.emitAuth(makeUser({ uid: 'UidDiferente999', displayName: 'Ana Paula' }));
  assert.equal(switchUser.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(switchUser.gtagCalls.slice(callsBeforeSwitch), [['set', { user_id: null }]]);

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

  // Se heating-uid-v1 existia (snippet do <head> enviou user_id e negou anúncios), mas sessão Firebase não existe mais: limpa a chave e envia user_id null; anúncios seguem negados (vínculo ligado).
  const staleSession = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
    initialUser: null,
  });
  assert.equal(staleSession.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(staleSession.gtagCalls, [['set', { user_id: null }]]);

  // heating-link-v1 vencido (> 365 dias) não vale: limpa heating-uid-v1 e reabre o aviso.
  const expired = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 366 * DAY, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
    initialUser: makeUser(),
  });
  assert.ok(expired.findClass('guifav-auth-notice'));
  assert.equal(expired.local.getItem('heating-uid-v1'), null);
  // Vínculo vencido, mas a sessão continua logada: anúncios seguem negados (não voltam a granted).
  assert.deepEqual(expired.gtagCalls, [...LOGIN_AD_DENY, ['set', { user_id: null }]]);
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
  assert.deepEqual(s1.gtagCalls, [...LOGIN_AD_DENY, ['set', { user_id: null }]]);
  assert.equal((s1.gtagCalls[3][1] as { user_id: unknown }).user_id, null);

  // 2. Desligar no menu da conta: limpa user_id; logado, os anúncios continuam negados (só voltam depois do logout).
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
  assert.deepEqual(s2.gtagCalls.slice(callsBeforeOff), [['set', { user_id: null }]]);

  // 3. Logout ("Sair"): limpa heating-uid-v1 e user_id, marca supressão na sessão, chama disableAutoSelect() e NÃO dispara One Tap; anúncios seguem negados (o vínculo deste navegador continua ligado).
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
  assert.deepEqual(sairCalls.slice(0, 1), [['set', { user_id: null }]]);
  assert.equal(
    sairCalls.filter((c) => c[0] === 'consent' && c[1] === 'update').length,
    0,
    'com o vínculo ligado no navegador, o logout não devolve anúncios',
  );

  // 3b. Mesmo com signOut lento/pendente, o clique em "Sair" já limpa heating-uid-v1 e manda user_id null sincronamente.
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
  assert.deepEqual(s3b.gtagCalls.slice(callsBeforeOut), [['set', { user_id: null }]]);

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
  // 1. Outra aba desliga heating-link-v1 -> atualiza toggle e manda user_id null; logado, os anúncios seguem negados.
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
  assert.deepEqual(s1.gtagCalls.slice(calls1), [['set', { user_id: null }]]);

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

  // 3. Outra aba remove heating-uid-v1 (ex.: logout lá) -> manda user_id null sem reescrever heating-uid-v1; anúncios seguem negados (vínculo ligado).
  const s3 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const calls3 = s3.gtagCalls.length;
  await s3.emitStorage('heating-uid-v1', null);
  assert.equal(s3.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s3.gtagCalls.slice(calls3), [['set', { user_id: null }]]);

  // 4. Outra aba troca heating-uid-v1 para outro uid -> manda user_id null nesta aba sem apagar o uid da outra aba; anúncios seguem negados.
  const s4 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const calls4 = s4.gtagCalls.length;
  const otherUidVal = JSON.stringify({ uid: 'UidOutraConta9', t: NOW });
  await s4.emitStorage('heating-uid-v1', otherUidVal);
  assert.equal(s4.local.getItem('heating-uid-v1'), otherUidVal);
  assert.deepEqual(s4.gtagCalls.slice(calls4), [['set', { user_id: null }]]);

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

test('initializeAuth sem popupRedirectResolver não pede apis.google.com sem clique; clique em Entrar passa browserPopupRedirectResolver', async () => {
  assert.ok(!AUTH_SRC.includes('getAuth'), 'não deve usar getAuth (carrega apis.google.com em Safari/iOS/celular)');

  // Carregamento via banner respondido (granted ou denied), sessão salva no IndexedDB ou One Tap: zero apis.google.com.
  for (const scenario of [
    { name: 'consent granted', opts: { consent: { v: 'granted', t: NOW - 1000 }, initialUser: null, gisAvailable: true } },
    { name: 'consent denied', opts: { consent: { v: 'denied', t: NOW - 1000 }, initialUser: null, gisAvailable: true } },
    { name: 'sessão salva IDB', opts: { idbMode: 'session' as FakeIdbMode, initialUser: makeUser() } },
  ]) {
    const s = await setupAuth(scenario.opts);
    assert.equal(s.getAuthCalls(), 0, scenario.name);
    assert.equal(s.initializeAuthCalls.length, 1, scenario.name);
    const deps = s.initializeAuthCalls[0].deps;
    assert.ok(deps && typeof deps === 'object', scenario.name);
    assert.deepEqual(Object.keys(deps), ['persistence'], scenario.name);
    const persistence = deps.persistence as unknown[];
    assert.equal(persistence.length, 2, scenario.name);
    assert.equal(persistence[0], INDEXED_DB_PERSISTENCE, scenario.name);
    assert.equal(persistence[1], BROWSER_LOCAL_PERSISTENCE, scenario.name);
    assert.equal(deps.popupRedirectResolver, undefined, scenario.name);
    assert.deepEqual(s.apiJsRequests, [], `${scenario.name}: nada de apis.google.com sem clique em Entrar`);
  }

  // Somente ao clicar em "Entrar com Google" o signInWithPopup recebe browserPopupRedirectResolver (disparando apis.google.com).
  const clicked = await setupAuth({ consent: { v: 'denied', t: NOW - 1000 }, initialUser: null });
  assert.deepEqual(clicked.apiJsRequests, []);
  await clicked.click(clicked.findClass('guifav-auth-btn')!);
  assert.equal(clicked.signInPopupCalls.length, 1);
  assert.equal(clicked.signInPopupCalls[0].tenantId, TENANT_ID);
  assert.equal(clicked.signInPopupCalls[0].resolver, BROWSER_POPUP_RESOLVER);
  assert.deepEqual(clicked.apiJsRequests, ['https://apis.google.com/js/api.js']);
  assert.deepEqual(clicked.warns, []);
});

test('heating-uid-v1 tem validade deslizante de 30 min: renova a cada página, não ultrapassa heating-link-v1 e nunca grava se vencido', async () => {
  const local = new Storage();
  const session = new Storage();
  local.setItem('consent-v1', JSON.stringify({ v: 'granted', t: NOW - DAY }));
  local.setItem('heating-link-v1', JSON.stringify({ v: 'granted', t: NOW - DAY, uid: 'UidCurso123ABC' }));

  // Página 1 em NOW: grava heating-uid-v1 com t = NOW.
  await setupAuth({ local, session, now: NOW, initialUser: makeUser() });
  assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), { uid: 'UidCurso123ABC', t: NOW });

  // Página 2 em NOW + 20 min (dentro dos 30 min): renova t para NOW + 20 min após confirmar as 3 condições.
  const t2 = NOW + 20 * MIN;
  await setupAuth({ local, session, now: t2, initialUser: makeUser() });
  assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), { uid: 'UidCurso123ABC', t: t2 });

  // Página 3 em t2 + 31 min (passou de 30 min): se deslogou no curso, auth.js limpa a chave vencida sem precisar mandar user_id null
  // (o snippet do <head> já ignorou a chave vencida).
  const t3 = t2 + 31 * MIN;
  const loggedOut = await setupAuth({ local, session, now: t3, initialUser: null });
  assert.equal(local.getItem('heating-uid-v1'), null);
  assert.deepEqual(loggedOut.gtagCalls, [], 'head não enviou user_id com chave vencida (> 30 min)');

  // Nova visita em t3 + 5 min com Firebase ainda logado: auth.js confirma as 3 condições e grava novo t.
  const t4 = t3 + 5 * MIN;
  await setupAuth({ local, session, now: t4, initialUser: makeUser() });
  assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), { uid: 'UidCurso123ABC', t: t4 });

  // Quando heating-link-v1 está a 5 min de vencer (dia 365): heating-uid-v1 limita t para expirar junto com heating-link-v1.
  const linkStart = NOW - 365 * DAY + 5 * MIN;
  const nearExpiry = await setupAuth({
    now: NOW,
    consent: { v: 'granted', t: NOW - DAY },
    heatingLink: { v: 'granted', t: linkStart, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const written = JSON.parse(nearExpiry.local.getItem('heating-uid-v1')!);
  assert.equal(written.uid, 'UidCurso123ABC');
  assert.equal(written.t + 30 * MIN, linkStart + 365 * DAY, 'não pode ultrapassar a validade de heating-link-v1');

  // Quando heating-link-v1 já venceu: nunca grava heating-uid-v1 e remove se houver sobra.
  const expiredLink = await setupAuth({
    now: NOW,
    consent: { v: 'granted', t: NOW - DAY },
    heatingLink: { v: 'granted', t: NOW - 365 * DAY, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 5 * MIN },
    initialUser: makeUser(),
  });
  assert.equal(expiredLink.local.getItem('heating-uid-v1'), null);

  // Evento storage de heating-uid-v1 vindo de outra aba com mesmo uid não reescreve heating-uid-v1 (evita loop entre abas).
  const tab = await setupAuth({
    now: NOW,
    consent: { v: 'granted', t: NOW - DAY },
    heatingLink: { v: 'granted', t: NOW - DAY, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  tab.setNow(NOW + 5000);
  const fromOtherTab = JSON.stringify({ uid: 'UidCurso123ABC', t: NOW + 1000 });
  await tab.emitStorage('heating-uid-v1', fromOtherTab);
  assert.equal(tab.local.getItem('heating-uid-v1'), fromOtherTab);
});

test('integração consent.js + auth.js: clicar Sim com sessão identificada ou logada com heating-link-v1 nunca concede ad_* nem transitoriamente', async () => {
  // Caso 1: visitante já identificado reabre "Cookies" no rodapé e confirma "Sim".
  const active = await setupAuth({
    withConsentScript: true,
    consent: { v: 'granted', t: NOW - DAY },
    heatingLink: { v: 'granted', t: NOW - DAY, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 5 * MIN },
    initialUser: makeUser(),
  });
  const callsBefore1 = active.gtagCalls.length;
  const cookieLink1 = active.body.findAll((e) => e.getAttribute('data-consent-open') !== null)[0];
  await active.click(cookieLink1);
  await active.click(active.findClass('consent-yes')!);
  const updates1 = active.gtagCalls
    .slice(callsBefore1)
    .filter((c) => c[0] === 'consent' && c[1] === 'update')
    .map((c) => c[2] as Record<string, string>);
  assert.ok(updates1.length >= 1);
  assert.deepEqual(updates1[0], {
    ad_storage: 'denied',
    ad_user_data: 'denied',
    ad_personalization: 'denied',
    analytics_storage: 'granted',
  });
  for (const u of updates1) {
    assert.notEqual(u.ad_storage, 'granted');
    assert.notEqual(u.ad_user_data, 'granted');
    assert.notEqual(u.ad_personalization, 'granted');
  }

  // Caso 2: visitante estava com cookies "Não" (heating-uid-v1 apagado), mas logado com heating-link-v1 aceito, e muda para "Sim".
  const switched = await setupAuth({
    withConsentScript: true,
    consent: { v: 'denied', t: NOW - MIN },
    heatingLink: { v: 'granted', t: NOW - DAY, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  assert.equal(switched.local.getItem('heating-uid-v1'), null);
  const callsBefore2 = switched.gtagCalls.length;
  const cookieLink2 = switched.body.findAll((e) => e.getAttribute('data-consent-open') !== null)[0];
  await switched.click(cookieLink2);
  await switched.click(switched.findClass('consent-yes')!);
  const updates2 = switched.gtagCalls
    .slice(callsBefore2)
    .filter((c) => c[0] === 'consent' && c[1] === 'update')
    .map((c) => c[2] as Record<string, string>);
  assert.ok(updates2.length >= 1);
  assert.deepEqual(updates2[0], {
    ad_storage: 'denied',
    ad_user_data: 'denied',
    ad_personalization: 'denied',
    analytics_storage: 'granted',
  });
  for (const u of updates2) {
    assert.notEqual(u.ad_storage, 'granted');
    assert.notEqual(u.ad_user_data, 'granted');
    assert.notEqual(u.ad_personalization, 'granted');
  }
  assert.deepEqual(JSON.parse(switched.local.getItem('heating-uid-v1')!), { uid: 'UidCurso123ABC', t: NOW });
});

test('initOneTap envolve initialize e prompt em try/catch com log curto', async () => {
  for (const phase of ['initialize', 'prompt'] as const) {
    const s = await setupAuth({
      consent: { v: 'denied', t: NOW - 1000 },
      initialUser: null,
      gisAvailable: true,
      gisThrowOn: phase,
    });
    assert.equal(s.warns.length, 1, phase);
    assert.equal(s.warns[0], 'guifav-auth: falha no One Tap', phase);
    assert.ok(!s.warns[0].includes('@'), phase);
  }
});

test('antes do banner: clique em Entrar com popup fechado e sessão IDB que resolve deslogada não iniciam One Tap nem carregam GIS', async () => {
  // 1. Sem banner respondido, clique em Entrar e popup fechado pelo usuário -> Firebase carrega e resolve deslogado, mas GIS/One Tap NÃO iniciam.
  const popupClosed = await setupAuth({
    pathname: '/',
    idbMode: 'empty',
    initialUser: null,
    signInPopupFn: async () => {
      throw Object.assign(new Error('closed'), { code: 'auth/popup-closed-by-user' });
    },
  });
  await popupClosed.click(popupClosed.findClass('guifav-auth-btn')!);
  assert.equal(popupClosed.firebaseImports.length, 2);
  assert.equal(popupClosed.signInPopupCalls.length, 1);
  assert.equal(popupClosed.gisScripts().length, 0, 'não deve carregar GIS antes do banner após fechar popup');
  assert.equal(popupClosed.oneTapPrompts.length, 0, 'não deve chamar prompt antes do banner');
  assert.deepEqual(popupClosed.warns, []);

  // Mesmo se GIS já estivesse no window: não chama prompt nem initialize antes do banner.
  const popupClosedGisPresent = await setupAuth({
    pathname: '/',
    idbMode: 'empty',
    initialUser: null,
    gisAvailable: true,
    signInPopupFn: async () => {
      throw Object.assign(new Error('closed'), { code: 'auth/popup-closed-by-user' });
    },
  });
  await popupClosedGisPresent.click(popupClosedGisPresent.findClass('guifav-auth-btn')!);
  assert.equal(popupClosedGisPresent.oneTapInits.length, 0);
  assert.equal(popupClosedGisPresent.oneTapPrompts.length, 0);

  // 2. Sem banner respondido, sessão salva no IndexedDB que resolve deslogada (null) -> Firebase carrega, mas GIS/One Tap NÃO carregam nem iniciam.
  const staleIdb = await setupAuth({
    pathname: '/',
    idbMode: 'session',
    initialUser: null,
  });
  assert.equal(staleIdb.firebaseImports.length, 2);
  assert.equal(staleIdb.gisScripts().length, 0, 'sessão IDB deslogada não deve carregar GIS antes do banner');
  assert.equal(staleIdb.oneTapPrompts.length, 0);

  const staleIdbGisPresent = await setupAuth({
    pathname: '/',
    idbMode: 'session',
    initialUser: null,
    gisAvailable: true,
  });
  assert.equal(staleIdbGisPresent.firebaseImports.length, 2);
  assert.equal(staleIdbGisPresent.oneTapInits.length, 0);
  assert.equal(staleIdbGisPresent.oneTapPrompts.length, 0);
});

test('antes de o Firebase responder: Sim no banner não apaga o uid do <head> nem devolve anúncios; Não revoga na hora', async () => {
  // Visitante identificado pelo <head> reabre "Cookies" e clica em Sim antes de o onAuthStateChanged chegar.
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
  });
  const before = s.gtagCalls.length;
  await s.emitConsent('granted');
  assert.ok(s.local.getItem('heating-uid-v1'), 'o uid do <head> continua até o Firebase decidir');
  // Nada de user_id null nem anúncio concedido antes de o Firebase responder; com o vínculo ligado, o Sim reafirma a
  // negação de anúncios e sinais (igual ao snippet do <head>).
  assert.deepEqual(s.gtagCalls.slice(before), LOGIN_AD_DENY);

  // O Firebase confirma a sessão: identificação normal, com anúncios negados antes do user_id.
  await s.emitAuth(makeUser());
  const ids = s.gtagCalls.filter((c) => c[0] === 'set' && typeof c[1] === 'object' && (c[1] as { user_id?: unknown }).user_id === 'UidCurso123ABC');
  assert.equal(ids.length, 1);

  // Revogação explícita (banner Não) antes de o Firebase responder: limpa na hora, sem devolver anúncios.
  const r = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
  });
  const beforeR = r.gtagCalls.length;
  r.local.setItem('consent-v1', JSON.stringify({ v: 'denied', t: NOW }));
  await r.emitConsent('denied');
  assert.equal(r.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(r.gtagCalls.slice(beforeR), [['set', { user_id: null }]]);
});

test('antes de o Firebase responder: storage que não revoga não apaga o uid do <head>; uid apagado em outra aba revoga', async () => {
  const base = {
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
  };
  // Outra aba liga o vínculo de novo, aceita os cookies e renova o mesmo uid: nada muda nesta aba.
  const s = await setupAuth(base);
  const before = s.gtagCalls.length;
  await s.emitStorage('heating-link-v1', JSON.stringify({ v: 'granted', t: NOW, uid: 'UidCurso123ABC' }));
  await s.emitStorage('consent-v1', JSON.stringify({ v: 'granted', t: NOW }));
  const renovado = JSON.stringify({ uid: 'UidCurso123ABC', t: NOW });
  s.local.setItem('heating-uid-v1', renovado);
  await s.emitStorage('heating-uid-v1', renovado);
  assert.ok(s.local.getItem('heating-uid-v1'), 'o uid do <head> continua');
  assert.deepEqual(s.gtagCalls.slice(before), [], 'nada de user_id null antes de o Firebase responder');

  // Outra aba apaga o uid (Sair lá): revogação, o user_id do <head> sai desta aba também.
  const r = await setupAuth(base);
  const beforeR = r.gtagCalls.length;
  r.local.removeItem('heating-uid-v1');
  await r.emitStorage('heating-uid-v1', null);
  assert.deepEqual(r.gtagCalls.slice(beforeR), [['set', { user_id: null }]]);

  // Outra aba desliga o vínculo: revogação de fato, limpa mesmo antes de o Firebase responder.
  const d = await setupAuth(base);
  const beforeD = d.gtagCalls.length;
  const desligado = JSON.stringify({ v: 'denied', t: NOW, uid: 'UidCurso123ABC' });
  d.local.setItem('heating-link-v1', desligado);
  await d.emitStorage('heating-link-v1', desligado);
  assert.equal(d.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(d.gtagCalls.slice(beforeD)[0], ['set', { user_id: null }]);
});

const UID_TTL = 30 * 60 * 1000;
const isUserIdSet = (c: unknown[], uid: string | null) =>
  c[0] === 'set' && typeof c[1] === 'object' && c[1] !== null && (c[1] as { user_id?: unknown }).user_id === uid;

test('Kimi P1 (rodada 4): página aberta sem o Firebase confirmar perde o user_id no vencimento de heating-uid-v1', async () => {
  // Firebase nunca responde (sem initialUser): o uid do <head> vale até o fim dos 30 min e então sai.
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - (UID_TTL - 60_000) },
  });
  const before = s.gtagCalls.length;
  await s.advance(59_000);
  assert.ok(s.local.getItem('heating-uid-v1'), 'antes do vencimento nada muda');
  assert.deepEqual(s.gtagCalls.slice(before), []);
  await s.advance(2_000);
  assert.equal(s.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s.gtagCalls.slice(before), [['set', { user_id: null }]], 'sem anúncio concedido: o vínculo segue ligado');
  assert.equal(s.pendingTimers(), 0, 'sem novo temporizador depois de limpar');
});

test('Kimi P1 (rodada 4): sessão confirmada renova no vencimento; sem as 3 condições no vencimento, limpa', async () => {
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  assert.ok(s.gtagCalls.some((c) => isUserIdSet(c, 'UidCurso123ABC')));
  const before = s.gtagCalls.length;
  // 3 vencimentos seguidos com as condições de pé: a chave é renovada a cada um e o user_id continua.
  await s.advance(3 * UID_TTL + 1_000);
  const stored = JSON.parse(s.local.getItem('heating-uid-v1') || 'null');
  assert.equal(stored.uid, 'UidCurso123ABC');
  assert.ok(NOW + 3 * UID_TTL + 1_000 - stored.t < UID_TTL, 'renovada dentro da janela');
  assert.ok(!s.gtagCalls.slice(before).some((c) => isUserIdSet(c, null)), 'nenhum user_id null enquanto confirmado');

  // O consentimento muda sem evento (storage perdido): no próximo vencimento a sessão não se confirma e limpa.
  s.local.setItem('consent-v1', JSON.stringify({ v: 'denied', t: NOW }));
  const beforeClear = s.gtagCalls.length;
  await s.advance(UID_TTL + 1_000);
  assert.equal(s.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s.gtagCalls.slice(beforeClear), [['set', { user_id: null }]]);
  assert.equal(s.pendingTimers(), 0);
});

test('Kimi P1 (rodada 4): aba que volta a ficar visível com o temporizador atrasado confere a validade na hora', async () => {
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
  });
  const before = s.gtagCalls.length;
  await s.emitVisibility('hidden');
  s.setNow(NOW + UID_TTL + 5_000); // relógio andou sem o temporizador rodar (aba em segundo plano)
  await s.emitVisibility('visible');
  assert.equal(s.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s.gtagCalls.slice(before), [['set', { user_id: null }]]);

  // Ainda dentro da validade: voltar à aba não muda nada.
  const r = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
  });
  const beforeR = r.gtagCalls.length;
  await r.emitVisibility('visible');
  assert.ok(r.local.getItem('heating-uid-v1'));
  assert.deepEqual(r.gtagCalls.slice(beforeR), []);
});

test('oposição em /prioridade: /identidade responde 409, o vínculo desliga neste navegador e o user_id sai', async () => {
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
    identityStatus: 409,
  });
  await flush();
  assert.equal(s.fetchCalls.length, 1);
  const link = JSON.parse(s.local.getItem('heating-link-v1') || 'null');
  assert.equal(link.v, 'denied');
  assert.equal(link.uid, 'UidCurso123ABC');
  assert.equal(s.local.getItem('heating-uid-v1'), null);
  const last = s.gtagCalls.filter((c) => c[0] === 'set' && typeof c[1] === 'object' && c[1] !== null && 'user_id' in (c[1] as object)).pop();
  assert.deepEqual(last, ['set', { user_id: null }]);
  assert.equal(s.session.getItem('guifav_identidade_sent:UidCurso123ABC'), null, 'não marca como enviada');
  const state = s.findClass('guifav-auth-state')!;
  assert.equal(state.textContent, 'Desligado');
  assert.equal(s.pendingTimers(), 0);
  assert.equal(s.warns.length, 0, 'oposição não é falha');
});

test('Kimi P2 (rodada 4): o aviso recebe o foco ao abrir e devolve o foco ao fechar por Ligar, Agora não ou Escape', async () => {
  for (const how of ['Ligar', 'Agora não', 'Escape'] as const) {
    const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser() });
    const notice = s.findClass('guifav-auth-notice')!;
    const btn = s.findClass('guifav-auth-btn')!;
    assert.equal(notice.hidden, false, how);
    assert.equal(notice.attrs.tabindex, '-1', how);
    assert.equal(s.activeElement(), notice, `${how}: foco no diálogo ao abrir`);
    if (how === 'Escape') {
      s.keydown('Escape');
    } else {
      const b = notice.findAll((e) => e.tag === 'button' && e.textContent === how)[0];
      b.focus();
      await s.click(b);
    }
    assert.equal(notice.hidden, true, how);
    assert.equal(s.activeElement(), btn, `${how}: foco volta ao botão da conta`);
  }

  // Foco estava em outro elemento quando o aviso abriu: volta para ele, não para o botão da conta.
  const r = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 } });
  const other = r.findClass('guifav-auth-item')!;
  other.focus();
  await r.emitAuth(makeUser());
  const n = r.findClass('guifav-auth-notice')!;
  assert.equal(r.activeElement(), n);
  r.keydown('Escape');
  assert.equal(r.activeElement(), other);

  // Fechamento programático (cookies negados) sem foco no aviso não rouba o foco.
  const q = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser() });
  const qn = q.findClass('guifav-auth-notice')!;
  const qitem = q.findClass('guifav-auth-item')!;
  qitem.focus();
  await q.emitConsent('denied');
  assert.equal(qn.hidden, true);
  assert.equal(q.activeElement(), qitem);
});

test('Claude P2 (rodada 5): aviso que abre sozinho não tira o foco de quem digita; anuncia por aria-live', async () => {
  const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 } });
  // A região viva já existe, vazia, antes de o aviso abrir (rodada 6: leitor de tela ignora região que nasce com texto).
  const live = s.findClass('guifav-auth-live')!;
  assert.ok(live);
  assert.equal(live.attrs.role, 'status');
  assert.equal(live.attrs['aria-live'], 'polite');
  assert.equal(live.textContent, '');
  const input = s.body.appendChild(s.createElement('input'));
  input.focus();
  await s.emitAuth(makeUser());
  const notice = s.findClass('guifav-auth-notice')!;
  assert.equal(notice.hidden, false);
  assert.equal(s.activeElement(), input, 'o foco continua no campo');
  assert.equal(live.textContent, '', 'o texto entra um instante depois');
  await s.advance(100);
  assert.equal(live.textContent, NOTICE_TEXT);
  // Responder pelo clique fecha sem puxar o foco de volta para o botão da conta se ele não estava no aviso.
  const yes = notice.findAll((e) => e.tag === 'button' && e.textContent === 'Ligar')[0];
  await s.click(yes);
  assert.equal(notice.hidden, true);
  assert.equal(s.activeElement(), input);
  assert.equal(live.textContent, '', 'fechar o aviso apaga o texto da região viva');

  // Aviso fechado antes do anúncio: o texto não entra depois.
  const q = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 } });
  const qin = q.body.appendChild(q.createElement('input'));
  qin.focus();
  await q.emitAuth(makeUser());
  await q.emitConsent('denied');
  await q.advance(200);
  assert.equal(q.findClass('guifav-auth-live')!.textContent, '');

  // Sem nada focado (ou com o foco no botão da conta), o aviso toma o foco e não usa a região viva.
  const r = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser() });
  assert.equal(r.activeElement(), r.findClass('guifav-auth-notice'));
  await r.advance(200);
  assert.equal(r.findClass('guifav-auth-live')!.textContent, '', 'com o foco no aviso, a região viva não repete o texto');
});

test('Claude P3 (rodada 5): antes do Firebase, uid de outra pessoa gravado por outra aba tira o user_id do <head>', async () => {
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
  });
  const before = s.gtagCalls.length;
  const outro = JSON.stringify({ uid: 'UidOutraConta9', t: NOW });
  await s.emitStorage('heating-uid-v1', outro);
  assert.deepEqual(s.gtagCalls.slice(before), [['set', { user_id: null }]]);
  assert.equal(s.local.getItem('heating-uid-v1'), outro, 'não apaga a chave da outra aba');
  assert.equal(s.pendingTimers(), 0);
});

test('Claude P3 (rodada 5): 409 que chega depois de trocar de conta não desliga o vínculo de quem está logado agora', async () => {
  const responders: Array<(r: { ok: boolean; status: number }) => void> = [];
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
    identityFetch: () => new Promise((res) => { responders.push(res); }),
  });
  assert.equal(s.fetchCalls.length, 1);
  // Outra conta entra antes da resposta, com o vínculo dela já ligado neste navegador.
  s.local.setItem('heating-link-v1', JSON.stringify({ v: 'granted', t: NOW, uid: 'UidOutraConta9' }));
  await s.emitAuth(makeUser({ uid: 'UidOutraConta9', displayName: 'Outra Pessoa' }));
  assert.equal(responders.length, 2, 'uma chamada por conta');
  responders[0]({ ok: false, status: 409 }); // resposta atrasada da conta anterior
  responders[1]({ ok: true, status: 204 });
  await flush();
  const link = JSON.parse(s.local.getItem('heating-link-v1') || 'null');
  assert.deepEqual([link.v, link.uid], ['granted', 'UidOutraConta9'], 'o 409 da conta anterior não mexe na atual');
  assert.ok(s.gtagCalls.some((c) => isUserIdSet(c, 'UidOutraConta9')));
  const last = s.gtagCalls.filter((c) => c[0] === 'set' && typeof c[1] === 'object' && c[1] !== null && 'user_id' in (c[1] as object)).pop();
  assert.deepEqual(last, ['set', { user_id: 'UidOutraConta9' }]);
});

test('Kimi P1 (rodada 6): sessão logada sem vínculo nega anúncios, marca login e só devolve anúncios no logout', async () => {
  const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser() });
  assert.deepEqual(s.gtagCalls, LOGIN_AD_DENY);
  assert.ok(s.local.getItem('heating-login-v1'));
  // "Agora não" mantém a sessão logada: anúncios continuam negados.
  const beforeNo = s.gtagCalls.length;
  await s.click(s.findClass('guifav-auth-no')!);
  assert.ok(!s.gtagCalls.slice(beforeNo).some((c) => c[0] === 'consent'), 'nada de anúncio concedido logado');
  // Logout (Firebase propaga null): a marca sai e os anúncios voltam, porque o vínculo não está ligado.
  const beforeOut = s.gtagCalls.length;
  await s.emitAuth(null);
  assert.equal(s.local.getItem('heating-login-v1'), null);
  // Kimi P2 (rodada 7): user_id null e depois anúncios e os dois sinais juntos de volta ao consentimento comum.
  assert.deepEqual(s.gtagCalls.slice(beforeOut), [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
    ['set', 'allow_google_signals', true],
    ['set', 'allow_ad_personalization_signals', true],
  ]);

  // Com o vínculo ligado no navegador, o logout não devolve anúncios.
  const r = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const beforeR = r.gtagCalls.length;
  await r.emitAuth(null);
  assert.equal(r.local.getItem('heating-login-v1'), null);
  assert.equal(r.gtagCalls.slice(beforeR).filter((c) => c[0] === 'consent').length, 0);

  // Cookies recusados: login não manda nada ao GA4 (anúncios já negados por padrão), mas marca o login.
  const q = await setupAuth({ consent: { v: 'denied', t: NOW - 1000 }, initialUser: makeUser() });
  assert.deepEqual(q.gtagCalls, []);
  assert.ok(q.local.getItem('heating-login-v1'));
});

test('Kimi P2 (rodada 6): logout em outra aba suprime o One Tap nesta aba; Kimi P3: Sair pelo teclado devolve o foco', async () => {
  const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser(), gisAvailable: true });
  assert.equal(s.session.getItem('guifav_one_tap_suppressed'), null);
  await s.emitAuth(null); // Sair feito em outra aba
  assert.equal(s.session.getItem('guifav_one_tap_suppressed'), '1');
  assert.equal(s.oneTapPrompts.length, 0, 'o convite não aparece logo depois do logout remoto');
  // Visitante que nunca entrou continua vendo o One Tap (a supressão só vem de uma saída).
  const fresh = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: null, gisAvailable: true });
  assert.equal(fresh.oneTapPrompts.length, 1);

  const k = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' }, initialUser: makeUser() });
  const btn = k.findClass('guifav-auth-btn')!;
  await k.click(btn);
  const sair = k.body.findAll((e) => e.textContent === 'Sair')[0];
  sair.focus();
  await k.click(sair);
  assert.equal(k.activeElement(), btn, 'o foco volta ao botão da conta');
});

test('Kimi P1 (rodada 6): sem a marca de login gravável, a sessão confirmada ainda segura anúncios; guifavAuthIdentified', async () => {
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  const authIdentified = () => (s.win.guifavAuthIdentified as () => boolean)();
  assert.equal(authIdentified(), true);
  // Armazenamento sem a marca (bloqueado ou apagado): desligar o vínculo logado não devolve anúncios.
  s.local.removeItem('heating-login-v1');
  await s.click(s.findClass('guifav-auth-btn')!);
  const before = s.gtagCalls.length;
  await s.click(s.body.findAll((e) => e.attrs.role === 'menuitemcheckbox')[0]);
  assert.deepEqual(s.gtagCalls.slice(before), [['set', { user_id: null }]]);
  assert.equal(authIdentified(), true, 'logado sem vínculo: o consent.js mantém anúncios negados');
  await s.emitAuth(null);
  assert.equal(authIdentified(), false, 'depois do logout e sem vínculo, o banner volta a conceder');
});

test('Claude P3 (rodada 7): Sim no banner depois de o Firebase confirmar a conta desliga também os sinais de anúncio', async () => {
  // Logado, sem vínculo, banner ainda sem resposta quando o Firebase confirma; depois Sim.
  const s = await setupAuth({ idbMode: 'session', initialUser: makeUser() });
  assert.deepEqual(s.gtagCalls, []);
  await s.emitConsent('granted');
  assert.deepEqual(s.gtagCalls, LOGIN_AD_DENY);

  // Só com o vínculo ligado no navegador, sem sessão: o mesmo.
  const r = await setupAuth({ heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' }, idbMode: 'session', initialUser: null });
  await r.emitConsent('granted');
  assert.deepEqual(r.gtagCalls, LOGIN_AD_DENY);

  // Sem sessão nem vínculo: o Sim não nega nada.
  const q = await setupAuth({ idbMode: 'session', initialUser: null });
  await q.emitConsent('granted');
  assert.deepEqual(q.gtagCalls, []);
});

test('Kimi P2 e P3 (rodada 7): Não -> Sim logado sem vínculo desliga os sinais; logout remoto com foco no menu devolve o foco', async () => {
  const s = await setupAuth({ consent: { v: 'denied', t: NOW - 1000 }, initialUser: makeUser() });
  assert.deepEqual(s.gtagCalls, []);
  await s.emitConsent('granted');
  assert.deepEqual(s.gtagCalls, LOGIN_AD_DENY);

  const k = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' }, initialUser: makeUser() });
  const btn = k.findClass('guifav-auth-btn')!;
  await k.click(btn);
  const sair = k.body.findAll((e) => e.textContent === 'Sair')[0];
  sair.focus();
  await k.emitAuth(null); // Sair feito em outra aba
  assert.equal(k.activeElement(), btn, 'o foco sai do item escondido e volta ao botão');
  assert.equal(btn.textContent, 'Entrar com Google');

  // Fechar o menu com clique fora não rouba o foco de onde a pessoa clicou.
  const c = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' }, initialUser: makeUser() });
  await c.click(c.findClass('guifav-auth-btn')!);
  const input = c.body.appendChild(c.createElement('input'));
  input.focus();
  await c.clickOutside();
  assert.equal(c.activeElement(), input);
});

