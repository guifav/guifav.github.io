// Testes do /assets/consent.js e do padrão de consentimento inline (issue #68).
// O script roda num contexto vm com um DOM mínimo; o fake só implementa o que o script usa.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SRC = readFileSync(new URL('../../assets/consent.js', import.meta.url), 'utf8');
const DAY = 24 * 3600 * 1000;
const MIN = 60 * 1000;
const NOW = Date.UTC(2026, 10, 14, 3, 0, 0);
const GRANTED = { ad_storage: 'granted', ad_user_data: 'granted', ad_personalization: 'granted', analytics_storage: 'granted' };
const IDENTIFIED_GRANTED = { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied', analytics_storage: 'granted' };
const DENIED = { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied', analytics_storage: 'denied' };
const AD_DENIED = { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' };

class Node {
  tag: string;
  attrs: Record<string, string> = {};
  children: Node[] = [];
  parent: Node | null = null;
  textContent = '';
  hidden = false;
  handlers: Record<string, ((e: unknown) => void)[]> = {};
  constructor(tag: string) {
    this.tag = tag;
  }
  get lang(): string {
    return this.attrs.lang ?? '';
  }
  setAttribute(k: string, v: string) {
    this.attrs[k] = String(v);
  }
  getAttribute(k: string) {
    return k in this.attrs ? this.attrs[k] : null;
  }
  appendChild(c: Node) {
    c.parent = this;
    this.children.push(c);
    return c;
  }
  addEventListener(t: string, fn: (e: unknown) => void) {
    (this.handlers[t] ??= []).push(fn);
  }
  closest(sel: string): Node | null {
    for (let e: Node | null = this; e; e = e.parent) {
      if (sel === '[data-consent-open]' && 'data-consent-open' in e.attrs) return e;
      if (sel === '#lang-toggle' && e.attrs.id === 'lang-toggle') return e;
      if (sel !== '[data-consent-open]' && sel !== '#lang-toggle') throw new Error(`seletor fora do fake: ${sel}`);
    }
    return null;
  }
  text(): string {
    return this.textContent + this.children.map((c) => c.text()).join('');
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
}
const Blocked = {
  getItem() {
    throw new Error('SecurityError');
  },
  setItem() {
    throw new Error('SecurityError');
  },
};

function setup(opts: { lang?: string; stored?: unknown; heatingUid?: unknown; heatingLink?: unknown; heatingLogin?: unknown; authIdentified?: () => boolean;
  storage?: Storage | typeof Blocked; gtag?: boolean; cookies?: string; clarityLoaded?: boolean } = {}) {
  const calls: unknown[][] = [];
  const cookieWrites: string[] = [];
  const clarity: number[] = [];
  const consentEvents: string[] = [];
  let reloads = 0;
  const html = new Node('html');
  html.attrs.lang = opts.lang ?? 'pt-BR';
  const body = html.appendChild(new Node('body'));
  const footer = body.appendChild(new Node('footer'));
  const cookieLink = footer.appendChild(new Node('a'));
  cookieLink.attrs = { href: '/privacidade/#cookies', 'data-consent-open': '' };
  const langBtn = body.appendChild(new Node('button'));
  langBtn.attrs.id = 'lang-toggle';
  const other = body.appendChild(new Node('a'));
  other.attrs.href = '/';
  const storage = opts.storage ?? new Storage();
  if (opts.stored !== undefined && storage instanceof Storage) storage.setItem('consent-v1', typeof opts.stored === 'string' ? opts.stored : JSON.stringify(opts.stored));
  if (opts.heatingUid !== undefined && storage instanceof Storage) storage.setItem('heating-uid-v1', typeof opts.heatingUid === 'string' ? opts.heatingUid : JSON.stringify(opts.heatingUid));
  if (opts.heatingLink !== undefined && storage instanceof Storage) storage.setItem('heating-link-v1', typeof opts.heatingLink === 'string' ? opts.heatingLink : JSON.stringify(opts.heatingLink));
  if (opts.heatingLogin !== undefined && storage instanceof Storage) storage.setItem('heating-login-v1', typeof opts.heatingLogin === 'string' ? opts.heatingLogin : JSON.stringify(opts.heatingLogin));
  const listeners: ((e: unknown) => void)[] = [];
  const document = {
    documentElement: html,
    body,
    get cookie() {
      return opts.cookies ?? '';
    },
    set cookie(v: string) {
      cookieWrites.push(v);
    },
    createElement: (t: string) => new Node(t),
    createTextNode: (t: string) => {
      const n = new Node('#text');
      n.textContent = t;
      return n;
    },
    addEventListener: (t: string, fn: (e: unknown) => void) => {
      if (t === 'click') listeners.push(fn);
    },
  };
  const ctx: Record<string, unknown> = {
    document,
    localStorage: storage,
    location: { hostname: 'guifav.dev', reload: () => { reloads += 1; } },
    JSON,
    Date: { now: () => NOW },
    guifavClarity: () => clarity.push(1),
    CustomEvent: class FakeCustomEvent {
      type: string;
      detail: unknown;
      constructor(type: string, init?: { detail?: unknown }) {
        this.type = type;
        this.detail = init?.detail;
      }
    },
    dispatchEvent: (ev: { type?: string; detail?: unknown }) => {
      if (ev?.type === 'guifav-consent') consentEvents.push(String(ev.detail));
      return true;
    },
  };
  ctx.window = ctx;
  if (opts.authIdentified) ctx.guifavAuthIdentified = opts.authIdentified;
  if (opts.clarityLoaded) ctx.clarity = () => {};
  if (opts.gtag !== false) ctx.gtag = (...a: unknown[]) => calls.push(JSON.parse(JSON.stringify(a)));
  vm.runInContext(SRC, vm.createContext(ctx));
  const banner = () => body.children.find((c) => c.attrs.class === 'consent') ?? null;
  const btn = (cls: string) => {
    const b = banner()!;
    const find = (n: Node): Node | null => (n.attrs.class === cls ? n : n.children.map(find).find(Boolean) ?? null);
    return find(b)!;
  };
  // Clique: handlers do próprio elemento (alvo) e depois o ouvinte do document (borbulha).
  const click = (target: Node) => {
    let prevented = false;
    const ev = { type: 'click', target, preventDefault: () => { prevented = true; } };
    for (let e: Node | null = target; e; e = e.parent) for (const fn of e.handlers.click ?? []) fn(ev);
    for (const fn of listeners) fn(ev);
    return prevented;
  };
  return { calls, cookieWrites, clarity, consentEvents, reloads: () => reloads, html, banner, btn, click, cookieLink, langBtn, other, storage };
}

const updates = (calls: unknown[][]) => calls.filter((c) => c[0] === 'consent' && c[1] === 'update').map((c) => c[2]);

test('sem escolha: banner em português com Sim, Não e o link da política', () => {
  const s = setup();
  const b = s.banner()!;
  assert.ok(b && !b.hidden);
  assert.equal(b.attrs.role, 'dialog');
  assert.equal(b.text(), 'Aceitar os cookies? Política de privacidade e cookiesSimNão');
  assert.equal(s.btn('consent-yes').textContent, 'Sim');
  assert.equal(s.btn('consent-no').textContent, 'Não');
  const find = (n: Node): Node | null => (n.tag === 'a' ? n : n.children.map(find).find(Boolean) ?? null);
  assert.equal(find(b)!.attrs.href, '/privacidade/#cookies');
  assert.deepEqual(updates(s.calls), []);
});

test('página em inglês: Accept cookies?, Yes e No; o texto acompanha a troca de idioma', () => {
  const s = setup({ lang: 'en' });
  assert.equal(s.banner()!.text(), 'Accept cookies? Privacy and cookie policyYesNo');
  const p = setup();
  p.html.attrs.lang = 'en'; // o handler do seletor (site.js) troca o lang antes do ouvinte do document
  p.click(p.langBtn);
  assert.equal(p.banner()!.text(), 'Accept cookies? Privacy and cookie policyYesNo');
});

test('Sim: grava a escolha, concede os quatro sinais, carrega o Clarity e esconde o banner', () => {
  const s = setup();
  s.click(s.btn('consent-yes'));
  assert.deepEqual(JSON.parse(s.storage instanceof Storage ? s.storage.getItem('consent-v1')! : ''), { v: 'granted', t: NOW });
  assert.deepEqual(updates(s.calls), [GRANTED]);
  assert.equal(s.clarity.length, 1);
  assert.equal(s.banner()!.hidden, true);
  assert.deepEqual(s.cookieWrites, []);
  assert.equal(s.reloads(), 0);
});

test('Não: grava, nega, não carrega o Clarity e apaga só os cookies de medição e anúncio', () => {
  const s = setup({ cookies: '_ga=GA1.1.1; _ga_K7Y87QNCX8=GS1; _clck=x; _clsk=y; theme=dark; lang=en' });
  s.click(s.btn('consent-no'));
  assert.deepEqual(JSON.parse((s.storage as Storage).getItem('consent-v1')!), { v: 'denied', t: NOW });
  assert.deepEqual(updates(s.calls), [DENIED]);
  assert.equal(s.clarity.length, 0);
  assert.equal(s.banner()!.hidden, true);
  const cleared = new Set(s.cookieWrites.map((w) => w.split('=')[0]));
  assert.deepEqual([...cleared].sort(), ['_clck', '_clsk', '_ga', '_ga_K7Y87QNCX8']);
  for (const w of s.cookieWrites) assert.match(w, /=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=\//);
  assert.ok(s.cookieWrites.some((w) => w.endsWith('; domain=.guifav.dev')));
  assert.equal(s.reloads(), 0, 'o Clarity não estava carregado');
});

test('escolha válida não mostra o banner; vencida (365 dias), quebrada ou desconhecida mostra', () => {
  assert.equal(setup({ stored: { v: 'granted', t: NOW - 364 * DAY } }).banner(), null);
  assert.equal(setup({ stored: { v: 'denied', t: NOW - DAY } }).banner(), null);
  for (const [name, stored] of [['vencida', { v: 'granted', t: NOW - 366 * DAY }], ['json quebrado', '{x'],
    ['valor estranho', { v: 'sim', t: NOW }], ['sem data', { v: 'granted' }]] as const) {
    assert.ok(setup({ stored }).banner(), name);
  }
});

test('o link Cookies do rodapé reabre o banner, mesmo com escolha gravada, e não navega', () => {
  const s = setup({ stored: { v: 'granted', t: NOW } });
  assert.equal(s.banner(), null);
  assert.equal(s.click(s.other), false);
  assert.equal(s.banner(), null);
  assert.equal(s.click(s.cookieLink), true);
  assert.ok(s.banner() && !s.banner()!.hidden);
});

test('revogar depois de aceitar com o Clarity rodando recarrega a página', () => {
  const s = setup({ stored: { v: 'granted', t: NOW }, clarityLoaded: true });
  s.click(s.cookieLink);
  s.click(s.btn('consent-no'));
  assert.equal(s.reloads(), 1);
  assert.deepEqual(updates(s.calls), [DENIED]);
});

test('data futura ou Infinity nunca vale como escolha', () => {
  for (const stored of [{ v: 'granted', t: NOW + DAY }, { v: 'denied', t: NOW + 1 }, '{"v":"granted","t":1e400}', '{"v":"granted","t":-1e400}']) {
    assert.ok(setup({ stored }).banner(), JSON.stringify(stored));
  }
});

test('Não com o Clarity rodando recarrega mesmo sem escolha gravada (storage bloqueado ou outra aba)', () => {
  const s = setup({ storage: Blocked, clarityLoaded: true });
  s.click(s.btn('consent-no'));
  assert.equal(s.reloads(), 1);
  const o = setup({ stored: { v: 'denied', t: NOW - DAY }, clarityLoaded: true });
  o.click(o.cookieLink);
  o.click(o.btn('consent-no'));
  assert.equal(o.reloads(), 1);
  const n = setup();
  n.click(n.btn('consent-no'));
  assert.equal(n.reloads(), 0, 'sem Clarity não recarrega');
});

test('Sim e Não disparam CustomEvent guifav-consent com detail granted ou denied', () => {
  const s = setup();
  s.click(s.btn('consent-yes'));
  assert.deepEqual(s.consentEvents, ['granted']);
  s.click(s.cookieLink);
  s.click(s.btn('consent-no'));
  assert.deepEqual(s.consentEvents, ['granted', 'denied']);
});

test('Sim com o vínculo do login ligado (heating-link-v1 granted) ou sinal do auth.js concede só analytics_storage e mantém anúncios negados', () => {
  // Reabrir "Cookies" e confirmar "Sim" num navegador com o vínculo ligado (até 365 dias, qualquer uid): nunca concede ad_*.
  for (const age of [0, 10 * MIN, 200 * DAY, 365 * DAY - 1]) {
    const s = setup({ stored: { v: 'granted', t: NOW - DAY }, heatingLink: { v: 'granted', t: NOW - age, uid: 'UidAtivo123' } });
    s.click(s.cookieLink);
    s.click(s.btn('consent-yes'));
    assert.deepEqual(updates(s.calls), [IDENTIFIED_GRANTED], `age=${age}`);
    assert.equal(s.clarity.length, 1);
    assert.deepEqual(s.consentEvents, ['granted']);
  }

  // Marca de sessão logada (heating-login-v1, até 365 dias), sem vínculo: também nunca concede ad_* (Kimi P1, rodada 6).
  for (const age of [0, 200 * DAY, 365 * DAY - 1]) {
    const s = setup({ stored: { v: 'denied', t: NOW - DAY }, heatingLogin: { t: NOW - age } });
    s.click(s.cookieLink);
    s.click(s.btn('consent-yes'));
    assert.deepEqual(updates(s.calls), [IDENTIFIED_GRANTED], `login age=${age}`);
  }
  for (const badLogin of [{ t: NOW - 365 * DAY }, { t: NOW + 1 }, { t: 'x' }, '{quebrado']) {
    const s = setup({ stored: { v: 'denied', t: NOW - DAY }, heatingLogin: badLogin });
    s.click(s.cookieLink);
    s.click(s.btn('consent-yes'));
    assert.notDeepEqual(updates(s.calls), [IDENTIFIED_GRANTED], JSON.stringify(badLogin));
  }

  // Kimi P2 (rodada 8): no Sim, os dois sinais de anúncio saem do próprio consent.js, sem depender do auth.js carregado:
  // desligados com sessão marcada ou vínculo ligado, ligados de novo no consentimento comum; o Não não mexe nos sinais.
  const sinais = (calls: unknown[][]) => calls.filter((c) => c[0] === 'set' && typeof c[1] === 'string' && String(c[1]).startsWith('allow_'));
  for (const opts of [{ heatingLogin: { t: NOW - DAY } }, { heatingLink: { v: 'granted', t: NOW - DAY, uid: 'UidAtivo123' } }]) {
    const s = setup({ stored: { v: 'denied', t: NOW - DAY }, ...opts });
    s.click(s.cookieLink);
    s.click(s.btn('consent-yes'));
    assert.deepEqual(sinais(s.calls), [['set', 'allow_google_signals', false], ['set', 'allow_ad_personalization_signals', false]], JSON.stringify(opts));
    const iUpd = s.calls.findIndex((c) => c[0] === 'consent' && c[1] === 'update');
    const iSig = s.calls.findIndex((c) => c[0] === 'set' && c[1] === 'allow_google_signals');
    assert.ok(iUpd >= 0 && iSig > iUpd);
  }
  const comum = setup({ stored: { v: 'denied', t: NOW - DAY } });
  comum.click(comum.cookieLink);
  comum.click(comum.btn('consent-yes'));
  assert.deepEqual(sinais(comum.calls), [['set', 'allow_google_signals', true], ['set', 'allow_ad_personalization_signals', true]]);
  const nao = setup({ stored: { v: 'granted', t: NOW - DAY }, heatingLogin: { t: NOW - DAY } });
  nao.click(nao.cookieLink);
  nao.click(nao.btn('consent-no'));
  assert.deepEqual(sinais(nao.calls), []);

  // Sinal do auth.js (sessão confirmada pelo Firebase, mesmo sem heating-link-v1 legível).
  const switched = setup({ stored: { v: 'denied', t: NOW - MIN }, authIdentified: () => true });
  switched.click(switched.cookieLink);
  switched.click(switched.btn('consent-yes'));
  assert.deepEqual(updates(switched.calls), [IDENTIFIED_GRANTED]);

  // Vínculo desligado, vencido, futuro, com uid inválido ou quebrado: volta a conceder os quatro sinais.
  for (const badLink of [
    { v: 'denied', t: NOW, uid: 'UidAtivo123' },
    { v: 'granted', t: NOW - 365 * DAY, uid: 'UidAtivo123' },
    { v: 'granted', t: NOW + 1, uid: 'UidAtivo123' },
    '{"v":"granted","t":1e400,"uid":"UidAtivo123"}',
    { v: 'granted', t: NOW, uid: '' },
    { v: 'granted', t: NOW, uid: 'uid-invalido' },
    { v: 'granted', t: NOW },
    '{quebrado',
  ]) {
    const s = setup({ heatingLink: badLink });
    s.click(s.btn('consent-yes'));
    assert.deepEqual(updates(s.calls), [GRANTED], JSON.stringify(badLink));
  }

  // Só o heating-uid-v1, sem o vínculo, não nega anúncios (o uid sozinho não é a regra).
  const soUid = setup({ heatingUid: { uid: 'UidAtivo123', t: NOW - MIN } });
  soUid.click(soUid.btn('consent-yes'));
  assert.deepEqual(updates(soUid.calls), [GRANTED]);

  // "Não" com o vínculo ligado continua negando os quatro sinais.
  const denied = setup({ stored: { v: 'granted', t: NOW - DAY }, heatingLink: { v: 'granted', t: NOW - DAY, uid: 'UidAtivo123' } });
  denied.click(denied.cookieLink);
  denied.click(denied.btn('consent-no'));
  assert.deepEqual(updates(denied.calls), [DENIED]);
});

test('sem localStorage: banner a cada página, Sim ainda concede, nada quebra; sem gtag também não quebra', () => {
  const s = setup({ storage: Blocked });
  assert.ok(s.banner());
  s.click(s.btn('consent-yes'));
  assert.deepEqual(updates(s.calls), [GRANTED]);
  const g = setup({ gtag: false });
  g.click(g.btn('consent-yes'));
  assert.equal(g.clarity.length, 1);
});

// ------------------------------------------------------------------ padrão inline nas páginas escritas à mão
function inlineGa(rel: string): string {
  const html = readFileSync(new URL(`../../${rel}`, import.meta.url), 'utf8');
  return [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]).find((b) => b.includes("gtag('consent','default'"))!;
}

// O Base.astro (blog) usa define:vars: o Astro põe as constantes no começo do mesmo script.
function inlineBase(): string {
  const src = readFileSync(new URL('../src/layouts/Base.astro', import.meta.url), 'utf8');
  const m = src.match(/define:vars=\{\{ GA4_ID, CONTENT_GROUP: contentGroup \}\}>([\s\S]*?)<\/script>/);
  assert.ok(m, 'inline do GA4 no Base.astro');
  return "const GA4_ID = 'G-K7Y87QNCX8'; const CONTENT_GROUP = 'insights-artigo';" + m![1];
}

function runInline(rel: string, stored: unknown, heatingUid?: unknown, heatingLink?: unknown, heatingLogin?: unknown) {
  const storage = new Storage();
  if (stored !== undefined) storage.setItem('consent-v1', typeof stored === 'string' ? stored : JSON.stringify(stored));
  if (heatingUid !== undefined) storage.setItem('heating-uid-v1', typeof heatingUid === 'string' ? heatingUid : JSON.stringify(heatingUid));
  if (heatingLink !== undefined) storage.setItem('heating-link-v1', typeof heatingLink === 'string' ? heatingLink : JSON.stringify(heatingLink));
  if (heatingLogin !== undefined) storage.setItem('heating-login-v1', typeof heatingLogin === 'string' ? heatingLogin : JSON.stringify(heatingLogin));
  const ctx: Record<string, unknown> = { localStorage: storage, location: { hostname: 'guifav.dev' }, JSON, Date: Object.assign(function () {}, { now: () => NOW }) };
  ctx.window = ctx;
  vm.runInContext(rel === 'Base.astro' ? inlineBase() : inlineGa(rel), vm.createContext(ctx));
  // Cópia por JSON: os objetos nascem em outro realm (vm).
  return JSON.parse(JSON.stringify(((ctx.dataLayer as unknown[]) ?? []).map((a) => Array.from(a as ArrayLike<unknown>)))) as unknown[][];
}

const MATERIAIS = [
  'ferramentas/planilha-custos-ia',
  'ferramentas/mapa-decisao-ia',
  'materiais/livro-desbloqueando-a-inteligencia-artificial',
].flatMap((m) => [`${m}/index.html`, `${m}/enviado/index.html`]);
for (const rel of ['index.html', 'portfolio/index.html', 'consultoria/index.html', 'ferramentas/index.html', 'ferramentas/llm-ranking/index.html', 'cursos/index.html', 'prioridade/index.html', 'privacidade/index.html', 'termos/index.html', ...MATERIAIS, 'Base.astro']) {
  test(`${rel}: padrão negado antes do config; aceite válido vira update granted e lê o vínculo e o uid antes do config`, () => {
    const names = (l: unknown[][]) => l.map((c) => `${c[0]}:${c[0] === 'consent' ? c[1] : ''}`);
    const none = runInline(rel, undefined);
    assert.deepEqual(none.find((c) => c[0] === 'consent'), ['consent', 'default', DENIED]);
    assert.deepEqual(names(none).filter((n) => n.startsWith('consent') || n.startsWith('config')), ['consent:default', 'config:']);
    const ok = runInline(rel, { v: 'granted', t: NOW - DAY });
    assert.deepEqual(names(ok).filter((n) => n.startsWith('consent') || n.startsWith('config')), ['consent:default', 'consent:update', 'config:']);
    assert.deepEqual(ok.find((c) => c[0] === 'consent' && c[1] === 'update')![2], GRANTED);
    const sets = (l: unknown[][]) => l.filter((c) => c[0] === 'set').map((c) => c.slice(1));
    assert.deepEqual(sets(none), [['ads_data_redaction', true]]);
    assert.deepEqual(sets(ok), [['ads_data_redaction', true]]);
    for (const stored of [{ v: 'granted', t: NOW - 366 * DAY }, { v: 'denied', t: NOW }, { v: 'granted' },
      { v: 'granted', t: NOW + DAY }, '{"v":"granted","t":1e400}']) {
      assert.deepEqual(names(runInline(rel, stored)).filter((n) => n === 'consent:update'), [], JSON.stringify(stored));
    }

    const LINK = { v: 'granted', t: NOW - 100 * DAY, uid: 'UidHead123' };
    const uidSet = (l: unknown[][]) => l.find((c) => c[0] === 'set' && typeof c[1] === 'object' && 'user_id' in (c[1] as object));

    // Vínculo ligado + heating-uid-v1 do mesmo uid (até 30 min): nega os 3 sinais de anúncio (analytics_storage continua
    // granted), desliga os sinais no GA4 e seta user_id, tudo ANTES do config.
    const withUid = runInline(rel, { v: 'granted', t: NOW - DAY }, { uid: 'UidHead123', t: NOW - 29 * MIN }, LINK);
    assert.deepEqual(
      names(withUid).filter((n) => n.startsWith('consent') || n.startsWith('config')),
      ['consent:default', 'consent:update', 'consent:update', 'config:'],
    );
    assert.deepEqual(updates(withUid), [GRANTED, AD_DENIED]);
    const configIdx = withUid.findIndex((c) => c[0] === 'config');
    const adDeniedIdx = withUid.findIndex((c) => c[0] === 'consent' && c[1] === 'update' && (c[2] as { ad_storage?: string }).ad_storage === 'denied');
    const sigIdx = withUid.findIndex((c) => c[0] === 'set' && c[1] === 'allow_google_signals' && c[2] === false);
    const persIdx = withUid.findIndex((c) => c[0] === 'set' && c[1] === 'allow_ad_personalization_signals' && c[2] === false);
    const uidIdx = withUid.findIndex((c) => c[0] === 'set' && typeof c[1] === 'object' && (c[1] as { user_id?: string }).user_id === 'UidHead123');
    assert.ok(adDeniedIdx > 0 && sigIdx > adDeniedIdx && persIdx > sigIdx && uidIdx > persIdx && configIdx > uidIdx, JSON.stringify(withUid));

    // Vínculo ligado sem uid recente (visita nova depois de 30 min, ou uid de outra pessoa): anúncios negados e
    // sinais desligados do mesmo jeito, mas sem user_id (o auth.js decide depois de confirmar a sessão).
    for (const uid of [undefined, { uid: 'UidHead123', t: NOW - 30 * MIN }, { uid: 'UidHead123', t: NOW - DAY },
      { uid: 'OutroUid999', t: NOW - MIN }, { uid: 'UidHead123', t: NOW + DAY }, '{quebrado']) {
      const res = runInline(rel, { v: 'granted', t: NOW - DAY }, uid, LINK);
      assert.deepEqual(updates(res), [GRANTED, AD_DENIED], JSON.stringify(uid));
      assert.ok(res.some((c) => c[0] === 'set' && c[1] === 'allow_google_signals' && c[2] === false), JSON.stringify(uid));
      assert.equal(uidSet(res), undefined, JSON.stringify(uid));
    }

    // Sem o vínculo ligado (ausente, desligado, vencido, futuro, uid inválido, quebrado), nem com heating-uid-v1 válido:
    // nada de anúncio negado, sinal nem user_id.
    for (const link of [undefined, { v: 'denied', t: NOW, uid: 'UidHead123' }, { v: 'granted', t: NOW - 365 * DAY, uid: 'UidHead123' },
      { v: 'granted', t: NOW + DAY, uid: 'UidHead123' }, { v: 'granted', t: NOW, uid: 'uid-com-traco' }, { v: 'granted', t: NOW }, '{quebrado']) {
      const res = runInline(rel, { v: 'granted', t: NOW - DAY }, { uid: 'UidHead123', t: NOW - MIN }, link);
      assert.deepEqual(sets(res), [['ads_data_redaction', true]], JSON.stringify(link));
      assert.deepEqual(updates(res), [GRANTED], JSON.stringify(link));
    }

    // Marca de sessão logada sem vínculo: nega anúncios e sinais antes do config, sem user_id (mesmo com uid sobrando).
    for (const age of [0, 200 * DAY, 365 * DAY - 1]) {
      const res = runInline(rel, { v: 'granted', t: NOW - DAY }, { uid: 'UidHead123', t: NOW - MIN }, undefined, { t: NOW - age });
      assert.deepEqual(updates(res), [GRANTED, AD_DENIED], `login age=${age}`);
      assert.ok(res.some((c) => c[0] === 'set' && c[1] === 'allow_google_signals' && c[2] === false));
      assert.ok(res.some((c) => c[0] === 'set' && c[1] === 'allow_ad_personalization_signals' && c[2] === false));
      assert.equal(uidSet(res), undefined);
      assert.ok(res.findIndex((c) => c[0] === 'config') > res.findIndex((c) => c[0] === 'set' && c[1] === 'allow_google_signals'));
    }
    // Marca vencida, futura, sem t ou quebrada: nada negado. Vínculo quebrado não impede a marca de negar.
    for (const login of [{ t: NOW - 365 * DAY }, { t: NOW + DAY }, {}, '{quebrado']) {
      const res = runInline(rel, { v: 'granted', t: NOW - DAY }, undefined, undefined, login);
      assert.deepEqual(updates(res), [GRANTED], JSON.stringify(login));
    }
    assert.deepEqual(updates(runInline(rel, { v: 'granted', t: NOW - DAY }, undefined, '{quebrado', { t: NOW })), [GRANTED, AD_DENIED]);

    // Sem consent-v1 granted, o vínculo e o uid nunca setam sinais, consentimento nem user_id.
    for (const consent of [undefined, { v: 'denied', t: NOW }, { v: 'granted', t: NOW - 366 * DAY }]) {
      const res = runInline(rel, consent, { uid: 'UidHead123', t: NOW - 5 * MIN }, LINK);
      assert.deepEqual(sets(res), [['ads_data_redaction', true]], JSON.stringify(consent));
      assert.deepEqual(updates(res), [], JSON.stringify(consent));
    }
  });
}
