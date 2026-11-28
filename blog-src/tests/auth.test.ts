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
const IDENTITY_URL = 'https://insights.guifav.dev/identidade';
const UID = 'UidCurso123ABC';
const AD_CONSENT_DENIED = { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' };
const AD_CONSENT_GRANTED = { ad_storage: 'granted', ad_user_data: 'granted', ad_personalization: 'granted' };
// Sessão logada sem identificar (oposição registrada, ou cookies aceitos sem o vínculo legível): anúncios e sinais
// negados (desenho do #197, item 4).
const LOGIN_AD_DENY = [
  ['consent', 'update', { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' }],
  ['set', 'allow_google_signals', false],
  ['set', 'allow_ad_personalization_signals', false],
];
// Identificação com as 3 condições: anúncios e sinais negados antes do user_id; auth_identified só uma vez por sessão.
const identifySeq = (uid: string, comEvento = true): unknown[][] => [
  ['consent', 'update', AD_CONSENT_DENIED],
  ['set', 'allow_google_signals', false],
  ['set', 'allow_ad_personalization_signals', false],
  ['set', { user_id: uid }],
  ...(comEvento ? [['event', 'auth_identified']] : []),
];
// Oposição registrada no servidor (409 do /identidade): a única marca que segura o vínculo desligado.
const OPPOSED_LINK = { v: 'denied', t: NOW - DAY, uid: UID, o: 1 };
const UID_TTL = 30 * 60 * 1000;
const isUserIdSet = (c: unknown[], uid: string | null) =>
  c[0] === 'set' && typeof c[1] === 'object' && c[1] !== null && (c[1] as { user_id?: unknown }).user_id === uid;
// Vínculo ligado e já confirmado pelo servidor neste navegador (POST /identidade ok, s: 1).
const linkOk = (t: number, uid = UID) => ({ v: 'granted', t, uid, s: 1 });
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
  // Texto como no DOM: atribuir textContent troca os filhos por um nó #text; ler concatena o texto dos filhos.
  private rawText = '';
  hidden = false;
  async = false;
  focused = false;
  handlers: Record<string, Array<{ fn: (e: unknown) => void; once?: boolean }>> = {};
  onFocus?: (el: El) => void;
  constructor(tag: string, onFocus?: (el: El) => void) {
    this.tag = tag;
    this.onFocus = onFocus;
  }
  get textContent(): string {
    if (this.tag === '#text') return this.rawText;
    return this.children.map((c) => c.textContent).join('');
  }
  set textContent(v: string) {
    const s = String(v ?? '');
    if (this.tag === '#text') {
      this.rawText = s;
      return;
    }
    for (const c of this.children) c.parent = null;
    this.children = [];
    if (s) {
      const t = new El('#text');
      t.rawText = s;
      t.parent = this;
      this.children.push(t);
    }
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
    if (c.parent) c.parent.removeChild(c);
    c.parent = this;
    this.children.push(c);
    return c;
  }
  insertBefore(c: El, ref: El | null) {
    if (c.parent) c.parent.removeChild(c);
    const i = ref ? this.children.indexOf(ref) : -1;
    c.parent = this;
    if (i < 0) this.children.push(c);
    else this.children.splice(i, 0, c);
    return c;
  }
  removeChild(c: El) {
    const i = this.children.indexOf(c);
    if (i >= 0) this.children.splice(i, 1);
    c.parent = null;
    return c;
  }
  replaceChild(n: El, old: El) {
    const i = this.children.indexOf(old);
    if (i < 0) return old;
    if (n.parent) n.parent.removeChild(n);
    n.parent = this;
    this.children[i] = n;
    old.parent = null;
    return old;
  }
  get parentNode() {
    return this.parent;
  }
  get firstChild() {
    return this.children[0] ?? null;
  }
  get nextSibling() {
    if (!this.parent) return null;
    const sib = this.parent.children;
    return sib[sib.indexOf(this) + 1] ?? null;
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

// Storage que registra cada gravação: prova que uma chave nunca foi escrita, nem por um instante.
class SpyStorage extends Storage {
  writes: Array<[string, string]> = [];
  setItem(k: string, v: string) {
    this.writes.push([k, String(v)]);
    super.setItem(k, v);
  }
}

// POST /identidade que só responde quando o teste mandar.
function identidadePendente() {
  const fila: Array<(r: { ok: boolean; status: number }) => void> = [];
  return {
    fetch: () => new Promise<{ ok: boolean; status: number }>((res) => {
      fila.push(res);
    }),
    responder: (status: number) => {
      const r = fila.shift();
      assert.ok(r, 'POST em andamento');
      r({ ok: status >= 200 && status < 300, status });
    },
    pendentes: () => fila.length,
  };
}

interface FakeUser {
  uid: string;
  tenantId: string | null;
  displayName?: string | null;
  email?: string | null;
  photoURL?: string | null;
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
  // Página sem o menu superior (sem #theme-toggle): o auth.js não mostra botão de conta.
  noHeader?: boolean;
  // Outro item no menu superior depois do botão de tema: o botão da conta entra entre os dois, não no fim.
  extraAfterTheme?: boolean;
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
  // Menu superior do site: nav > .nav-links > ... #lang-toggle, #theme-toggle (o botão da conta entra depois do tema).
  let navLinks: El | null = null;
  let themeToggle: El | null = null;
  if (!opts.noHeader) {
    const nav = body.appendChild(new El('nav', setActive));
    navLinks = nav.appendChild(new El('div', setActive));
    navLinks.setAttribute('class', 'nav-links');
    const lang = navLinks.appendChild(new El('button', setActive));
    lang.setAttribute('id', 'lang-toggle');
    themeToggle = navLinks.appendChild(new El('button', setActive));
    themeToggle.setAttribute('id', 'theme-toggle');
    if (opts.extraAfterTheme) navLinks.appendChild(new El('a', setActive)).setAttribute('id', 'nav-extra');
  }
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
    URL,
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
    navLinks,
    themeToggle,
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

// Aviso do login, região aria-live e interruptor do vínculo saíram em 28/11: nada disso pode existir no DOM.
function assertSemAvisoNemInterruptor(s: { body: El }, label = '') {
  const avisos = s.body.findAll((e) => /(^|\s)guifav-auth-(notice|live|yes|no|state)(\s|$)/.test(e.attrs.class ?? ''));
  assert.equal(avisos.length, 0, `${label}: nenhum aviso do login`);
  assert.equal(
    s.body.findAll((e) => e.attrs.role === 'menuitemcheckbox' || e.attrs.role === 'switch').length,
    0,
    `${label}: nenhum interruptor do vínculo`,
  );
  assert.equal(s.body.findAll((e) => 'aria-live' in e.attrs).length, 0, `${label}: nenhuma região aria-live`);
}

// Tudo o que sai ou fica visível (DOM com atributos, GA4, Worker, logs e storage) sem o email da conta.
function assertSemEmail(
  s: { body: El; gtagCalls: unknown[]; fetchCalls: unknown[]; warns: string[]; local: Storage; session: Storage },
  label = '',
) {
  const dump = JSON.stringify({
    dom: s.body.findAll(() => true).map((e) => [e.textContent, e.attrs]),
    gtag: s.gtagCalls,
    fetch: s.fetchCalls,
    warns: s.warns,
    local: [...s.local.m.entries()],
    session: [...s.session.m.entries()],
  });
  assert.ok(!dump.includes('maria.secreta'), `${label}: email não aparece`);
  assert.ok(!dump.includes('@'), `${label}: nenhum @`);
}

test('o auth.js não tem mais aviso do login, interruptor do vínculo nem exclusão de /prioridade', () => {
  for (const s of ['guifav-auth-notice', 'guifav-auth-yes', 'guifav-auth-no', 'guifav-auth-live', 'menuitemcheckbox',
    'aria-live', 'Ligar minha atividade', 'column-reverse', "'/prioridade'"]) {
    assert.ok(!AUTH_SRC.includes(s), s);
  }
});

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
    assert.equal(s.findClass('guifav-auth-btn')?.textContent, 'Entrar', `idbMode=${idbMode}`);
    if (idbMode === 'upgrade') {
      assert.equal(s.idbStats().upgradeAborted, true, 'onupgradeneeded deve abortar para não criar DB vazio');
    }
    if (idbMode === 'empty') {
      assert.equal(s.idbStats().openedName, 'firebaseLocalStorageDb');
      assert.equal(s.idbStats().requestedKey, IDB_USER_KEY);
      assert.equal(s.idbStats().dbClosed, true);
    }
  }

  // (b) Sessão salva no IndexedDB carrega Firebase mesmo antes do banner, sem One Tap e sem aviso: a conta aparece
  // no menu superior.
  const withSession = await setupAuth({ pathname: '/', idbMode: 'session', initialUser: makeUser() });
  assert.equal(withSession.firebaseImports.length, 2);
  const userBtn = withSession.findClass('guifav-auth-btn')!;
  assert.equal(userBtn.attrs.class, 'nav-btn guifav-auth-btn is-user');
  assert.equal(userBtn.attrs['aria-label'], 'Maria, menu da conta');
  assert.equal(userBtn.textContent, 'M', 'sem foto, a inicial do primeiro nome');
  assertSemAvisoNemInterruptor(withSession, 'antes do banner');
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

test('login antes do banner liga o vínculo sem aviso e manda o POST uma vez sem a origem; Não não identifica; Sim identifica sem repetir o POST', async () => {
  // Logado (sessão salva no IndexedDB) sem resposta no banner: o vínculo liga e o login vai ao servidor como origem
  // desconhecida, mesmo com a origem da visita disponível; nada vai ao GA4.
  const s = await setupAuth({ idbMode: 'session' });
  s.win.guifavOrigin = { current: () => ({ canal: 'busca', fonte: 'google' }) };
  await s.emitAuth(makeUser());
  // O servidor respondeu ok: o vínculo fica confirmado (s: 1), mas sem cookies aceitos o GA4 não recebe nada.
  assert.deepEqual(JSON.parse(s.local.getItem('heating-link-v1')!), linkOk(NOW));
  assert.equal(s.fetchCalls.length, 1);
  assert.equal(s.fetchCalls[0].url, IDENTITY_URL);
  assert.equal(s.fetchCalls[0].init?.body, 'origem_canal=desconhecida');
  assert.deepEqual(s.gtagCalls, []);
  assert.equal(s.local.getItem('heating-uid-v1'), null);
  assertSemAvisoNemInterruptor(s, 'sem resposta no banner');

  // "Não" no banner: só o user_id null defensivo; nada de heating-uid-v1 nem novo POST.
  await s.emitConsent('denied');
  assert.deepEqual(s.gtagCalls, [['set', { user_id: null }]]);
  assert.equal(s.local.getItem('heating-uid-v1'), null);
  assert.equal(s.fetchCalls.length, 1);
  assertSemAvisoNemInterruptor(s, 'Não');

  // "Sim" depois: identifica na hora (vínculo já confirmado), sem aviso e sem repetir o POST da mesma sessão.
  await s.emitConsent('granted');
  assert.deepEqual(s.gtagCalls.slice(1), identifySeq(UID));
  assert.deepEqual(JSON.parse(s.local.getItem('heating-uid-v1')!), { uid: UID, t: NOW });
  assert.equal(s.fetchCalls.length, 1, 'uma vez por sessão e por uid');
  assertSemAvisoNemInterruptor(s, 'Sim');
});

test('login com cookies recusados ou sem resposta: liga o vínculo e manda o POST /identidade uma vez com origem_canal=desconhecida, sem user_id nem heating-uid-v1', async () => {
  for (const caso of ['recusados', 'sem resposta'] as const) {
    const local = new Storage();
    const session = new Storage();
    if (caso === 'recusados') local.setItem('consent-v1', JSON.stringify({ v: 'denied', t: NOW - 1000 }));
    // Sobra de heating-uid-v1 de quando os cookies estavam aceitos.
    local.setItem('heating-uid-v1', JSON.stringify({ uid: UID, t: NOW - 1000 }));
    const s = await setupAuth({ local, session, idbMode: 'session' });
    s.win.guifavOrigin = {
      current: () => ({ canal: 'busca', fonte: 'google', utm_source: 'news', utm_campaign: 'lanc-1', entrada: '/blog/x/' }),
    };
    await s.emitAuth(makeUser());
    assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), linkOk(NOW), `${caso}: confirmado pelo servidor`);
    assert.deepEqual(JSON.parse(local.getItem('heating-login-v1')!), { t: NOW }, caso);
    assert.equal(s.fetchCalls.length, 1, caso);
    assert.equal(s.fetchCalls[0].url, IDENTITY_URL, caso);
    assert.equal(s.fetchCalls[0].init?.method, 'POST', caso);
    assert.deepEqual(
      s.fetchCalls[0].init?.headers,
      { Authorization: 'Bearer jwt.token.falso', 'Content-Type': 'application/x-www-form-urlencoded' },
      caso,
    );
    assert.equal(s.fetchCalls[0].init?.body, 'origem_canal=desconhecida', `${caso}: a origem só vai com cookies aceitos`);
    assert.deepEqual(s.gtagCalls, [], `${caso}: nada de user_id nem consentimento no GA4`);
    assert.equal(local.getItem('heating-uid-v1'), null, caso);
    assert.equal(session.getItem('guifav_identidade_sent_v2:UidCurso123ABC'), '1', caso);
    assertSemAvisoNemInterruptor(s, caso);
    assertSemEmail(s, caso);

    // Outra página na mesma sessão: não repete o POST nem manda nada ao GA4.
    const s2 = await setupAuth({ local, session, idbMode: 'session', initialUser: makeUser() });
    assert.equal(s2.fetchCalls.length, 0, caso);
    assert.deepEqual(s2.gtagCalls, [], caso);
    assert.equal(local.getItem('heating-uid-v1'), null, caso);
  }
});

test('Gui (28/11): todo login liga o vínculo; denied antigo sem o, vencido, quebrado ou de outro uid viram granted agora; só a oposição (o: 1) segura', async () => {
  for (const [caso, link] of [
    ['denied antigo do interruptor (sem o)', { v: 'denied', t: NOW - DAY, uid: UID }],
    ['granted vencido (> 365 dias)', { v: 'granted', t: NOW - 366 * DAY, uid: UID }],
    ['granted de outro uid', { v: 'granted', t: NOW - DAY, uid: 'UidOutraConta9' }],
    ['oposição de outro uid', { v: 'denied', t: NOW - DAY, uid: 'UidOutraConta9', o: 1 }],
    ['oposição vencida (> 365 dias)', { ...OPPOSED_LINK, t: NOW - 366 * DAY }],
    ['quebrado', '{nao-e-json'],
  ] as const) {
    const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, heatingLink: link, initialUser: makeUser() });
    assert.deepEqual(JSON.parse(s.local.getItem('heating-link-v1')!), linkOk(NOW), caso);
    assert.deepEqual(JSON.parse(s.local.getItem('heating-uid-v1')!), { uid: UID, t: NOW }, caso);
    // Sessão logada nega anúncios na hora; a identificação só vem com a resposta ok do POST.
    assert.deepEqual(s.gtagCalls, [...LOGIN_AD_DENY, ...identifySeq(UID)], caso);
    assert.equal(s.fetchCalls.length, 1, caso);
    assert.equal(s.fetchCalls[0].init?.body, 'origem_canal=desconhecida', caso);
  }

  // Vínculo confirmado numa sessão anterior (s: 1, sem a marca de envio desta aba): não identifica antes de o POST desta
  // sessão responder (rodada 2 do #212); com a resposta ok, identifica uma vez. Nem o login nem o POST ok reescrevem o
  // instante (a validade de 365 dias não é renovada a cada página).
  const kept = linkOk(NOW - DAY);
  const k = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, heatingLink: kept, initialUser: makeUser() });
  assert.deepEqual(JSON.parse(k.local.getItem('heating-link-v1')!), kept);
  assert.deepEqual(k.gtagCalls, [...LOGIN_AD_DENY, ...identifySeq(UID)]);
  assert.equal(k.fetchCalls.length, 1);

  // Vínculo ligado antes do s: 1 (sem confirmação): nega anúncios, e a primeira resposta ok confirma, renova o instante e
  // identifica.
  const legado = { v: 'granted', t: NOW - DAY, uid: UID };
  const l = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, heatingLink: legado, initialUser: makeUser() });
  assert.deepEqual(JSON.parse(l.local.getItem('heating-link-v1')!), linkOk(NOW));
  assert.deepEqual(l.gtagCalls, [...LOGIN_AD_DENY, ...identifySeq(UID)]);

  // Oposição válida do mesmo uid: a marca fica como está e nada vai ao Worker; com cookies aceitos, a sessão logada só
  // nega anúncios e sinais, sem user_id nem heating-uid-v1.
  for (const consent of [{ v: 'granted', t: NOW - 1000 }, { v: 'denied', t: NOW - 1000 }, undefined]) {
    const label = consent ? consent.v : 'sem resposta';
    const o = await setupAuth({
      consent,
      heatingLink: OPPOSED_LINK,
      heatingUid: { uid: UID, t: NOW - 1000 },
      idbMode: 'session',
      initialUser: makeUser(),
    });
    assert.deepEqual(JSON.parse(o.local.getItem('heating-link-v1')!), OPPOSED_LINK, label);
    assert.deepEqual(o.fetchCalls, [], label);
    assert.equal(o.local.getItem('heating-uid-v1'), null, label);
    assert.ok(!o.gtagCalls.some((c) => isUserIdSet(c, UID)), `${label}: nunca user_id`);
    assert.ok(!o.gtagCalls.some((c) => c[0] === 'event'), `${label}: nunca auth_identified`);
    if (label === 'granted') {
      // O <head> mandou o uid da sobra; o auth.js tira o user_id e mantém anúncios negados.
      assert.deepEqual(o.gtagCalls, [...LOGIN_AD_DENY, ['set', { user_id: null }]], label);
    } else {
      assert.deepEqual(o.gtagCalls, [], label);
    }
    assertSemAvisoNemInterruptor(o, label);
  }
});

test('tenant errado é ignorado; tenant certo mostra a conta no menu superior e liga o vínculo sem aviso', async () => {
  const wrongTenant = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidOutroTenant1' },
    initialUser: makeUser({ uid: 'UidOutroTenant1', tenantId: 'outro-tenant' }),
  });
  assert.equal(wrongTenant.authObj.tenantId, TENANT_ID);
  assert.equal(wrongTenant.findClass('guifav-auth-btn')?.textContent, 'Entrar');
  assert.equal(wrongTenant.findClass('guifav-auth-btn')?.attrs['aria-label'], 'Entrar com Google');
  assert.deepEqual(wrongTenant.gtagCalls, []);
  assert.deepEqual(wrongTenant.fetchCalls, []);
  assert.deepEqual(
    JSON.parse(wrongTenant.local.getItem('heating-link-v1')!),
    { v: 'granted', t: NOW - 1000, uid: 'UidOutroTenant1' },
    'outro tenant não mexe no vínculo',
  );
  assert.equal(wrongTenant.local.getItem('heating-login-v1'), null, 'outro tenant não marca login');

  const ok = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    initialUser: makeUser(),
  });
  const btn = ok.findClass('guifav-auth-btn')!;
  assert.equal(btn.attrs.class, 'nav-btn guifav-auth-btn is-user');
  assert.equal(btn.attrs['aria-label'], 'Maria, menu da conta');
  assert.equal(btn.attrs.title, 'Maria');
  assert.equal(ok.findClass('guifav-auth-name')?.textContent, 'Conectado como Maria');
  assertSemAvisoNemInterruptor(ok, 'tenant certo');
  // Sem aviso: o login liga o vínculo, nega anúncios e manda o POST /identidade (sem guifavOrigin, como origem
  // desconhecida); com a resposta ok, confirma o vínculo, grava heating-uid-v1 e identifica no GA4. A marca de login
  // (sem uid nem email) vai para o <head> das próximas páginas.
  assert.deepEqual(JSON.parse(ok.local.getItem('heating-link-v1')!), linkOk(NOW));
  assert.deepEqual(JSON.parse(ok.local.getItem('heating-uid-v1')!), { uid: UID, t: NOW });
  assert.deepEqual(ok.gtagCalls, [...LOGIN_AD_DENY, ...identifySeq(UID)]);
  assert.deepEqual(JSON.parse(ok.local.getItem('heating-login-v1')!), { t: NOW });
  assert.equal(ok.fetchCalls.length, 1);
  assert.equal(ok.fetchCalls[0].init?.body, 'origem_canal=desconhecida');
});

test('login com cookies aceitos liga o vínculo sozinho: as três condições do user_id, heating-uid-v1, sinais de anúncio desligados, POST com a origem uma vez por sessão e zero vazamento de email', async () => {
  const local = new Storage();
  const session = new Storage();
  local.setItem('consent-v1', JSON.stringify({ v: 'granted', t: NOW - 1000 }));

  const s = await setupAuth({ local, session });
  s.win.guifavOrigin = { current: () => ({ canal: 'busca', fonte: 'google', entrada: '/blog/x/' }) };
  await s.emitAuth(makeUser());

  // Vínculo (confirmado pelo servidor) e heating-uid-v1 gravados com validade, sem aviso nem clique.
  assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), linkOk(NOW));
  assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), { uid: UID, t: NOW });
  assert.deepEqual(JSON.parse(local.getItem('heating-login-v1')!), { t: NOW });
  assertSemAvisoNemInterruptor(s, 'login');

  // Ordem no GA4: a sessão logada nega anúncios e sinais na hora; com a resposta ok do POST, consentimento de anúncio
  // negado (sem mexer em analytics_storage) e sinais desligados antes de user_id, seguido de auth_identified.
  assert.deepEqual(s.gtagCalls, [...LOGIN_AD_DENY, ...identifySeq(UID)]);

  // POST /identidade com Bearer e a origem da visita (cookies aceitos).
  assert.equal(s.fetchCalls.length, 1);
  assert.equal(s.fetchCalls[0].url, IDENTITY_URL);
  assert.equal(s.fetchCalls[0].init?.method, 'POST');
  assert.deepEqual(s.fetchCalls[0].init?.headers, { Authorization: 'Bearer jwt.token.falso', 'Content-Type': 'application/x-www-form-urlencoded' });
  assert.equal(s.fetchCalls[0].init?.body, 'origem_canal=busca&origem_fonte=google&origem_entrada=%2Fblog%2Fx%2F');
  assert.equal(session.getItem('guifav_identidade_sent_v2:UidCurso123ABC'), '1');

  // Nunca chama Clarity nem vaza email em nenhum canal, storage ou DOM.
  assert.deepEqual(s.clarityCalls, []);
  assertSemEmail(s, 'login');

  // Segunda página na mesma sessão, um minuto depois: renova heating-uid-v1, reaplica a negação e o user_id, mas NÃO
  // repete auth_identified nem o POST e não reescreve o vínculo.
  const s2 = await setupAuth({ local, session, now: NOW + MIN, initialUser: makeUser() });
  assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), { uid: UID, t: NOW + MIN });
  assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), linkOk(NOW));
  assert.deepEqual(s2.gtagCalls, identifySeq(UID, false), 'confirmado: identifica na hora, sem negar antes');
  assert.equal(s2.fetchCalls.length, 0);

  // Outro uid na mesma sessão não herda nada do primeiro: o login dele liga o vínculo dele e identifica, com POST e
  // auth_identified próprios.
  const t3 = NOW + 2 * MIN;
  const otherUid = await setupAuth({
    local,
    session,
    now: t3,
    initialUser: makeUser({ uid: 'UidDiferente999', displayName: 'Ana Paula' }),
  });
  assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), linkOk(t3, 'UidDiferente999'));
  assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), { uid: 'UidDiferente999', t: t3 });
  // O <head> mandou o uid anterior (heating-uid-v1 da sessão): ele sai na hora, e o novo uid só entra com a confirmação.
  assert.deepEqual(otherUid.gtagCalls, [...LOGIN_AD_DENY, ['set', { user_id: null }], ...identifySeq('UidDiferente999')]);
  assert.equal(otherUid.fetchCalls.length, 1);
  assert.equal(otherUid.findClass('guifav-auth-btn')?.attrs['aria-label'], 'Ana, menu da conta');

  // Troca de usuário na mesma página sem reload: o user_id anterior sai na hora e o novo uid só é identificado depois de
  // o POST dele confirmar o vínculo dele; o botão passa a mostrar a conta nova.
  const switchUser = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: UID },
    initialUser: makeUser(),
  });
  const callsBeforeSwitch = switchUser.gtagCalls.length;
  await switchUser.emitAuth(makeUser({ uid: 'UidDiferente999', displayName: 'Ana Paula' }));
  assert.deepEqual(switchUser.gtagCalls.slice(callsBeforeSwitch), [['set', { user_id: null }], ...identifySeq('UidDiferente999')]);
  assert.deepEqual(JSON.parse(switchUser.local.getItem('heating-uid-v1')!), { uid: 'UidDiferente999', t: NOW });
  assert.deepEqual(JSON.parse(switchUser.local.getItem('heating-link-v1')!), linkOk(NOW, 'UidDiferente999'));
  assert.equal(switchUser.fetchCalls.length, 2);
  const switchBtn = switchUser.findClass('guifav-auth-btn')!;
  assert.equal(switchBtn.attrs['aria-label'], 'Ana, menu da conta');
  assert.equal(switchBtn.attrs.title, 'Ana');
  assert.equal(switchBtn.textContent, 'A');
  assert.equal(switchUser.findClass('guifav-auth-name')?.textContent, 'Conectado como Ana');

  // Se heating-uid-v1 existia (snippet do <head> enviou user_id e negou anúncios), mas a sessão Firebase não existe mais:
  // limpa a chave e envia user_id null; anúncios seguem negados (vínculo ligado).
  const staleSession = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: UID },
    heatingUid: { uid: UID, t: NOW - 1000 },
    initialUser: null,
  });
  assert.equal(staleSession.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(staleSession.gtagCalls, [['set', { user_id: null }]]);
  assert.deepEqual(staleSession.fetchCalls, []);
});

test('limpeza de heating-uid-v1, user_id null literal e consentimento de anúncios em: logout e revogação de cookies; Sair suprime One Tap na mesma página', async () => {
  // 1. Logout ("Sair"): limpa heating-uid-v1 e user_id, marca supressão na sessão, chama disableAutoSelect() e NÃO dispara
  // One Tap; anúncios seguem negados (o vínculo deste navegador continua ligado).
  const s1 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: UID },
    initialUser: makeUser(),
    gisAvailable: true,
  });
  assert.ok(s1.local.getItem('heating-uid-v1'));
  const callsBeforeSair = s1.gtagCalls.length;
  await s1.click(s1.findClass('guifav-auth-btn')!);
  const sair = s1.body.findAll((e) => e.attrs.role === 'menuitem')[0];
  assert.equal(sair.textContent, 'Sair');
  await s1.click(sair);
  assert.equal(s1.signOutCalls(), 1);
  assert.equal(s1.local.getItem('heating-uid-v1'), null);
  assert.equal(s1.local.getItem('heating-login-v1'), null);
  assert.equal(s1.session.getItem('guifav_one_tap_suppressed'), '1');
  assert.equal(s1.oneTapDisableAutoSelects.length, 1);
  assert.equal(s1.oneTapPrompts.length, 0, 'Sair não pode disparar One Tap na mesma página');
  const sairCalls = s1.gtagCalls.slice(callsBeforeSair);
  assert.deepEqual(sairCalls.slice(0, 1), [['set', { user_id: null }]]);
  assert.equal((sairCalls[0][1] as { user_id: unknown }).user_id, null, 'null literal, não string vazia');
  assert.equal(
    sairCalls.filter((c) => c[0] === 'consent' && c[1] === 'update').length,
    0,
    'com o vínculo ligado no navegador, o logout não devolve anúncios',
  );
  assert.equal(s1.findClass('guifav-auth-btn')?.textContent, 'Entrar');

  // 1b. Mesmo com signOut lento/pendente, o clique em "Sair" já limpa heating-uid-v1 e manda user_id null sincronamente.
  const s1b = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: UID },
    initialUser: makeUser(),
    signOutFn: () => new Promise<void>(() => {}),
  });
  await s1b.click(s1b.findClass('guifav-auth-btn')!);
  const callsBeforeOut = s1b.gtagCalls.length;
  await s1b.click(s1b.body.findAll((e) => e.attrs.role === 'menuitem')[0]);
  assert.equal(s1b.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s1b.gtagCalls.slice(callsBeforeOut), [['set', { user_id: null }]]);

  // 2. Visitante muda banner de cookies para "Não" (guifav-consent denied): limpa user_id, NÃO devolve anúncios para
  // granted e não manda outro POST.
  const s2 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: UID },
    initialUser: makeUser(),
  });
  assert.ok(s2.local.getItem('heating-uid-v1'));
  const countBefore = s2.fetchCalls.length;
  const callsBeforeDeny = s2.gtagCalls.length;
  await s2.emitConsent('denied');
  assert.equal(s2.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s2.gtagCalls.slice(callsBeforeDeny), [['set', { user_id: null }]]);
  assert.equal((s2.gtagCalls[callsBeforeDeny][1] as { user_id: unknown }).user_id, null);
  assert.equal(s2.fetchCalls.length, countBefore);
  assert.deepEqual(JSON.parse(s2.local.getItem('heating-link-v1')!), linkOk(NOW), 'o vínculo (confirmado no carregamento) fica');

  // 2b. Se aceitar cookies de novo na mesma página com login e heating-link-v1 ativos, volta a negar anúncios antes de
  // setar user_id, sem repetir auth_identified nem o POST.
  const callsBeforeRegrant = s2.gtagCalls.length;
  await s2.emitConsent('granted');
  assert.deepEqual(s2.gtagCalls.slice(callsBeforeRegrant), identifySeq(UID, false));
  assert.equal(s2.fetchCalls.length, countBefore);
});

test('outra aba (evento storage): oposição gravada lá, cookies revogados, heating-uid-v1 apagado ou trocado e localStorage.clear() mandam user_id null aqui sem devolver anúncios', async () => {
  // 1. Outra aba recebe 409 do /identidade e grava a oposição -> user_id null aqui também, sem religar o vínculo nem
  // mandar outro POST; logado, os anúncios seguem negados.
  const s1 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: UID },
    initialUser: makeUser(),
  });
  const calls1 = s1.gtagCalls.length;
  const fetch1 = s1.fetchCalls.length;
  const oposicao = JSON.stringify({ v: 'denied', t: NOW, uid: UID, o: 1 });
  await s1.emitStorage('heating-link-v1', oposicao);
  assert.equal(s1.local.getItem('heating-link-v1'), oposicao, 'esta aba não religa o vínculo');
  assert.equal(s1.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s1.gtagCalls.slice(calls1), [['set', { user_id: null }]]);
  assert.equal(s1.fetchCalls.length, fetch1);
  assertSemAvisoNemInterruptor(s1, 'oposição em outra aba');

  // 2. Outra aba revoga consent-v1 -> manda user_id null e NÃO devolve anúncios para granted.
  const s2 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: UID },
    initialUser: makeUser(),
  });
  const calls2 = s2.gtagCalls.length;
  await s2.emitStorage('consent-v1', JSON.stringify({ v: 'denied', t: NOW }));
  assert.equal(s2.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s2.gtagCalls.slice(calls2), [['set', { user_id: null }]]);

  // 3. Outra aba remove heating-uid-v1 (ex.: logout lá) -> manda user_id null sem reescrever heating-uid-v1; anúncios
  // seguem negados (vínculo ligado).
  const s3 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: UID },
    initialUser: makeUser(),
  });
  const calls3 = s3.gtagCalls.length;
  await s3.emitStorage('heating-uid-v1', null);
  assert.equal(s3.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(s3.gtagCalls.slice(calls3), [['set', { user_id: null }]]);

  // 4. Outra aba troca heating-uid-v1 para outro uid -> manda user_id null nesta aba sem apagar o uid da outra aba;
  // anúncios seguem negados.
  const s4 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: UID },
    initialUser: makeUser(),
  });
  const calls4 = s4.gtagCalls.length;
  const otherUidVal = JSON.stringify({ uid: 'UidOutraConta9', t: NOW });
  await s4.emitStorage('heating-uid-v1', otherUidVal);
  assert.equal(s4.local.getItem('heating-uid-v1'), otherUidVal);
  assert.deepEqual(s4.gtagCalls.slice(calls4), [['set', { user_id: null }]]);

  // 5. Outra aba chama localStorage.clear() (key === null) -> manda user_id null, NÃO devolve anúncios para granted e não
  // religa o vínculo apagado (só o próximo login liga).
  const s5 = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: UID },
    initialUser: makeUser(),
  });
  const calls5 = s5.gtagCalls.length;
  await s5.emitStorage(null, null);
  assert.deepEqual(s5.gtagCalls.slice(calls5), [['set', { user_id: null }]]);
  assert.equal(s5.local.getItem('heating-link-v1'), null);
});

test('getIdToken pendente com Sair, oposição gravada em outra aba ou troca de conta no meio não faz POST /identidade com o token antigo', async () => {
  const pendente = () => {
    let resolveToken: ((tok: string) => void) | null = null;
    const user = makeUser({
      getIdToken: () => new Promise<string>((resolve) => {
        resolveToken = resolve;
      }),
    });
    return { user, resolve: (tok: string) => resolveToken?.(tok) };
  };

  // 1. Sair no menu enquanto getIdToken ainda está pendente: nenhum fetch sai depois.
  const p1 = pendente();
  const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: p1.user });
  assert.equal(s.fetchCalls.length, 0);
  await s.click(s.findClass('guifav-auth-btn')!);
  await s.click(s.body.findAll((e) => e.attrs.role === 'menuitem')[0]);
  assert.equal(s.signOutCalls(), 1);
  p1.resolve('jwt.tardio.saiu');
  await flush();
  assert.equal(s.fetchCalls.length, 0);

  // 2. Outra aba grava a oposição (409 lá) enquanto o token daqui está pendente: nenhum fetch.
  const p2 = pendente();
  const r = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: p2.user });
  await r.emitStorage('heating-link-v1', JSON.stringify({ v: 'denied', t: NOW, uid: UID, o: 1 }));
  p2.resolve('jwt.tardio.oposicao');
  await flush();
  assert.equal(r.fetchCalls.length, 0);

  // 3. Outra conta entra na mesma página antes do token: só a conta nova vai ao servidor, com o token dela.
  const p3 = pendente();
  const q = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: p3.user });
  await q.emitAuth(makeUser({ uid: 'UidOutraConta9', displayName: 'Outra Pessoa' }));
  p3.resolve('jwt.tardio.trocou');
  await flush();
  assert.equal(q.fetchCalls.length, 1);
  assert.equal((q.fetchCalls[0].init?.headers as Record<string, string>).Authorization, 'Bearer jwt.token.falso');
});

test('Clarity mask e acessibilidade: botão no menu superior, menu com o nome e um item Sair, aria-label com nome, setas, clique fora e Escape', async () => {
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    initialUser: makeUser(),
  });

  // Clarity mask no container do botão e no menu.
  const root = s.findClass('guifav-auth')!;
  const btn = s.findClass('guifav-auth-btn')!;
  const menu = s.findClass('guifav-auth-menu')!;
  assert.equal(root.tag, 'span');
  assert.equal(root.attrs['data-clarity-mask'], 'true');
  assert.equal(menu.attrs['data-clarity-mask'], 'true');

  // Ordem no DOM: botão antes do menu; o menu abre para baixo do botão (sem column-reverse).
  assert.deepEqual(root.children.map((c) => c.attrs.class), ['nav-btn guifav-auth-btn is-user', 'guifav-auth-menu']);
  const style = s.head.findAll((e) => e.attrs.id === 'guifav-auth-style')[0];
  assert.ok(style);
  assert.ok(!style.textContent.includes('column-reverse'));
  assert.ok(style.textContent.includes('.guifav-auth-menu{position:absolute;top:calc(100% + 8px);right:0'));

  // Botão: nome no aria-label e no title (a foto ou a inicial não têm texto legível).
  assert.equal(btn.tag, 'button');
  assert.equal(btn.attrs.type, 'button');
  assert.equal(btn.attrs['aria-label'], 'Maria, menu da conta');
  assert.equal(btn.attrs.title, 'Maria');
  assert.equal(btn.attrs['aria-haspopup'], 'menu');
  assert.equal(btn.attrs['aria-controls'], 'guifav-auth-menu');
  assert.equal(btn.attrs['aria-expanded'], 'false');

  // Painel (escondido, mascarado) sem role nem id: o nome fica fora do menu ARIA, que só tem o item "Sair" (P3 da
  // revisão do #212: um role="menu" só tem itens).
  assert.equal(menu.hidden, true);
  assert.equal(menu.getAttribute('role'), null);
  assert.equal(menu.getAttribute('id'), null);
  assert.deepEqual(menu.children.map((c) => c.attrs.class ?? c.attrs.id), ['guifav-auth-name', 'guifav-auth-menu']);
  const nome = menu.children[0];
  assert.equal(nome.textContent, 'Conectado como Maria');
  assert.equal(nome.getAttribute('role'), null);
  const list = menu.children[1];
  assert.equal(list.tag, 'div');
  assert.equal(list.attrs.id, 'guifav-auth-menu', 'o aria-controls do botão aponta para o menu ARIA');
  assert.equal(list.attrs.role, 'menu');
  assert.equal(list.attrs['aria-label'], 'Conta');
  assert.equal(s.body.findAll((e) => e.attrs.id === 'guifav-auth-menu').length, 1);
  assert.equal(s.body.findAll((e) => e.attrs.role === 'menu').length, 1);
  assert.deepEqual(list.children.map((c) => c.attrs.role), ['menuitem']);
  assert.equal(menu.findAll((e) => e.attrs.role === 'menuitem').length, 1);
  const sair = list.children[0];
  assert.equal(sair.tag, 'button');
  assert.equal(sair.textContent, 'Sair');
  assertSemAvisoNemInterruptor(s, 'menu');

  // Teclas com o menu fechado não fazem nada.
  assert.equal(s.keydown('ArrowDown'), false);

  // Abrir foca o item; setas, Home e End ficam no único item e evitam a rolagem da página.
  await s.click(btn);
  assert.equal(menu.hidden, false);
  assert.equal(btn.attrs['aria-expanded'], 'true');
  assert.equal(s.activeElement(), sair, 'abrir menu foca o primeiro item');
  for (const key of ['ArrowDown', 'ArrowUp', 'Home', 'End']) {
    assert.equal(s.keydown(key), true, key);
    assert.equal(s.activeElement(), sair, key);
  }

  // Escape fecha menu e devolve foco ao botão.
  assert.equal(s.keydown('Escape'), true);
  assert.equal(menu.hidden, true);
  assert.equal(btn.attrs['aria-expanded'], 'false');
  assert.equal(s.activeElement(), btn);

  // Clique fora fecha o menu.
  await s.click(btn);
  assert.equal(menu.hidden, false);
  await s.clickOutside();
  assert.equal(menu.hidden, true);

  // Clique no botão com o menu aberto fecha e devolve o foco ao botão.
  await s.click(btn);
  assert.equal(s.activeElement(), sair);
  await s.click(btn);
  assert.equal(menu.hidden, true);
  assert.equal(s.activeElement(), btn);
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

test('heating-uid-v1 tem validade deslizante de 30 min: renova a cada página, não ultrapassa heating-link-v1 e nunca grava com o vínculo vencido sem um novo login', async () => {
  const local = new Storage();
  const session = new Storage();
  local.setItem('consent-v1', JSON.stringify({ v: 'granted', t: NOW - DAY }));
  local.setItem('heating-link-v1', JSON.stringify({ v: 'granted', t: NOW - DAY, uid: UID }));

  // Página 1 em NOW: grava heating-uid-v1 com t = NOW.
  await setupAuth({ local, session, now: NOW, initialUser: makeUser() });
  assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), { uid: UID, t: NOW });

  // Página 2 em NOW + 20 min (dentro dos 30 min): renova t para NOW + 20 min após confirmar as 3 condições.
  const t2 = NOW + 20 * MIN;
  await setupAuth({ local, session, now: t2, initialUser: makeUser() });
  assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), { uid: UID, t: t2 });

  // Página 3 em t2 + 31 min (passou de 30 min): se deslogou no curso, auth.js limpa a chave vencida sem precisar mandar
  // user_id null (o snippet do <head> já ignorou a chave vencida).
  const t3 = t2 + 31 * MIN;
  const loggedOut = await setupAuth({ local, session, now: t3, initialUser: null });
  assert.equal(local.getItem('heating-uid-v1'), null);
  assert.deepEqual(loggedOut.gtagCalls, [], 'head não enviou user_id com chave vencida (> 30 min)');

  // Nova visita em t3 + 5 min com Firebase ainda logado: auth.js confirma as 3 condições e grava novo t.
  const t4 = t3 + 5 * MIN;
  await setupAuth({ local, session, now: t4, initialUser: makeUser() });
  assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), { uid: UID, t: t4 });

  // Quando heating-link-v1 (confirmado) está a 5 min de vencer (dia 365): heating-uid-v1 limita t para expirar junto com
  // heating-link-v1 (nem o login nem o POST ok renovam um vínculo confirmado ainda válido).
  const linkStart = NOW - 365 * DAY + 5 * MIN;
  const nearExpiry = await setupAuth({
    now: NOW,
    consent: { v: 'granted', t: NOW - DAY },
    heatingLink: linkOk(linkStart),
    initialUser: makeUser(),
  });
  const written = JSON.parse(nearExpiry.local.getItem('heating-uid-v1')!);
  assert.equal(written.uid, UID);
  assert.equal(written.t + 30 * MIN, linkStart + 365 * DAY, 'não pode ultrapassar a validade de heating-link-v1');
  assert.equal(JSON.parse(nearExpiry.local.getItem('heating-link-v1')!).t, linkStart);

  // Quando heating-link-v1 já venceu e o Firebase não tem sessão: nunca grava heating-uid-v1 e remove a sobra; sem
  // vínculo nem login válidos, o user_id do <head> sai e os anúncios voltam ao consentimento comum.
  const expiredLink = await setupAuth({
    now: NOW,
    consent: { v: 'granted', t: NOW - DAY },
    heatingLink: { v: 'granted', t: NOW - 365 * DAY, uid: UID },
    heatingUid: { uid: UID, t: NOW - 5 * MIN },
    initialUser: null,
  });
  assert.equal(expiredLink.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(expiredLink.gtagCalls, [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
    ['set', 'allow_google_signals', true],
    ['set', 'allow_ad_personalization_signals', true],
  ]);

  // Vínculo vencido com a sessão logada: o login renova o vínculo (t = agora) e heating-uid-v1 volta com a validade cheia.
  const renewed = await setupAuth({
    now: NOW,
    consent: { v: 'granted', t: NOW - DAY },
    heatingLink: { v: 'granted', t: NOW - 365 * DAY, uid: UID },
    heatingUid: { uid: UID, t: NOW - 5 * MIN },
    initialUser: makeUser(),
  });
  assert.deepEqual(JSON.parse(renewed.local.getItem('heating-link-v1')!), linkOk(NOW));
  assert.deepEqual(JSON.parse(renewed.local.getItem('heating-uid-v1')!), { uid: UID, t: NOW });
  // O uid que o <head> mandou com o vínculo vencido sai até o servidor confirmar o vínculo renovado.
  assert.deepEqual(renewed.gtagCalls, [...LOGIN_AD_DENY, ['set', { user_id: null }], ...identifySeq(UID)]);

  // Evento storage de heating-uid-v1 vindo de outra aba com mesmo uid não reescreve heating-uid-v1 (evita loop entre abas).
  const tab = await setupAuth({
    now: NOW,
    consent: { v: 'granted', t: NOW - DAY },
    heatingLink: { v: 'granted', t: NOW - DAY, uid: UID },
    initialUser: makeUser(),
  });
  tab.setNow(NOW + 5000);
  const fromOtherTab = JSON.stringify({ uid: UID, t: NOW + 1000 });
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
  // Nada de user_id null nem anúncio concedido antes de o Firebase responder (os sinais do Sim são do consent.js).
  assert.deepEqual(s.gtagCalls.slice(before), []);

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
  // Outra aba liga o vínculo de novo, aceita os cookies e renova o mesmo uid: o uid fica, nada de user_id null; o vínculo
  // ligado lá só reafirma aqui a negação de anúncios e sinais.
  const s = await setupAuth(base);
  const before = s.gtagCalls.length;
  await s.emitStorage('heating-link-v1', JSON.stringify({ v: 'granted', t: NOW, uid: 'UidCurso123ABC' }));
  await s.emitStorage('consent-v1', JSON.stringify({ v: 'granted', t: NOW }));
  const renovado = JSON.stringify({ uid: 'UidCurso123ABC', t: NOW });
  s.local.setItem('heating-uid-v1', renovado);
  await s.emitStorage('heating-uid-v1', renovado);
  assert.ok(s.local.getItem('heating-uid-v1'), 'o uid do <head> continua');
  assert.deepEqual(s.gtagCalls.slice(before), LOGIN_AD_DENY, 'nada de user_id null antes de o Firebase responder');

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

test('oposição: /identidade responde 409, o vínculo desliga com o: 1 neste navegador, o user_id sai e o próximo login não religa', async () => {
  const local = new SpyStorage();
  local.setItem('consent-v1', JSON.stringify({ v: 'granted', t: NOW - 1000 }));
  const s = await setupAuth({ local, initialUser: makeUser(), identityStatus: 409 });
  assert.equal(s.fetchCalls.length, 1);
  assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), { v: 'denied', t: NOW, uid: UID, o: 1 });
  assert.equal(local.getItem('heating-uid-v1'), null);
  // P2 da revisão do #212: sem a confirmação do servidor, o GA4 nunca identificou; o 409 só manda o user_id null
  // defensivo e, logado, os anúncios seguem negados (nada de consentimento concedido).
  assert.deepEqual(s.gtagCalls, [...LOGIN_AD_DENY, ['set', { user_id: null }]]);
  assert.ok(!local.writes.some(([k]) => k === 'heating-uid-v1'), 'heating-uid-v1 nunca gravado');
  assert.ok(!local.writes.some(([k, v]) => k === 'heating-link-v1' && JSON.parse(v).s === 1), 'nunca confirmado');
  assert.equal(s.session.getItem('guifav_auth_identified:UidCurso123ABC'), null);
  assert.equal(s.session.getItem('guifav_identidade_sent_v2:UidCurso123ABC'), null, 'não marca como enviada');
  assert.equal(s.pendingTimers(), 0);
  assert.equal(s.warns.length, 0, 'oposição não é falha');
  // A conta continua no menu superior, sem interruptor nem aviso.
  const btn = s.findClass('guifav-auth-btn')!;
  assert.equal(btn.attrs.class, 'nav-btn guifav-auth-btn is-user');
  assertSemAvisoNemInterruptor(s, '409');

  // Próxima página (sessão nova, um dia depois) com o mesmo uid: o login não religa, nada vai ao Worker e o GA4 só
  // recebe a negação de anúncios da sessão logada, sem user_id.
  const next = await setupAuth({ local, now: NOW + DAY, initialUser: makeUser() });
  assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), { v: 'denied', t: NOW, uid: UID, o: 1 });
  assert.deepEqual(next.fetchCalls, []);
  assert.deepEqual(next.gtagCalls, LOGIN_AD_DENY);
  assert.equal(local.getItem('heating-uid-v1'), null);

  // Sair e entrar de novo na mesma página também não religa.
  await next.click(next.findClass('guifav-auth-btn')!);
  await next.click(next.body.findAll((e) => e.attrs.role === 'menuitem')[0]);
  await next.emitAuth(makeUser());
  assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), { v: 'denied', t: NOW, uid: UID, o: 1 });
  assert.deepEqual(next.fetchCalls, []);
  assert.ok(!next.gtagCalls.some((c) => isUserIdSet(c, UID)), 'nunca user_id');
  assert.ok(!next.gtagCalls.some((c) => c[0] === 'event'), 'nunca auth_identified');

  // Com cookies recusados, o 409 grava a mesma marca (o user_id null é só defensivo) e a próxima página não manda nada.
  const dl = new Storage();
  dl.setItem('consent-v1', JSON.stringify({ v: 'denied', t: NOW - 1000 }));
  const d = await setupAuth({ local: dl, initialUser: makeUser(), identityStatus: 409 });
  assert.equal(d.fetchCalls.length, 1);
  assert.equal(d.fetchCalls[0].init?.body, 'origem_canal=desconhecida');
  assert.deepEqual(JSON.parse(dl.getItem('heating-link-v1')!), { v: 'denied', t: NOW, uid: UID, o: 1 });
  assert.deepEqual(d.gtagCalls, [['set', { user_id: null }]]);
  const d2 = await setupAuth({ local: dl, initialUser: makeUser() });
  assert.deepEqual(d2.fetchCalls, []);
  assert.deepEqual(d2.gtagCalls, []);
});

test('cabeçalho (28/11): o botão da conta entra logo depois de #theme-toggle dentro de .nav-links; sem o menu superior, nenhum botão e o login continua', async () => {
  const isRoot = (e: El) => (e.attrs.class ?? '').split(' ').includes('guifav-auth');
  const styles = (x: { head: El }) => x.head.findAll((e) => e.attrs.id === 'guifav-auth-style');

  // Deslogado: "Entrar" logo depois do botão de tema, dentro de .nav-links.
  const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 } });
  const root = s.findClass('guifav-auth')!;
  assert.ok(root);
  assert.equal(root.tag, 'span');
  assert.equal(root.parentNode, s.navLinks);
  assert.equal(s.themeToggle!.nextSibling, root, 'logo depois do botão de tema');
  assert.deepEqual(s.navLinks!.children.map((c) => c.attrs.id ?? c.attrs.class), ['lang-toggle', 'theme-toggle', 'guifav-auth']);
  assert.equal(s.body.findAll(isRoot).length, 1);
  assert.equal(styles(s).length, 1);
  const btn = root.children[0];
  assert.equal(btn.tag, 'button');
  assert.equal(btn.attrs.type, 'button');
  assert.equal(btn.attrs.class, 'nav-btn guifav-auth-btn');
  assert.equal(btn.textContent, 'Entrar');
  assert.equal(btn.attrs['aria-label'], 'Entrar com Google');
  for (const a of ['title', 'aria-haspopup', 'aria-controls', 'aria-expanded']) assert.equal(btn.getAttribute(a), null, a);
  const menu = root.children[1];
  assert.equal(menu.attrs.class, 'guifav-auth-menu');
  assert.equal(menu.hidden, true);
  assert.equal(root.findAll((e) => e.attrs.id === 'guifav-auth-menu')[0]?.attrs.role, 'menu', 'aria-controls aponta para o menu ARIA');
  assertSemAvisoNemInterruptor(s, 'deslogado');

  // Login na mesma página: o mesmo botão vira a conta, sem duplicar nem sair do lugar.
  await s.emitAuth(makeUser());
  assert.equal(s.body.findAll(isRoot).length, 1);
  assert.equal(s.findClass('guifav-auth'), root);
  assert.equal(s.themeToggle!.nextSibling, root);
  assert.equal(root.children[0], btn);
  assert.equal(btn.attrs.class, 'nav-btn guifav-auth-btn is-user');
  assert.equal(btn.attrs['aria-label'], 'Maria, menu da conta');
  assert.equal(btn.attrs.title, 'Maria');
  assert.equal(btn.attrs['aria-haspopup'], 'menu');
  assert.equal(btn.attrs['aria-controls'], 'guifav-auth-menu');
  assert.equal(btn.attrs['aria-expanded'], 'false');
  assert.ok(!btn.textContent.includes('Entrar'), 'o texto Entrar sai');
  assert.deepEqual(btn.children.map((c) => c.attrs.class), ['guifav-auth-initial']);
  assert.equal(styles(s).length, 1, 'estilo injetado uma vez');
  assertSemAvisoNemInterruptor(s, 'logado');

  // Logout: volta a "Entrar", sem foto nem inicial e sem os atributos do menu.
  await s.emitAuth(null);
  assert.equal(btn.attrs.class, 'nav-btn guifav-auth-btn');
  assert.equal(btn.textContent, 'Entrar');
  assert.deepEqual(btn.children.map((c) => c.tag), ['#text']);
  assert.equal(btn.attrs['aria-label'], 'Entrar com Google');
  for (const a of ['title', 'aria-haspopup', 'aria-controls', 'aria-expanded']) assert.equal(btn.getAttribute(a), null, a);
  assert.equal(s.findClass('guifav-auth-name')?.textContent, '');
  assert.equal(menu.hidden, true);

  // Página sem o menu superior (sem #theme-toggle): nenhum botão nem estilo, mas o login liga o vínculo, identifica e vai
  // ao Worker; o logout também funciona sem botão.
  const n = await setupAuth({ noHeader: true, consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser() });
  assert.equal(n.navLinks, null);
  assert.equal(n.body.findAll(isRoot).length, 0);
  assert.equal(n.findClass('guifav-auth-btn'), null);
  assert.equal(n.findClass('guifav-auth-menu'), null);
  assert.equal(styles(n).length, 0);
  assert.deepEqual(JSON.parse(n.local.getItem('heating-link-v1')!), linkOk(NOW));
  assert.deepEqual(n.gtagCalls, [...LOGIN_AD_DENY, ...identifySeq(UID)]);
  assert.equal(n.fetchCalls.length, 1);
  assert.deepEqual(n.warns, []);
  await n.emitAuth(null);
  assert.equal(n.local.getItem('heating-uid-v1'), null);
  assert.equal(n.local.getItem('heating-login-v1'), null);
  assert.equal(n.body.findAll(isRoot).length, 0);

  // Com outro item depois do botão de tema, a conta entra entre os dois (logo depois do tema), não no fim.
  const x = await setupAuth({ extraAfterTheme: true, consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser() });
  assert.deepEqual(x.navLinks!.children.map((c) => c.attrs.id ?? c.attrs.class), ['lang-toggle', 'theme-toggle', 'guifav-auth', 'nav-extra']);
  assert.equal(x.themeToggle!.nextSibling, x.findClass('guifav-auth'));

  // Sem o menu superior e deslogado: o One Tap continua valendo, entra no tenant e a sessão identifica normalmente.
  const o = await setupAuth({ noHeader: true, consent: { v: 'granted', t: NOW - 1000 }, initialUser: null, gisAvailable: true });
  assert.equal(o.body.findAll(isRoot).length, 0);
  assert.equal(o.oneTapPrompts.length, 1);
  const cb = o.oneTapInits[0].callback as (r: { credential: string }) => void;
  cb({ credential: 'google-id-token-xyz' });
  await flush();
  assert.equal(o.signInCredCalls.length, 1);
  assert.equal(o.signInCredCalls[0].tenantId, TENANT_ID);
  await o.emitAuth(makeUser());
  assert.deepEqual(o.gtagCalls, [...LOGIN_AD_DENY, ...identifySeq(UID)]);
  assert.equal(o.fetchCalls.length, 1);
  assert.equal(o.body.findAll(isRoot).length, 0);
});

test('avatar (28/11): foto do Google por https vira img sem referrer; outro host, http ou lixo vira a inicial; erro da imagem troca pela inicial; menu com o primeiro nome e nada de email', async () => {
  const FOTO = 'https://lh3.googleusercontent.com/a/ACg8ocJfotoFalsa=s96-c';
  const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser({ photoURL: FOTO }) });
  const btn = s.findClass('guifav-auth-btn')!;
  assert.equal(btn.children.length, 1);
  const img = btn.children[0];
  assert.equal(img.tag, 'img');
  assert.equal(img.attrs.class, 'guifav-auth-avatar');
  assert.equal(img.attrs.src, FOTO);
  assert.equal(img.attrs.alt, '', 'decorativa: o nome está no aria-label');
  assert.equal(img.attrs.referrerpolicy, 'no-referrer');
  assert.equal(img.attrs.width, '24');
  assert.equal(img.attrs.height, '24');
  assert.equal(btn.textContent, '');
  assert.equal(btn.attrs['aria-label'], 'Maria, menu da conta');
  assert.equal(btn.attrs.title, 'Maria');
  assert.equal(s.findClass('guifav-auth-initial'), null);
  assert.equal(s.findClass('guifav-auth-name')?.textContent, 'Conectado como Maria');
  assertSemAvisoNemInterruptor(s, 'com foto');
  assertSemEmail(s, 'com foto');

  // Foto que falha ao carregar: a inicial entra no lugar da img (uma vez só).
  img.emit('error');
  assert.equal(img.parentNode, null);
  assert.deepEqual(btn.children.map((c) => c.attrs.class), ['guifav-auth-initial']);
  assert.equal(btn.textContent, 'M');
  assert.equal(btn.children[0].attrs['aria-hidden'], 'true');
  img.emit('error');
  assert.equal(btn.children.length, 1);
  assertSemEmail(s, 'depois do erro');

  // Outro host, http, sufixo enganoso ou valor que não é URL: só a inicial, nunca img.
  for (const photoURL of [
    'https://exemplo.com/foto.png',
    'http://lh3.googleusercontent.com/a/x',
    'https://lh3.googleusercontent.com.evil.example/a',
    'https://evilgoogleusercontent.com/a',
    // Só lh3, o endereço citado na política (revisão da rodada 2 do #212).
    'https://lh4.googleusercontent.com/a/x',
    'https://googleusercontent.com/a/x',
    'javascript:alert(1)',
    'data:image/png;base64,AAAA',
    'não é uma url',
    '',
    null,
  ]) {
    const r = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser({ photoURL }) });
    const b = r.findClass('guifav-auth-btn')!;
    assert.equal(b.findAll((e) => e.tag === 'img').length, 0, String(photoURL));
    assert.deepEqual(b.children.map((c) => c.attrs.class), ['guifav-auth-initial'], String(photoURL));
    assert.equal(b.textContent, 'M', String(photoURL));
    assertSemEmail(r, String(photoURL));
  }

  // Sem nome, a conta aparece como "Conta" com a inicial "?"; nome com espaços e minúsculo vira o primeiro nome e a
  // inicial maiúscula.
  const semNome = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser({ displayName: null }) });
  assert.equal(semNome.findClass('guifav-auth-btn')?.textContent, '?');
  assert.equal(semNome.findClass('guifav-auth-btn')?.attrs['aria-label'], 'Conta, menu da conta');
  assert.equal(semNome.findClass('guifav-auth-name')?.textContent, 'Conectado como Conta');
  const minusculo = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    initialUser: makeUser({ displayName: '  joão  da silva ' }),
  });
  assert.equal(minusculo.findClass('guifav-auth-btn')?.textContent, 'J');
  assert.equal(minusculo.findClass('guifav-auth-btn')?.attrs['aria-label'], 'joão, menu da conta');

  // Troca de conta na mesma página: a foto da conta anterior sai.
  const t = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser({ photoURL: FOTO }) });
  await t.emitAuth(makeUser({ uid: 'UidOutraConta9', displayName: 'Bruno Lima', photoURL: null }));
  const tb = t.findClass('guifav-auth-btn')!;
  assert.equal(tb.findAll((e) => e.tag === 'img').length, 0);
  assert.equal(tb.textContent, 'B');
  assert.equal(t.findClass('guifav-auth-name')?.textContent, 'Conectado como Bruno');
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

test('Kimi P1 (rodada 6): sessão logada sem vínculo (oposição) nega anúncios, marca login e só devolve anúncios no logout', async () => {
  const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, heatingLink: OPPOSED_LINK, initialUser: makeUser() });
  assert.deepEqual(s.gtagCalls, LOGIN_AD_DENY);
  assert.ok(s.local.getItem('heating-login-v1'));
  assert.deepEqual(s.fetchCalls, []);
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

  // Com o vínculo ligado no navegador (o que todo login faz), o logout não devolve anúncios.
  const r = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser() });
  assert.deepEqual(r.gtagCalls, [...LOGIN_AD_DENY, ...identifySeq(UID)]);
  const beforeR = r.gtagCalls.length;
  await r.emitAuth(null);
  assert.equal(r.local.getItem('heating-login-v1'), null);
  assert.equal(r.gtagCalls.slice(beforeR).filter((c) => c[0] === 'consent').length, 0);

  // Cookies recusados: login não manda nada ao GA4 (anúncios já negados por padrão), mas marca o login e manda o POST
  // sem a origem.
  const q = await setupAuth({ consent: { v: 'denied', t: NOW - 1000 }, initialUser: makeUser() });
  assert.deepEqual(q.gtagCalls, []);
  assert.ok(q.local.getItem('heating-login-v1'));
  assert.equal(q.fetchCalls.length, 1);
  assert.equal(q.fetchCalls[0].init?.body, 'origem_canal=desconhecida');
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
  const sair = k.body.findAll((e) => e.attrs.role === 'menuitem')[0];
  assert.equal(sair.textContent, 'Sair');
  sair.focus();
  await k.click(sair);
  assert.equal(k.activeElement(), btn, 'o foco volta ao botão da conta');
});

test('Kimi P1 (rodada 6): sem a marca de login gravável, a sessão confirmada ainda segura anúncios depois do 409; guifavAuthIdentified', async () => {
  let responder: ((r: { ok: boolean; status: number }) => void) | null = null;
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    initialUser: makeUser(),
    identityFetch: () => new Promise((res) => {
      responder = res;
    }),
  });
  const authIdentified = () => (s.win.guifavAuthIdentified as () => boolean)();
  assert.equal(authIdentified(), true);
  assert.deepEqual(s.gtagCalls, LOGIN_AD_DENY, 'POST em andamento: vínculo sem confirmação, sem user_id');
  // Armazenamento sem a marca (bloqueado ou apagado): a oposição que chega (409) desliga o vínculo logado sem devolver
  // anúncios.
  s.local.removeItem('heating-login-v1');
  const before = s.gtagCalls.length;
  assert.ok(responder, 'POST em andamento');
  responder!({ ok: false, status: 409 });
  await flush();
  assert.deepEqual(JSON.parse(s.local.getItem('heating-link-v1')!), { v: 'denied', t: NOW, uid: UID, o: 1 });
  assert.deepEqual(s.gtagCalls.slice(before), [['set', { user_id: null }]]);
  assert.equal(authIdentified(), true, 'logado sem vínculo: o consent.js mantém anúncios negados');
  // Logout: sem sessão, marca nem vínculo, o banner volta a conceder e os anúncios voltam aqui.
  const beforeOut = s.gtagCalls.length;
  await s.emitAuth(null);
  assert.equal(authIdentified(), false, 'depois do logout e sem vínculo, o banner volta a conceder');
  assert.deepEqual(s.gtagCalls.slice(beforeOut), [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
    ['set', 'allow_google_signals', true],
    ['set', 'allow_ad_personalization_signals', true],
  ]);
});

test('Claude P3 (rodada 7) e Kimi P2 (rodada 8): no Sim, o auth.js só nega quando a marca de login não é legível; com o vínculo ligado, identifica de uma vez', async () => {
  // Logado com oposição e a marca legível: o consent.js nega ad_* e os dois sinais na hora; o auth.js não repete.
  const s = await setupAuth({ idbMode: 'session', heatingLink: OPPOSED_LINK, initialUser: makeUser() });
  assert.ok(s.local.getItem('heating-login-v1'));
  await s.emitConsent('granted');
  assert.deepEqual(s.gtagCalls, []);
  assert.deepEqual(s.fetchCalls, []);

  // Sessão confirmada nesta página mas a marca sumiu (armazenamento bloqueado ou apagado): o auth.js nega no Sim.
  const r = await setupAuth({ idbMode: 'session', heatingLink: OPPOSED_LINK, initialUser: makeUser() });
  r.local.removeItem('heating-login-v1');
  await r.emitConsent('granted');
  assert.deepEqual(r.gtagCalls, LOGIN_AD_DENY);

  // Sem sessão nem vínculo: o Sim não nega nada.
  const q = await setupAuth({ idbMode: 'session', initialUser: null });
  await q.emitConsent('granted');
  assert.deepEqual(q.gtagCalls, []);

  // Login sem resposta no banner (vínculo ligado no login): o Sim identifica de uma vez, sem negar duas vezes, e não
  // repete o POST feito antes do banner.
  const k = await setupAuth({ idbMode: 'session', initialUser: makeUser() });
  assert.deepEqual(k.gtagCalls, []);
  assert.equal(k.fetchCalls.length, 1);
  await k.emitConsent('granted');
  assert.deepEqual(k.gtagCalls, identifySeq(UID));
  assert.equal(k.fetchCalls.length, 1);
});

test('Kimi P2 e P3 (rodada 7): Não -> Sim logado sem vínculo desliga os sinais só pelo consent.js; com vínculo identifica; logout remoto com foco no menu devolve o foco', async () => {
  // Não -> Sim logado com oposição: quem nega ad_* e os sinais é o consent.js (testado em consent.test.ts); o auth.js
  // não duplica.
  const s = await setupAuth({ consent: { v: 'denied', t: NOW - 1000 }, heatingLink: OPPOSED_LINK, initialUser: makeUser() });
  assert.deepEqual(s.gtagCalls, []);
  await s.emitConsent('granted');
  assert.deepEqual(s.gtagCalls, []);

  // Não -> Sim logado com o vínculo ligado: identifica uma vez, com a negação só dentro da identificação.
  const v = await setupAuth({ consent: { v: 'denied', t: NOW - 1000 }, initialUser: makeUser() });
  assert.deepEqual(v.gtagCalls, []);
  assert.equal(v.fetchCalls.length, 1);
  await v.emitConsent('granted');
  assert.deepEqual(v.gtagCalls, identifySeq(UID));
  assert.equal(v.fetchCalls.length, 1);

  const k = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, heatingLink: { v: 'granted', t: NOW - 1000, uid: UID }, initialUser: makeUser() });
  const btn = k.findClass('guifav-auth-btn')!;
  await k.click(btn);
  const sair = k.body.findAll((e) => e.attrs.role === 'menuitem')[0];
  assert.equal(k.activeElement(), sair);
  await k.emitAuth(null); // Sair feito em outra aba
  assert.equal(k.activeElement(), btn, 'o foco sai do item escondido e volta ao botão');
  assert.equal(btn.textContent, 'Entrar');
  assert.equal(k.findClass('guifav-auth-menu')?.hidden, true);

  // Fechar o menu com clique fora não rouba o foco de onde a pessoa clicou.
  const c = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, heatingLink: { v: 'granted', t: NOW - 1000, uid: UID }, initialUser: makeUser() });
  await c.click(c.findClass('guifav-auth-btn')!);
  const input = c.body.appendChild(c.createElement('input'));
  input.focus();
  await c.clickOutside();
  assert.equal(c.activeElement(), input);
});

test('Kimi P2 (rodada 8): outra aba que entra na conta ou liga o vínculo nega anúncios e sinais aqui na hora', async () => {
  // Esta aba deslogada (Firebase sem usuário), cookies aceitos: outra aba entra e grava a marca de login.
  const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: null });
  const before = s.gtagCalls.length;
  await s.emitStorage('heating-login-v1', JSON.stringify({ t: NOW }));
  assert.deepEqual(s.gtagCalls.slice(before), LOGIN_AD_DENY);

  // Outra aba liga o vínculo.
  const r = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: null });
  const beforeR = r.gtagCalls.length;
  await r.emitStorage('heating-link-v1', JSON.stringify({ v: 'granted', t: NOW, uid: 'UidCurso123ABC' }));
  assert.deepEqual(r.gtagCalls.slice(beforeR).slice(0, 3), LOGIN_AD_DENY);

  // Marca apagada ou vínculo desligado em outra aba não nega (só volta ao consentimento comum); cookies recusados também não.
  const q = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: null });
  const beforeQ = q.gtagCalls.length;
  await q.emitStorage('heating-login-v1', null);
  await q.emitStorage('heating-link-v1', JSON.stringify({ v: 'denied', t: NOW, uid: 'UidCurso123ABC' }));
  assert.ok(!q.gtagCalls.slice(beforeQ).some((c) => c[0] === 'set' && c[1] === 'allow_google_signals' && c[2] === false));
  assert.ok(!q.gtagCalls.slice(beforeQ).some((c) => c[0] === 'consent' && (c[2] as { ad_storage?: string }).ad_storage === 'denied'));
  const n = await setupAuth({ consent: { v: 'denied', t: NOW - 1000 }, initialUser: null });
  await n.emitStorage('heating-login-v1', JSON.stringify({ t: NOW }));
  assert.deepEqual(n.gtagCalls, []);
});


test('Claude P4 (rodada 8): antes do Firebase, vínculo apagado em outra aba sem marca de login devolve anúncios e sinais', async () => {
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
  });
  const before = s.gtagCalls.length;
  await s.emitStorage('heating-link-v1', null);
  assert.deepEqual(s.gtagCalls.slice(before), [
    ['set', { user_id: null }],
    ['consent', 'update', AD_CONSENT_GRANTED],
    ['set', 'allow_google_signals', true],
    ['set', 'allow_ad_personalization_signals', true],
  ]);
  // Com a marca de login, o mesmo apagamento só tira o user_id.
  const r = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    heatingUid: { uid: 'UidCurso123ABC', t: NOW - 1000 },
  });
  r.local.setItem('heating-login-v1', JSON.stringify({ t: NOW }));
  const beforeR = r.gtagCalls.length;
  await r.emitStorage('heating-link-v1', null);
  assert.deepEqual(r.gtagCalls.slice(beforeR), [['set', { user_id: null }]]);
});

test('Roger P2 (86e90be): login e logout em outra aba, com esta aba deslogada, devolvem anúncios e sinais sem recarregar', async () => {
  const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: null });
  const before = s.gtagCalls.length;
  await s.emitStorage('heating-login-v1', JSON.stringify({ t: NOW }));
  await s.emitStorage('heating-login-v1', null);
  assert.deepEqual(s.gtagCalls.slice(before), [
    ...LOGIN_AD_DENY,
    ['consent', 'update', AD_CONSENT_GRANTED],
    ['set', 'allow_google_signals', true],
    ['set', 'allow_ad_personalization_signals', true],
  ]);

  // Vínculo ligado e depois desligado em outra aba, esta aba deslogada e sem user_id: o mesmo.
  const r = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: null });
  const beforeR = r.gtagCalls.length;
  await r.emitStorage('heating-link-v1', JSON.stringify({ v: 'granted', t: NOW, uid: 'UidCurso123ABC' }));
  await r.emitStorage('heating-link-v1', JSON.stringify({ v: 'denied', t: NOW, uid: 'UidCurso123ABC' }));
  const after = r.gtagCalls.slice(beforeR);
  assert.deepEqual(after.slice(0, 3), LOGIN_AD_DENY);
  assert.deepEqual(after.filter((c) => c[0] === 'consent').pop(), ['consent', 'update', AD_CONSENT_GRANTED]);
  assert.ok(after.some((c) => c[0] === 'set' && c[1] === 'allow_google_signals' && c[2] === true));

  // Marca apagada, mas esta aba continua logada (Firebase confirma) ou com o vínculo ligado: nada volta.
  const q = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser() });
  const beforeQ = q.gtagCalls.length;
  await q.emitStorage('heating-login-v1', null);
  assert.ok(!q.gtagCalls.slice(beforeQ).some((c) => c[0] === 'consent'));
  // Cookies recusados: nada volta.
  const n = await setupAuth({ consent: { v: 'denied', t: NOW - 1000 }, initialUser: null });
  await n.emitStorage('heating-login-v1', null);
  assert.deepEqual(n.gtagCalls, []);
});

test('Kimi P2 (rodada 9): logout em outra aba antes da primeira resposta do Firebase desta aba', async () => {
  // A marca existia no carregamento (o <head> negou anúncios); o Firebase daqui responde sem usuário.
  const local = new Storage();
  local.setItem('consent-v1', JSON.stringify({ v: 'granted', t: NOW - 1000 }));
  local.setItem('heating-login-v1', JSON.stringify({ t: NOW - 1000 }));
  const s = await setupAuth({ local, gisAvailable: true });
  const before = s.gtagCalls.length;
  await s.emitAuth(null);
  assert.equal(local.getItem('heating-login-v1'), null);
  assert.equal(s.session.getItem('guifav_one_tap_suppressed'), '1');
  assert.equal(s.oneTapPrompts.length, 0, 'sem One Tap logo depois da saída');
  assert.deepEqual(s.gtagCalls.slice(before), [
    ['consent', 'update', AD_CONSENT_GRANTED],
    ['set', 'allow_google_signals', true],
    ['set', 'allow_ad_personalization_signals', true],
  ]);

  // Se a remoção da marca chega antes pelo evento storage, a restauração acontece ali e a resposta do Firebase não repete.
  const local2 = new Storage();
  local2.setItem('consent-v1', JSON.stringify({ v: 'granted', t: NOW - 1000 }));
  local2.setItem('heating-login-v1', JSON.stringify({ t: NOW - 1000 }));
  const r = await setupAuth({ local: local2, gisAvailable: true });
  const beforeR = r.gtagCalls.length;
  await r.emitStorage('heating-login-v1', null);
  await r.emitAuth(null);
  assert.equal(r.gtagCalls.slice(beforeR).filter((c) => c[0] === 'consent').length, 1);
  // Claude P2 (rodada 10): nessa ordem (Sair termina lá antes do Firebase daqui responder), o One Tap também fica suprimido.
  assert.equal(r.session.getItem('guifav_one_tap_suppressed'), '1');
  assert.equal(r.oneTapPrompts.length, 0);

  // Visitante sem marca: a primeira resposta sem usuário não suprime o One Tap nem mexe em anúncios.
  const v = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, gisAvailable: true });
  await v.emitAuth(null);
  assert.equal(v.session.getItem('guifav_one_tap_suppressed'), null);
  assert.deepEqual(v.gtagCalls, []);
});

test('Kimi P2 (rodada 11 do #203): track.js só agenda o auth.js depois do load da página', () => {
  function run(readyState: string, withIdle = true) {
    const winHandlers: Record<string, Array<() => void>> = {};
    const idleCalls: Array<() => void> = [];
    const timers: Array<{ fn: () => void; ms: number }> = [];
    const appended: El[] = [];
    const html = new El('html');
    const body = html.appendChild(new El('body'));
    const origAppend = body.appendChild.bind(body);
    body.appendChild = (c: El) => { appended.push(c); return origAppend(c); };
    const ctx: Record<string, unknown> = {
      document: {
        documentElement: html, body, readyState, baseURI: 'https://guifav.dev/', referrer: '', cookie: '',
        createElement: (t: string) => new El(t), querySelector: () => null, addEventListener: () => {},
      },
      location: { hostname: 'guifav.dev', pathname: '/', href: 'https://guifav.dev/' },
      sessionStorage: new Storage(), localStorage: new Storage(), URL, JSON, Date,
      setTimeout: (fn: () => void, ms: number) => timers.push({ fn, ms }),
      addEventListener: (t: string, fn: () => void) => { (winHandlers[t] ??= []).push(fn); },
    };
    if (withIdle) ctx.requestIdleCallback = (fn: () => void) => idleCalls.push(fn);
    ctx.window = ctx;
    vm.runInContext(TRACK_SRC, vm.createContext(ctx));
    return { winHandlers, idleCalls, appended, timers };
  }
  const loading = run('loading');
  assert.equal(loading.idleCalls.length, 0, 'nada agendado antes do load');
  assert.equal((loading.winHandlers.load ?? []).length, 1);
  loading.winHandlers.load[0]();
  assert.equal(loading.idleCalls.length, 1, 'agenda no load');
  loading.idleCalls[0]();
  assert.ok(loading.appended.some((e) => e.tag === 'script' && String(e.attrs.src).startsWith('/assets/auth.js?v=')));

  const complete = run('complete');
  assert.equal(complete.idleCalls.length, 1, 'página já carregada: agenda na hora');
  assert.equal((complete.winHandlers.load ?? []).length, 0);

  // interactive (DOM pronto, recursos ainda carregando) também espera o load.
  const interactive = run('interactive');
  assert.equal(interactive.idleCalls.length, 0);
  assert.equal((interactive.winHandlers.load ?? []).length, 1);

  // Sem requestIdleCallback: o fallback de 1,5 s só é agendado depois do load.
  const noIdle = run('loading', false);
  const authTimers = () => noIdle.timers.filter((t) => t.ms === 1500);
  assert.equal(authTimers().length, 0, 'nada antes do load');
  noIdle.winHandlers.load[0]();
  assert.equal(authTimers().length, 1);
  authTimers()[0].fn();
  assert.ok(noIdle.appended.some((e) => e.tag === 'script' && String(e.attrs.src).startsWith('/assets/auth.js?v=')));
});

test('Kimi P2 (rodada 11 do #203): GIS que termina de carregar depois de um logout em outra aba não abre o One Tap', async () => {
  const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: null });
  assert.equal(s.gisScripts().length, 1, 'GIS sendo baixado');
  await s.emitStorage('heating-login-v1', JSON.stringify({ t: NOW }));
  await s.emitStorage('heating-login-v1', null);
  s.finishGisLoad();
  await flush();
  assert.equal(s.oneTapPrompts.length, 0);

  // Cookies apagados em outra aba (consent-v1 some) enquanto o GIS baixa: também não abre.
  const k = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: null });
  k.local.removeItem('consent-v1');
  k.finishGisLoad();
  await flush();
  assert.equal(k.oneTapPrompts.length, 0);
  // Kimi P3 (rodada 1 do #205): a tentativa rearma, e a próxima resposta ao banner abre o convite.
  await k.emitConsent('granted');
  assert.equal(k.oneTapPrompts.length, 1);
  // Supressão continua valendo mesmo rearmado.
  const z = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: null });
  await z.emitStorage('heating-login-v1', JSON.stringify({ t: NOW }));
  await z.emitStorage('heating-login-v1', null);
  z.finishGisLoad();
  await flush();
  await z.emitConsent('granted');
  assert.equal(z.oneTapPrompts.length, 0);

  // Controle: sem saída no meio, o GIS carregado abre o convite.
  const c = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: null });
  c.finishGisLoad();
  await flush();
  assert.equal(c.oneTapPrompts.length, 1);
});

test('P3 (rodada 11 do #203): marca gravada em outra aba não suprime; apagada cancela o convite aberto', async () => {
  const s = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: null, gisAvailable: true });
  assert.equal(s.oneTapPrompts.length, 1, 'convite aberto para o visitante');
  await s.emitStorage('heating-login-v1', JSON.stringify({ t: NOW }));
  assert.equal(s.session.getItem('guifav_one_tap_suppressed'), null, 'login em outra aba não é saída');
  assert.equal(s.oneTapCancels.length, 0);
  await s.emitStorage('heating-login-v1', null);
  assert.equal(s.session.getItem('guifav_one_tap_suppressed'), '1');
  assert.equal(s.oneTapCancels.length, 1, 'o convite aberto é cancelado');
});

test('login vira conversão (#197): o POST /identidade leva a origem da visita urlencoded, só com rótulos', async () => {
  const s = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
  });
  s.win.guifavOrigin = {
    current: () => ({ canal: 'busca', fonte: 'google', utm_source: 'news', utm_medium: null, utm_campaign: 'lanc-1', entrada: '/blog/x/' }),
  };
  await s.emitAuth(makeUser());
  assert.equal(s.fetchCalls.length, 1);
  const init = s.fetchCalls[0].init as { headers: Record<string, string>; body?: string };
  assert.deepEqual(init.headers, { Authorization: 'Bearer jwt.token.falso', 'Content-Type': 'application/x-www-form-urlencoded' });
  assert.equal(init.body, 'origem_canal=busca&origem_fonte=google&utm_source=news&utm_campaign=lanc-1&origem_entrada=%2Fblog%2Fx%2F');

  // Kimi P2 (rodada 8 do #206): valores fora do padrão (URL, termo buscado, identificador, maiúsculas) não saem do navegador.
  const h = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
  });
  h.win.guifavOrigin = {
    current: () => ({
      canal: 'busca', fonte: 'Google Ads', utm_source: 'https://evil.example/?gclid=abc', utm_medium: 'como fazer rag',
      utm_campaign: 'ok_1', entrada: '/blog/x/?q=termo buscado',
    }),
  };
  await h.emitAuth(makeUser());
  const initH = h.fetchCalls[0].init as { body?: string };
  assert.equal(initH.body, 'origem_canal=busca&utm_campaign=ok_1');
  // Caminho acima de 200 caracteres também não sai.
  const l = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
  });
  l.win.guifavOrigin = { current: () => ({ canal: 'direto', entrada: '/' + 'a'.repeat(200) }) };
  await l.emitAuth(makeUser());
  assert.equal((l.fetchCalls[0].init as { body?: string }).body, 'origem_canal=direto');
  assert.ok(!String(init.body).includes('@'), 'nunca o email');

  // Sem guifavOrigin (track.js ausente) ou com valor estranho: corpo só com a origem desconhecida.
  const r = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
  });
  r.win.guifavOrigin = { current: () => { throw new Error('quebrado'); } };
  await r.emitAuth(makeUser());
  const initR = r.fetchCalls[0].init as { headers: Record<string, string>; body?: string };
  assert.deepEqual(initR.headers, { Authorization: 'Bearer jwt.token.falso', 'Content-Type': 'application/x-www-form-urlencoded' });
  assert.equal(initR.body, 'origem_canal=desconhecida');
});

test('Kimi P2 (rodada 10 do #206): marca de envio do auth.js antigo na mesma aba não impede o envio com a origem', async () => {
  const session = new Storage();
  session.setItem('guifav_identidade_sent:UidCurso123ABC', '1'); // gravada pelo auth.js antigo (envio sem corpo)
  const s = await setupAuth({
    session,
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  assert.equal(s.fetchCalls.length, 1, 'o auth.js atual manda uma vez, já com corpo');
  assert.equal(session.getItem('guifav_identidade_sent_v2:UidCurso123ABC'), '1');
  const s2 = await setupAuth({
    session,
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - 1000, uid: 'UidCurso123ABC' },
    initialUser: makeUser(),
  });
  assert.equal(s2.fetchCalls.length, 0, 'e só uma vez por sessão');
});

test('P2 da revisão do #212: o GA4 só identifica depois de o /identidade confirmar o vínculo (s: 1); Sim e outra aba sem confirmação no meio não adiantam', async () => {
  const local = new SpyStorage();
  local.setItem('consent-v1', JSON.stringify({ v: 'granted', t: NOW - 1000 }));
  const pend = identidadePendente();
  const s = await setupAuth({ local, initialUser: makeUser(), identityFetch: pend.fetch });

  // POST em andamento: vínculo ligado sem s, a sessão logada só nega anúncios; nada de user_id, heating-uid-v1 nem
  // auth_identified, e nenhum temporizador do heating-uid-v1.
  assert.equal(s.fetchCalls.length, 1);
  assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), { v: 'granted', t: NOW, uid: UID });
  assert.deepEqual(s.gtagCalls, LOGIN_AD_DENY);
  assert.equal(local.getItem('heating-uid-v1'), null);
  assert.equal(s.pendingTimers(), 0);
  assert.equal((s.win.guifavAuthIdentified as () => boolean)(), true, 'o consent.js mantém anúncios negados');

  // Sim de novo no banner enquanto o POST não volta: nada de identificar nem de segundo POST.
  await s.emitConsent('granted');
  assert.deepEqual(s.gtagCalls, LOGIN_AD_DENY);
  assert.equal(s.fetchCalls.length, 1);

  // Servidor confirma: s: 1 com o instante da confirmação, marca de envio da sessão e identificação completa.
  s.setNow(NOW + 2000);
  pend.responder(204);
  await flush();
  assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), linkOk(NOW + 2000));
  assert.equal(s.session.getItem('guifav_identidade_sent_v2:UidCurso123ABC'), '1');
  assert.deepEqual(s.gtagCalls, [...LOGIN_AD_DENY, ...identifySeq(UID)]);
  assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), { uid: UID, t: NOW + 2000 });
  // heating-uid-v1 só foi gravado depois da confirmação.
  const confirmWrite = local.writes.findIndex(([k, v]) => k === 'heating-link-v1' && JSON.parse(v).s === 1);
  const firstUidWrite = local.writes.findIndex(([k]) => k === 'heating-uid-v1');
  assert.ok(confirmWrite >= 0 && firstUidWrite > confirmWrite, 'heating-uid-v1 depois do s: 1');
  assert.equal(s.fetchCalls.length, 1);
  assert.deepEqual(s.warns, []);

  // Outra aba confirma o vínculo (s: 1) enquanto o POST daqui está pendente: a confirmação de lá não vale nesta aba
  // (rodada 2 do #212), que só reafirma a negação; a identificação vem com a resposta ok do POST daqui, uma vez.
  const po = identidadePendente();
  const o = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser(), identityFetch: po.fetch });
  assert.deepEqual(o.gtagCalls, LOGIN_AD_DENY);
  const beforeO = o.gtagCalls.length;
  await o.emitStorage('heating-link-v1', JSON.stringify(linkOk(NOW)));
  assert.deepEqual(o.gtagCalls.slice(beforeO), LOGIN_AD_DENY);
  assert.equal(o.local.getItem('heating-uid-v1'), null);
  po.responder(204);
  await flush();
  assert.deepEqual(o.gtagCalls.slice(beforeO), [...LOGIN_AD_DENY, ...identifySeq(UID)]);

  // Outra aba liga o vínculo sem confirmar: aqui só reafirma a negação, sem user_id.
  const pp = identidadePendente();
  const p = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, initialUser: makeUser(), identityFetch: pp.fetch });
  const beforeP = p.gtagCalls.length;
  await p.emitStorage('heating-link-v1', JSON.stringify({ v: 'granted', t: NOW, uid: UID }));
  assert.deepEqual(p.gtagCalls.slice(beforeP), LOGIN_AD_DENY);
  assert.equal(p.local.getItem('heating-uid-v1'), null);

  // Vínculo de outro uid com s: 1 não confirma este uid.
  const pq = identidadePendente();
  const q = await setupAuth({
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: linkOk(NOW - DAY, 'UidOutraConta9'),
    initialUser: makeUser(),
    identityFetch: pq.fetch,
  });
  assert.deepEqual(JSON.parse(q.local.getItem('heating-link-v1')!), { v: 'granted', t: NOW, uid: UID });
  assert.deepEqual(q.gtagCalls, LOGIN_AD_DENY);

  // Marca de envio da sessão (POST aceito por um auth.js de antes do s: 1) vale como confirmação: identifica sem novo
  // POST e grava s: 1.
  const session = new Storage();
  session.setItem('guifav_identidade_sent_v2:UidCurso123ABC', '1');
  const legado = await setupAuth({
    session,
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: { v: 'granted', t: NOW - DAY, uid: UID },
    initialUser: makeUser(),
  });
  assert.deepEqual(legado.fetchCalls, []);
  assert.deepEqual(JSON.parse(legado.local.getItem('heating-link-v1')!), linkOk(NOW));
  assert.deepEqual(legado.gtagCalls, [...LOGIN_AD_DENY, ...identifySeq(UID)]);
  assert.deepEqual(JSON.parse(legado.local.getItem('heating-uid-v1')!), { uid: UID, t: NOW });

  // Aba identificada cujo vínculo perde a confirmação sem evento (storage perdido; outra aba gravou um vínculo novo sem
  // s: 1): no vencimento de heating-uid-v1, a chave não renova e o user_id sai.
  const v = await setupAuth({ consent: { v: 'granted', t: NOW - 1000 }, heatingLink: linkOk(NOW - DAY), initialUser: makeUser() });
  assert.ok(v.gtagCalls.some((c) => isUserIdSet(c, UID)));
  v.local.setItem('heating-link-v1', JSON.stringify({ v: 'granted', t: NOW, uid: UID }));
  const beforeV = v.gtagCalls.length;
  await v.advance(UID_TTL + 1000);
  assert.equal(v.local.getItem('heating-uid-v1'), null);
  assert.deepEqual(v.gtagCalls.slice(beforeV), [['set', { user_id: null }]]);
  assert.equal(v.pendingTimers(), 0);
});

test('P2 da revisão do #212: 409 no primeiro login, com cookies aceitos, nunca produz user_id, heating-uid-v1 nem auth_identified em momento algum', async () => {
  const local = new SpyStorage();
  local.setItem('consent-v1', JSON.stringify({ v: 'granted', t: NOW - 1000 }));
  const pend = identidadePendente();
  const s = await setupAuth({ local, initialUser: makeUser(), identityFetch: pend.fetch });
  const nunca = (x: { gtagCalls: unknown[][]; session: Storage }, label: string) => {
    assert.ok(!x.gtagCalls.some((c) => isUserIdSet(c, UID)), `${label}: nunca user_id`);
    assert.ok(!x.gtagCalls.some((c) => c[0] === 'event'), `${label}: nunca auth_identified`);
    assert.ok(!local.writes.some(([k]) => k === 'heating-uid-v1'), `${label}: heating-uid-v1 nunca gravado`);
    assert.ok(!local.writes.some(([k, v]) => k === 'heating-link-v1' && JSON.parse(v).s === 1), `${label}: nunca confirmado`);
    assert.equal(x.session.getItem('guifav_auth_identified:UidCurso123ABC'), null, label);
  };
  nunca(s, 'POST em andamento');

  // Sim no banner, aba escondida e de volta e o relógio passando dos 30 min: nada identifica antes da resposta.
  await s.emitConsent('granted');
  await s.emitVisibility('hidden');
  await s.emitVisibility('visible');
  await s.advance(UID_TTL + 1000);
  nunca(s, 'antes da resposta');

  pend.responder(409);
  await flush();
  nunca(s, 'depois do 409');
  assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), { v: 'denied', t: NOW + UID_TTL + 1000, uid: UID, o: 1 });
  assert.deepEqual(s.gtagCalls, [...LOGIN_AD_DENY, ['set', { user_id: null }]]);
  assert.equal(s.session.getItem('guifav_identidade_sent_v2:UidCurso123ABC'), null);
  assert.deepEqual(s.warns, []);

  // Próxima página na mesma sessão: nada de POST nem identificação.
  const s2 = await setupAuth({ local, session: s.session, now: NOW + UID_TTL + 2000, initialUser: makeUser() });
  nunca(s2, 'próxima página');
  assert.deepEqual(s2.fetchCalls, []);
  assert.deepEqual(s2.gtagCalls, LOGIN_AD_DENY);
});

test('P2 da revisão do #212: falha do /identidade (500 ou rede) não confirma o vínculo nem identifica no GA4; a próxima página tenta de novo', async () => {
  for (const caso of ['500', 'rede'] as const) {
    const local = new SpyStorage();
    const session = new Storage();
    local.setItem('consent-v1', JSON.stringify({ v: 'granted', t: NOW - 1000 }));
    const s = await setupAuth({
      local,
      session,
      initialUser: makeUser(),
      ...(caso === '500' ? { identityStatus: 500 } : { identityThrow: true }),
    });
    assert.equal(s.fetchCalls.length, 1, caso);
    assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), { v: 'granted', t: NOW, uid: UID }, `${caso}: sem s`);
    assert.deepEqual(s.gtagCalls, LOGIN_AD_DENY, caso);
    assert.equal(local.getItem('heating-uid-v1'), null, caso);
    assert.ok(!local.writes.some(([k]) => k === 'heating-uid-v1'), `${caso}: heating-uid-v1 nunca gravado`);
    assert.equal(session.getItem('guifav_identidade_sent_v2:UidCurso123ABC'), null, caso);
    assert.equal(session.getItem('guifav_auth_identified:UidCurso123ABC'), null, caso);
    assert.deepEqual(
      s.warns,
      [caso === '500' ? 'guifav-auth: identidade nao registrada' : 'guifav-auth: falha ao enviar identidade'],
      caso,
    );
    assertSemEmail(s, caso);

    // Próxima página na mesma sessão, servidor de volta: manda de novo, confirma e identifica.
    const s2 = await setupAuth({ local, session, now: NOW + MIN, initialUser: makeUser() });
    assert.equal(s2.fetchCalls.length, 1, caso);
    assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), linkOk(NOW + MIN), caso);
    assert.deepEqual(s2.gtagCalls, [...LOGIN_AD_DENY, ...identifySeq(UID)], caso);
    assert.deepEqual(JSON.parse(local.getItem('heating-uid-v1')!), { uid: UID, t: NOW + MIN }, caso);
  }
});

test('Kimi P2 (rodada 2 do #212): s: 1 de uma sessão anterior não identifica antes de o POST desta sessão responder; o 409 que chega desliga sem nunca identificar', async () => {
  // Repro exato: vínculo confirmado numa sessão anterior (s: 1) e heating-uid-v1 ainda dentro dos 30 min, cookies aceitos,
  // aba nova (sem a marca de envio), POST pendente; a oposição foi registrada no servidor nesse meio tempo.
  const local = new SpyStorage();
  local.setItem('consent-v1', JSON.stringify({ v: 'granted', t: NOW - 1000 }));
  local.setItem('heating-link-v1', JSON.stringify(linkOk(NOW - DAY)));
  local.setItem('heating-uid-v1', JSON.stringify({ uid: UID, t: NOW - 5 * MIN }));
  local.setItem('heating-login-v1', JSON.stringify({ t: NOW - DAY }));
  const w0 = local.writes.length;
  const session = new Storage();
  const pend = identidadePendente();
  const s = await setupAuth({ local, session, initialUser: makeUser(), identityFetch: pend.fetch });
  const nunca = (label: string) => {
    assert.ok(!s.gtagCalls.some((c) => isUserIdSet(c, UID)), `${label}: nunca user_id`);
    assert.ok(!s.gtagCalls.some((c) => c[0] === 'event'), `${label}: nunca auth_identified`);
    assert.ok(!local.writes.slice(w0).some(([k]) => k === 'heating-uid-v1'), `${label}: heating-uid-v1 nunca regravado`);
    assert.equal(session.getItem('guifav_auth_identified:UidCurso123ABC'), null, label);
    assert.equal(session.getItem('guifav_identidade_sent_v2:UidCurso123ABC'), null, label);
  };

  // POST pendente: nada identificado; a sobra de heating-uid-v1 sai e o user_id vai a null (defensivo).
  assert.equal(s.fetchCalls.length, 1);
  assert.deepEqual(s.gtagCalls, [...LOGIN_AD_DENY, ['set', { user_id: null }]]);
  assert.equal(local.getItem('heating-uid-v1'), null);
  assert.equal((s.win.guifavAuthIdentified as () => boolean)(), true);
  nunca('POST pendente');
  // Sim no banner e o relógio andando também não identificam.
  await s.emitConsent('granted');
  await s.advance(UID_TTL + 1000);
  nunca('antes da resposta');

  // O 409 chega: a marca da oposição é gravada e nada nunca foi identificado.
  pend.responder(409);
  await flush();
  assert.deepEqual(JSON.parse(local.getItem('heating-link-v1')!), { v: 'denied', t: NOW + UID_TTL + 1000, uid: UID, o: 1 });
  nunca('depois do 409');
  assert.ok(!s.gtagCalls.some((c) => c[0] === 'consent' && (c[2] as { ad_storage?: string }).ad_storage === 'granted'), 'logado: anúncios nunca voltam');
  assert.deepEqual(s.warns, []);

  // Controle: na mesma aba, com a marca de envio, o s: 1 identifica na hora e sem POST (próxima página da mesma sessão).
  const okSession = new Storage();
  okSession.setItem('guifav_identidade_sent_v2:UidCurso123ABC', '1');
  const c = await setupAuth({
    session: okSession,
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: linkOk(NOW - DAY),
    initialUser: makeUser(),
  });
  assert.deepEqual(c.fetchCalls, []);
  assert.deepEqual(c.gtagCalls, identifySeq(UID));
  // Marca de envio de outro uid não confirma este.
  const other = new Storage();
  other.setItem('guifav_identidade_sent_v2:UidOutraConta9', '1');
  const pd = identidadePendente();
  const d = await setupAuth({
    session: other,
    consent: { v: 'granted', t: NOW - 1000 },
    heatingLink: linkOk(NOW - DAY),
    initialUser: makeUser(),
    identityFetch: pd.fetch,
  });
  assert.deepEqual(d.gtagCalls, LOGIN_AD_DENY);
  assert.equal(d.fetchCalls.length, 1);
});
