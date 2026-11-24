// Testes do /assets/track.js (issue #60): eventos próprios do GA4 da home, do portfólio e do blog.
// O script roda num contexto vm com um DOM mínimo; o fake só aceita os seletores que o script usa
// e falha com seletor desconhecido, para o teste não passar por um fake permissivo demais.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SRC = readFileSync(new URL('../../assets/track.js', import.meta.url), 'utf8');

type Attrs = Record<string, string>;

class El {
  tag: string;
  attrs: Attrs;
  parent: El | null;
  children: El[] = [];
  handlers: Record<string, ((e: unknown) => void)[]> = {};
  constructor(tag: string, attrs: Attrs = {}, parent: El | null = null) {
    this.tag = tag;
    this.attrs = { ...attrs };
    this.parent = parent;
    if (parent) parent.children.push(this);
  }
  get lang(): string {
    return this.attrs.lang ?? '';
  }
  set lang(v: string) {
    this.attrs.lang = v;
  }
  addEventListener(type: string, fn: (e: unknown) => void) {
    (this.handlers[type] ??= []).push(fn);
  }
  getAttribute(k: string): string | null {
    return k in this.attrs ? this.attrs[k] : null;
  }
  setAttribute(k: string, v: string): void {
    this.attrs[k] = String(v);
  }
  matches(sel: string): boolean {
    return sel.split(',').some((s) => matchOne(this, s.trim()));
  }
  closest(sel: string): El | null {
    for (let e: El | null = this; e; e = e.parent) if (e.matches(sel)) return e;
    return null;
  }
  querySelector(sel: string): El | null {
    return walk(this).slice(1).find((e) => e.matches(sel)) ?? null;
  }
  appendChild(c: El): El {
    c.parent = this;
    this.children.push(c);
    return c;
  }
  value = '';
}

function matchOne(el: El, s: string): boolean {
  const m = s.match(/^([a-z]*)(?:#([\w-]+))?(?:\[([\w-]+)(?:="([^"]*)")?\])?$/);
  if (!m || s === '') throw new Error(`seletor fora do fake: ${s}`);
  const [, tag, id, attr, val] = m;
  if (tag && el.tag !== tag) return false;
  if (id && el.attrs.id !== id) return false;
  if (attr) {
    if (!(attr in el.attrs)) return false;
    if (val !== undefined && el.attrs[attr] !== val) return false;
  }
  return true;
}

function walk(e: El, out: El[] = []): El[] {
  out.push(e);
  e.children.forEach((c) => walk(c, out));
  return out;
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

interface Opts {
  lang?: string;
  theme?: string;
  gtag?: boolean;
  signup?: boolean;
  storage?: Storage | typeof Blocked;
  lead?: string; // data-material-id da página de enviado (issue #99)
  cookie?: string;
  local?: Storage | typeof Blocked;
  referrer?: string; // document.referrer da página (issue #114)
  href?: string; // endereço da página aberta (issue #114)
}

function setup(opts: Opts = {}) {
  const calls: unknown[][] = [];
  const html = new El('html', { lang: opts.lang ?? 'pt-BR' });
  const body = new El('body', { 'data-theme': opts.theme ?? 'light' }, html);
  const nav = new El('nav', {}, body);
  const main = new El('main', {}, body);
  const footer = new El('footer', {}, body);
  if (opts.signup) new El('article', { 'data-ga-event': 'sign_up' }, main);
  if (opts.lead !== undefined) new El('header', { 'data-ga-event': 'generate_lead', 'data-material-id': opts.lead }, main);
  // Ouvintes do document com a fase: captura roda antes do alvo; borbulha, depois dele e dos ancestrais.
  const listeners: Record<string, { fn: (e: unknown) => void; capture: boolean }[]> = {};
  const document = {
    documentElement: html,
    body,
    cookie: opts.cookie ?? '',
    referrer: opts.referrer ?? '',
    baseURI: 'https://guifav.dev/portfolio/',
    createElement: (tag: string) => new El(tag),
    addEventListener(type: string, fn: (e: unknown) => void, opt?: boolean | { capture?: boolean }) {
      const capture = typeof opt === 'boolean' ? opt : Boolean(opt?.capture);
      (listeners[type] ??= []).push({ fn, capture });
    },
    querySelector(sel: string) {
      return walk(html).find((e) => e.matches(sel)) ?? null;
    },
    getElementById(id: string) {
      return walk(html).find((e) => e.attrs.id === id) ?? null;
    },
  };
  const ctx: Record<string, unknown> = {
    document,
    URL,
    location: { href: opts.href ?? 'https://guifav.dev/portfolio/', origin: 'https://guifav.dev', host: 'guifav.dev', hostname: 'guifav.dev' },
    sessionStorage: opts.storage ?? new Storage(),
    localStorage: opts.local ?? new Storage(),
    Date,
    JSON,
  };
  ctx.window = ctx;
  // Cópia por JSON: os objetos nascem em outro realm (vm), e o deepStrictEqual compara protótipos.
  if (opts.gtag !== false) ctx.gtag = (...a: unknown[]) => calls.push(JSON.parse(JSON.stringify(a)));
  vm.runInContext(SRC, vm.createContext(ctx));
  // Despacho com as fases do DOM: captura no document, alvo e ancestrais (borbulha), borbulha no document.
  const fire = (type: string, target: El, extra: Record<string, unknown> = {}) => {
    const ev = { type, target, button: 0, ...extra };
    for (const l of listeners[type] ?? []) if (l.capture) l.fn(ev);
    for (let e: El | null = target; e; e = e.parent) for (const fn of e.handlers[type] ?? []) fn(ev);
    for (const l of listeners[type] ?? []) if (!l.capture) l.fn(ev);
  };
  return { calls, html, body, nav, main, footer, fire, listeners, ctx };
}

const events = (calls: unknown[][]) => calls.filter((c) => c[0] === 'event');
const B = 'beacon';

test('ao abrir: grava idioma e tema como propriedades do usuário', () => {
  assert.deepEqual(setup().calls, [['set', 'user_properties', { site_language: 'pt', site_theme: 'light' }]]);
  assert.deepEqual(setup({ lang: 'en', theme: 'dark' }).calls, [
    ['set', 'user_properties', { site_language: 'en', site_theme: 'dark' }],
  ]);
  // Idioma é o do documento (pt-BR ou en); qualquer variante de en conta como en, o resto como pt.
  assert.equal((setup({ lang: 'en-US' }).calls[0][2] as Attrs).site_language, 'en');
  assert.equal((setup({ lang: 'english' }).calls[0][2] as Attrs).site_language, 'pt');
  assert.equal((setup({ lang: '' }).calls[0][2] as Attrs).site_language, 'pt');
  assert.equal((setup({ theme: 'sepia' }).calls[0][2] as Attrs).site_theme, 'light');
});

test('contato e agendamento no menu e no rodapé: posição pela estrutura da página', () => {
  const s = setup();
  s.fire('click', new El('a', { href: 'mailto:contact@guifav.dev' }, s.nav));
  s.fire('click', new El('a', { href: 'https://calendar.app.google/VyG67Nq5p6zsstAt8' }, s.nav));
  // No rodapé, o contato conta como CTA e como clique de rodapé.
  s.fire('click', new El('a', { href: 'mailto:contact@guifav.dev', 'data-cta-location': 'hero' }, s.footer));
  assert.deepEqual(events(s.calls), [
    ['event', 'contact_click', { cta_location: 'menu', origin_channel: 'direto', transport_type: B }],
    ['event', 'schedule_click', { cta_location: 'menu', origin_channel: 'direto', transport_type: B }],
    ['event', 'contact_click', { cta_location: 'rodape', origin_channel: 'direto', transport_type: B }],
    ['event', 'footer_click', { link_label: 'contato', transport_type: B }],
  ]);
});

test('CTAs no corpo da página: data-cta-location distingue os dois "Agende" da home', () => {
  const s = setup();
  const sec = new El('div', { class: 'section-cta' }, s.main);
  s.fire('click', new El('a', { href: 'https://calendar.app.google/VyG67Nq5p6zsstAt8', 'data-cta-location': 'projetos' }, sec));
  s.fire('click', new El('a', { href: 'https://calendar.app.google/VyG67Nq5p6zsstAt8', 'data-cta-location': 'consultoria' }, sec));
  s.fire('click', new El('a', { href: 'mailto:contact@guifav.dev', 'data-cta-location': 'hero' }, sec));
  // Sem atributo (link dentro de um post): "conteudo".
  s.fire('click', new El('a', { href: 'https://calendar.app.google/outro' }, s.main));
  assert.deepEqual(events(s.calls), [
    ['event', 'schedule_click', { cta_location: 'projetos', origin_channel: 'direto', transport_type: B }],
    ['event', 'schedule_click', { cta_location: 'consultoria', origin_channel: 'direto', transport_type: B }],
    ['event', 'contact_click', { cta_location: 'hero', origin_channel: 'direto', transport_type: B }],
    ['event', 'schedule_click', { cta_location: 'conteudo', origin_channel: 'direto', transport_type: B }],
  ]);
});

test('clique num filho do link (ícone, span) conta como clique no link', () => {
  const s = setup();
  const a = new El('a', { href: 'https://calendar.app.google/x', 'data-cta-location': 'hero' }, s.main);
  s.fire('click', new El('span', {}, a));
  assert.deepEqual(events(s.calls), [['event', 'schedule_click', { cta_location: 'hero', origin_channel: 'direto', transport_type: B }]]);
});

test('só o email do Gui e o Google Calendar são CTA', () => {
  const s = setup();
  for (const href of [
    'mailto:contact@righttowarn.ai',
    'mailto:outro@guifav.dev',
    'https://calendar.google.com/calendar/u/0',
    'http://calendar.app.google/x',
    'https://calendar.app.google.evil.example/x',
    '/portfolio/',
    'https://github.com/guifav',
    '#',
    'http://[invalido',
  ]) {
    s.fire('click', new El('a', { href }, s.main));
  }
  s.fire('click', new El('a', {}, s.main)); // âncora sem href
  s.fire('click', new El('a', {}, s.footer)); // no rodapé também não é link
  s.fire('click', new El('a', { href: 'http://[invalido' }, s.footer)); // href inválido: nem footer_click
  s.fire('click', new El('div', {}, s.main));
  assert.deepEqual(events(s.calls), []);
});

test('variações do mailto do Gui continuam sendo contato', () => {
  const s = setup();
  s.fire('click', new El('a', { href: 'mailto:Contact@GuiFav.dev?subject=Oi' }, s.main));
  s.fire('click', new El('a', { href: 'MAILTO:contact%40guifav.dev' }, s.main));
  assert.deepEqual(events(s.calls), [
    ['event', 'contact_click', { cta_location: 'conteudo', origin_channel: 'direto', transport_type: B }],
    ['event', 'contact_click', { cta_location: 'conteudo', origin_channel: 'direto', transport_type: B }],
  ]);
});

test('rodapé: rótulo estável por destino, igual em português e inglês', () => {
  const s = setup();
  const cases: [string, string][] = [
    ['https://github.com/guifav', 'github'],
    ['https://linkedin.com/in/guilhermefavaron', 'linkedin'],
    ['https://www.linkedin.com/in/guilhermefavaron', 'linkedin'],
    ['https://huggingface.co/guifav', 'huggingface'],
    ['https://youtube.com/@ia-aplicada-guilhermefavaron', 'youtube'],
    ['https://www.youtube.com/@x', 'youtube'],
    ['/', 'inicio'],
    ['/blog/assinar/', 'expert-insights'],
    ['https://guifav.dev/blog/assinar/', 'expert-insights'],
    ['/blog/', '/blog/'],
    ['https://exemplo.com/x', 'exemplo.com'],
    ['mailto:outro@exemplo.com', 'email'],
  ];
  for (const [href] of cases) s.fire('click', new El('a', { href }, s.footer));
  assert.deepEqual(
    events(s.calls),
    cases.map(([, label]) => ['event', 'footer_click', { link_label: label, transport_type: B }]),
  );
});

test('troca de idioma e de tema: evento e propriedade do usuário com o estado novo', () => {
  const s = setup();
  const lt = new El('button', { id: 'lang-toggle' }, s.nav);
  const tt = new El('button', { id: 'theme-toggle' }, s.nav);
  // Como o site.js e o script do blog: o handler do próprio botão troca o estado, no alvo.
  lt.addEventListener('click', () => { s.html.lang = s.html.lang === 'en' ? 'pt-BR' : 'en'; });
  tt.addEventListener('click', () => {
    s.body.setAttribute('data-theme', s.body.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
  });
  s.calls.length = 0;
  // O ouvinte do track.js roda depois, na borbulha até o document, e já lê o estado novo.
  s.fire('click', lt);
  s.fire('click', new El('span', {}, tt));
  s.fire('click', lt);
  assert.deepEqual(s.calls, [
    ['set', 'user_properties', { site_language: 'en' }],
    ['event', 'language_change', { site_language: 'en' }],
    ['set', 'user_properties', { site_theme: 'dark' }],
    ['event', 'theme_change', { site_theme: 'dark' }],
    ['set', 'user_properties', { site_language: 'pt' }],
    ['event', 'language_change', { site_language: 'pt' }],
  ]);
});

test('clique no botão sem mudança de estado não gera evento', () => {
  const s = setup({ theme: 'dark' });
  const tt = new El('button', { id: 'theme-toggle' }, s.nav);
  const lt = new El('button', { id: 'lang-toggle' }, s.nav);
  s.calls.length = 0;
  s.fire('click', tt);
  s.fire('click', lt);
  s.fire('click', new El('button', { id: 'outro' }, s.nav));
  assert.deepEqual(s.calls, []);
});

test('clique do meio (auxclick) em CTA conta; botão direito e auxclick em botão de tema não', () => {
  const s = setup();
  const a = new El('a', { href: 'https://calendar.app.google/x', 'data-cta-location': 'hero' }, s.main);
  s.fire('auxclick', a, { button: 1 });
  s.fire('auxclick', a, { button: 2 });
  const tt = new El('button', { id: 'theme-toggle' }, s.nav);
  s.body.setAttribute('data-theme', 'dark');
  s.fire('auxclick', tt, { button: 1 });
  assert.deepEqual(events(s.calls), [['event', 'schedule_click', { cta_location: 'hero', origin_channel: 'direto', transport_type: B }]]);
});

test('sign_up na página de inscrição confirmada, uma vez por sessão', () => {
  const storage = new Storage();
  const first = setup({ signup: true, storage });
  assert.deepEqual(events(first.calls), [['event', 'sign_up', { method: 'expert-insights' }]]);
  assert.equal(storage.getItem('ga_sign_up_sent'), '1');
  // Recarregar a página na mesma sessão não conta de novo.
  assert.deepEqual(events(setup({ signup: true, storage }).calls), []);
  // Outra sessão (storage novo) conta.
  assert.equal(events(setup({ signup: true }).calls).length, 1);
});

test('sign_up só existe com o marcador; sem sessionStorage, envia mesmo assim', () => {
  assert.deepEqual(events(setup().calls), []);
  assert.deepEqual(events(setup({ signup: true, storage: Blocked }).calls), [
    ['event', 'sign_up', { method: 'expert-insights' }],
  ]);
});

test('marcador com outro valor não é sign_up', () => {
  const html = new El('html', { lang: 'pt-BR' });
  const body = new El('body', { 'data-theme': 'light' }, html);
  new El('article', { 'data-ga-event': 'outro' }, body);
  const calls: unknown[][] = [];
  const ctx: Record<string, unknown> = {
    document: {
      documentElement: html,
      body,
      baseURI: 'https://guifav.dev/',
      addEventListener() {},
      querySelector: (sel: string) => walk(html).find((e) => e.matches(sel)) ?? null,
    },
    URL,
    sessionStorage: new Storage(),
    gtag: (...a: unknown[]) => calls.push(JSON.parse(JSON.stringify(a))),
  };
  ctx.window = ctx;
  vm.runInContext(SRC, vm.createContext(ctx));
  assert.deepEqual(events(calls), []);
});

test('sem gtag (página sem GA4): nada quebra e nada é enviado', () => {
  const s = setup({ gtag: false, signup: true });
  s.fire('click', new El('a', { href: 'mailto:contact@guifav.dev' }, s.footer));
  s.html.lang = 'en';
  s.fire('click', new El('button', { id: 'lang-toggle' }, s.nav));
  assert.deepEqual(s.calls, []);
});

test('alvo sem closest (documento, nó de texto) é ignorado', () => {
  const s = setup();
  s.calls.length = 0;
  for (const { fn } of s.listeners.click) {
    fn({ type: 'click', target: null, button: 0 });
    fn({ type: 'click', target: { nodeType: 3 }, button: 0 });
  }
  assert.deepEqual(s.calls, []);
});

test('registra click e auxclick na borbulha (depois do handler do botão) e submit na captura (antes do envio)', () => {
  const l = setup().listeners;
  assert.deepEqual(Object.keys(l).sort(), ['auxclick', 'click', 'submit']);
  assert.deepEqual([l.click, l.auxclick, l.submit].map((x) => x.map((y) => y.capture)), [[false], [false], [true]]);
});

// ---------------------------------------------------------------- Materiais (issue #99)

test('generate_lead na página de enviado, uma vez por sessão e por material', () => {
  const storage = new Storage();
  const first = setup({ lead: 'planilha-custos-ia', storage });
  assert.deepEqual(events(first.calls), [['event', 'generate_lead', { material_id: 'planilha-custos-ia' }]]);
  assert.deepEqual(events(setup({ lead: 'planilha-custos-ia', storage }).calls), []);
  // Outro material na mesma sessão conta de novo.
  assert.deepEqual(events(setup({ lead: 'mapa-decisao-ia', storage }).calls), [['event', 'generate_lead', { material_id: 'mapa-decisao-ia' }]]);
});

test('generate_lead: sem sessionStorage envia mesmo assim; id fora do formato não envia', () => {
  assert.equal(events(setup({ lead: 'planilha-custos-ia', storage: Blocked }).calls).length, 1);
  for (const bad of ['', 'Planilha', '../x', 'a'.repeat(65)]) assert.deepEqual(events(setup({ lead: bad }).calls), [], bad);
});

function formSetup(opts: Opts & { marked?: boolean } = {}) {
  const s = setup(opts);
  const form = new El('form', opts.marked === false ? {} : { 'data-ga-client-id': '' }, s.main);
  const input = new El('input', { type: 'hidden', name: 'ga_cid' }, form) as El & { value?: string };
  input.value = 'antigo';
  const button = new El('button', { type: 'submit' }, form);
  return { ...s, form, input, button };
}
const aceito = (t = Date.now() - 1000) => {
  const st = new Storage();
  st.setItem('consent-v1', JSON.stringify({ v: 'granted', t }));
  return st;
};
const GA = '_gcl=x; _ga=GA1.1.1234567890.1700000000; outro=1';

test('ga_cid: com o consentimento aceito, o envio preenche o client_id do cookie _ga', () => {
  const s = formSetup({ cookie: GA, local: aceito() });
  s.fire('submit', s.form);
  assert.equal(s.input.value, '1234567890.1700000000');
  assert.equal(s.listeners.submit.every((l) => l.capture), true, 'ouvinte de envio na captura');
});

test('ga_cid: sem consentimento, consentimento negado ou vencido, cookie ausente ou malformado, vai vazio', () => {
  const negado = new Storage();
  negado.setItem('consent-v1', JSON.stringify({ v: 'denied', t: Date.now() }));
  const casos: [string, Opts][] = [
    ['sem escolha', { cookie: GA }],
    ['negado', { cookie: GA, local: negado }],
    ['vencido', { cookie: GA, local: aceito(Date.now() - 366 * 864e5) }],
    ['data futura', { cookie: GA, local: aceito(Date.now() + 864e5) }],
    ['sem localStorage', { cookie: GA, local: Blocked }],
    ['sem cookie', { cookie: 'outro=1', local: aceito() }],
    ['malformado', { cookie: '_ga=GA1.1.abc.123', local: aceito() }],
  ];
  for (const [nome, o] of casos) {
    const s = formSetup(o);
    s.fire('submit', s.form);
    assert.equal(s.input.value, '', nome);
  }
});

test('ga_cid: formulário sem data-ga-client-id não é tocado', () => {
  const s = formSetup({ cookie: GA, local: aceito(), marked: false });
  s.fire('submit', s.form);
  assert.equal(s.input.value, 'antigo');
});

// ---------------------------------------------------------------- Origem da visita (issue #114)

type Origem = { canal: string; fonte: string | null; utm_source: string | null; utm_medium: string | null; utm_campaign: string | null;
  entrada: string | null };
const origemDe = (referrer: string, href: string): Origem => {
  const s = setup({ referrer, href });
  return JSON.parse(JSON.stringify((s.ctx.guifavOrigin as { current(): Origem }).current()));
};
const SEM = { utm_source: null, utm_medium: null, utm_campaign: null };

test('origem: classificador de canal com a lista fixa de fontes', () => {
  const P = 'https://guifav.dev/blog/x/';
  const casos: [string, string, string, string | null][] = [
    // [referrer, página de entrada, canal, fonte]
    ['', P, 'direto', null],
    ['https://www.google.com/', P, 'busca', 'google'],
    ['https://www.google.com.br/', P, 'busca', 'google'],
    ['https://google.co.uk/', P, 'busca', 'google'],
    ['android-app://com.google.android.googlequicksearchbox/', P, 'busca', 'google'],
    ['https://www.bing.com/', P, 'busca', 'bing'],
    ['https://duckduckgo.com/', P, 'busca', 'outro'],
    ['https://chatgpt.com/', P, 'assistente', 'chatgpt'],
    ['https://chat.openai.com/', P, 'assistente', 'chatgpt'],
    ['https://www.perplexity.ai/', P, 'assistente', 'perplexity'],
    ['https://claude.ai/', P, 'assistente', 'claude'],
    ['https://copilot.microsoft.com/', P, 'assistente', 'copilot'],
    ['https://edgeservices.bing.com/', P, 'assistente', 'copilot'],
    ['https://gemini.google.com/', P, 'assistente', 'gemini'],
    ['https://www.linkedin.com/', P, 'social', 'linkedin'],
    ['https://lnkd.in/', P, 'social', 'linkedin'],
    ['https://www.youtube.com/', P, 'social', 'youtube'],
    ['https://t.co/', P, 'social', 'outro'],
    ['https://github.com/guifav', P, 'outro_site', 'github'],
    ['https://mail.google.com/', P, 'email', 'outro'],
    ['https://exemplo.com.br/artigo', P, 'outro_site', 'outro'],
    ['https://docs.google.com/', P, 'outro_site', 'outro'], // só a busca do Google é busca
    ['https://guifav.dev/', P, 'desconhecida', null], // primeira página da aba com referrer interno: a origem se perdeu
    ['https://www.guifav.dev/', P, 'desconhecida', null],
    ['nao e url', P, 'direto', null],
    // UTM manda mais que o referrer; clique pago (id de anúncio ou utm_medium pago) manda mais que tudo.
    ['', `${P}?utm_source=chatgpt.com`, 'assistente', 'chatgpt'],
    ['', `${P}?utm_source=linkedin&utm_medium=social`, 'social', 'linkedin'],
    ['', `${P}?utm_source=linkedin&utm_medium=email`, 'email', 'linkedin'],
    ['', `${P}?utm_source=newsletter&utm_medium=email`, 'email', 'outro'],
    ['https://www.google.com/', `${P}?gclid=abc123`, 'pago', 'google'],
    ['', `${P}?utm_source=google&utm_medium=cpc`, 'pago', 'google'],
    ['', `${P}?utm_source=parceiro`, 'desconhecida', null],
    ['https://www.google.com/', `${P}?utm_source=parceiro`, 'busca', 'google'],
  ];
  for (const [ref, href, canal, fonte] of casos) {
    const o = origemDe(ref, href);
    assert.deepEqual([o.canal, o.fonte], [canal, fonte], `${ref} -> ${href}`);
  }
  assert.deepEqual(origemDe('', P), { canal: 'direto', fonte: null, ...SEM, entrada: '/blog/x/' });
});

test('origem: utm_medium referral e organic só valem depois da fonte conhecida e do referrer (revisão do agy no #127)', () => {
  const P = 'https://guifav.dev/blog/x/';
  const casos: [string, string, string, string | null][] = [
    ['', `${P}?utm_source=chatgpt.com&utm_medium=referral`, 'assistente', 'chatgpt'],
    ['', `${P}?utm_source=perplexity&utm_medium=referral`, 'assistente', 'perplexity'],
    ['', `${P}?utm_source=claude.ai&utm_medium=referral`, 'assistente', 'claude'],
    ['', `${P}?utm_source=gemini&utm_medium=referral`, 'assistente', 'gemini'],
    ['', `${P}?utm_source=linkedin&utm_medium=referral`, 'social', 'linkedin'],
    ['', `${P}?utm_source=chatgpt.com&utm_medium=organic`, 'assistente', 'chatgpt'],
    ['https://www.perplexity.ai/', `${P}?utm_medium=referral`, 'assistente', 'perplexity'],
    ['https://www.linkedin.com/', `${P}?utm_medium=organic`, 'social', 'linkedin'],
    // Sem fonte conhecida nem referrer, o medium fraco decide.
    ['', `${P}?utm_source=parceiro&utm_medium=referral`, 'outro_site', 'outro'],
    ['', `${P}?utm_medium=organic`, 'busca', 'outro'],
    // email e social continuam na frente da fonte.
    ['', `${P}?utm_source=chatgpt.com&utm_medium=email`, 'email', 'chatgpt'],
  ];
  for (const [ref, href, canal, fonte] of casos) {
    const o = origemDe(ref, href);
    assert.deepEqual([o.canal, o.fonte], [canal, fonte], `${ref} -> ${href}`);
  }
});

test('origem: clique pago sem referrer nem utm_source tira a fonte do parâmetro, sem guardar o valor', () => {
  const P = 'https://guifav.dev/blog/x/';
  const casos: [string, string][] = [['gclid', 'google'], ['gbraid', 'google'], ['wbraid', 'google'], ['dclid', 'google'],
    ['msclkid', 'bing'], ['li_fat_id', 'linkedin'], ['ttclid', 'outro']];
  for (const [param, fonte] of casos) {
    const o = origemDe('', `${P}?${param}=VALORSECRETO`);
    assert.deepEqual([o.canal, o.fonte], ['pago', fonte], param);
    assert.ok(!JSON.stringify(o).includes('VALORSECRETO'), param);
  }
  // Com utm_source, vale a fonte dele (ou "outro"), nunca a do parâmetro nem a do referrer.
  assert.deepEqual([origemDe('https://www.google.com/', `${P}?gclid=x&utm_source=parceiro`).canal,
    origemDe('https://www.google.com/', `${P}?gclid=x&utm_source=parceiro`).fonte], ['pago', 'outro']);
});

test('origem: utm_source fora da lista com referrer conhecido não herda a fonte do referrer quando a etiqueta decide', () => {
  const P = 'https://guifav.dev/blog/x/';
  for (const [href, canal] of [[`${P}?utm_source=substack&utm_medium=email`, 'email'], [`${P}?utm_source=substack&utm_medium=social`, 'social'],
    [`${P}?utm_source=parceiro&utm_medium=cpc`, 'pago']]) {
    const o = origemDe('https://www.google.com/', href);
    assert.deepEqual([o.canal, o.fonte, o.utm_source], [canal, 'outro', href.includes('substack') ? 'substack' : 'parceiro'], href);
  }
  // Sem etiqueta que decida, o referrer conhecido segue valendo (a fonte do utm_source não está na lista).
  assert.deepEqual([origemDe('https://www.google.com/', `${P}?utm_source=parceiro`).canal, origemDe('https://www.google.com/', `${P}?utm_source=parceiro`).fonte],
    ['busca', 'google']);
});

test('origem: fbclid não é anúncio; sem referrer, indica rede social (revisão do Roger no #127)', () => {
  const P = 'https://guifav.dev/blog/x/';
  const fb = origemDe('', `${P}?fbclid=META123`);
  assert.deepEqual([fb.canal, fb.fonte], ['social', 'outro']);
  const comRef = origemDe('https://l.facebook.com/', `${P}?fbclid=META123`);
  assert.deepEqual([comRef.canal, comRef.fonte], ['social', 'outro']);
  // O anúncio da Meta se reconhece pela etiqueta.
  const anuncio = origemDe('https://l.facebook.com/', `${P}?fbclid=META123&utm_source=facebook&utm_medium=paid_social`);
  assert.deepEqual([anuncio.canal, anuncio.fonte], ['pago', 'outro']);
  for (const o of [fb, comRef, anuncio]) assert.ok(!JSON.stringify(o).includes('META123'));
});

test('origem: apps Android e subdomínios dos serviços da lista', () => {
  const P = 'https://guifav.dev/blog/x/';
  const casos: [string, string, string][] = [
    ['android-app://com.google.android.apps.bard/', 'assistente', 'gemini'],
    ['android-app://com.openai.chatgpt/', 'assistente', 'chatgpt'],
    ['android-app://ai.perplexity.app.android/', 'assistente', 'perplexity'],
    ['android-app://com.anthropic.claude/', 'assistente', 'claude'],
    ['android-app://com.microsoft.copilot/', 'assistente', 'copilot'],
    ['android-app://com.microsoft.bing/', 'busca', 'bing'],
    ['android-app://com.linkedin.android/', 'social', 'linkedin'],
    ['android-app://com.google.android.youtube/', 'social', 'youtube'],
    ['https://copilot.bing.com/', 'assistente', 'copilot'],
    ['https://m.bing.com/', 'busca', 'bing'],
    ['https://www2.bing.com/', 'busca', 'bing'],
    ['https://l.instagram.com/', 'social', 'outro'],
    ['https://out.reddit.com/', 'social', 'outro'],
  ];
  for (const [ref, canal, fonte] of casos) {
    const o = origemDe(ref, P);
    assert.deepEqual([o.canal, o.fonte], [canal, fonte], ref);
  }
});

test('origem: UTM fora de [a-z0-9._-]{1,64} é recusado campo a campo; maiúsculas viram minúsculas', () => {
  const base = 'https://guifav.dev/?';
  assert.deepEqual(origemDe('', `${base}utm_source=LinkedIn&utm_medium=Social&utm_campaign=Lancamento_2026.v1`),
    { canal: 'social', fonte: 'linkedin', utm_source: 'linkedin', utm_medium: 'social', utm_campaign: 'lancamento_2026.v1', entrada: '/' });
  for (const ruim of ['a b', 'a+b', 'ação', 'a/b', 'https://x.com', 'a?b', 'a=b', 'a@b.com', 'x'.repeat(65), '', '%20']) {
    const o = origemDe('', `${base}utm_source=google&utm_campaign=${encodeURIComponent(ruim)}`);
    assert.equal(o.utm_campaign, null, ruim);
    assert.equal(o.utm_source, 'google', 'os outros campos ficam');
  }
  assert.equal(origemDe('', `${base}utm_campaign=${'x'.repeat(64)}`).utm_campaign, 'x'.repeat(64));
  // Nomes do protótipo casam com o padrão, mas não são fonte da lista.
  for (const nome of ['constructor', 'tostring', '__proto__', 'hasownproperty', 'valueof']) {
    assert.deepEqual([origemDe('', `${base}utm_source=${nome}`).canal, origemDe('', `${base}utm_source=${nome}`).fonte], ['desconhecida', null], nome);
  }
});

test('origem: nem a URL de onde a pessoa veio, nem o termo buscado, nem gclid ou query entram na origem', () => {
  const st = new Storage();
  const s = setup({ storage: st, referrer: 'https://www.google.com/search?q=termo+secreto&oq=termo',
    href: 'https://guifav.dev/blog/rag/?q=termo&gclid=GCLIDSECRETO&fbclid=FB1&utm_term=termo&utm_source=google#ancora' });
  const salvo = st.getItem('site_origin_v1')!;
  assert.deepEqual(JSON.parse(salvo), { canal: 'pago', fonte: 'google', utm_source: 'google', utm_medium: null, utm_campaign: null,
    entrada: '/blog/rag/' });
  for (const proibido of ['termo', 'secreto', 'GCLID', 'FB1', 'http', 'search', '?', '#', 'ancora']) {
    assert.ok(!salvo.includes(proibido), proibido);
  }
  assert.equal(Object.keys(JSON.parse(salvo)).length, 6, 'só os seis campos');
  void s;
});

test('origem: gravada na primeira página da aba e nunca trocada pela navegação interna', () => {
  const st = new Storage();
  origemDe('', 'x'); // sem storage compartilhado: não interfere
  setup({ storage: st, referrer: 'https://chatgpt.com/', href: 'https://guifav.dev/blog/agentes/?utm_source=chatgpt.com' });
  const primeira = st.getItem('site_origin_v1');
  // Segunda página da mesma aba: referrer interno e outra query; a origem continua a da entrada.
  const s2 = setup({ storage: st, referrer: 'https://guifav.dev/blog/agentes/', href: 'https://guifav.dev/tools/planilha-custos-ia/?utm_source=x' });
  assert.equal(st.getItem('site_origin_v1'), primeira);
  assert.deepEqual(JSON.parse(JSON.stringify((s2.ctx.guifavOrigin as { current(): Origem }).current())),
    { canal: 'assistente', fonte: 'chatgpt', utm_source: 'chatgpt.com', utm_medium: null, utm_campaign: null, entrada: '/blog/agentes/' });
  // Valor adulterado no storage: só o que casa com as listas e os padrões fica; o resto vira nulo.
  const bad = new Storage();
  bad.setItem('site_origin_v1', JSON.stringify({ canal: 'busca', fonte: 'google', entrada: 'https://x.com/?q=termo', utm_source: 'A B' }));
  const proto = new Storage();
  proto.setItem('site_origin_v1', JSON.stringify({ canal: 'social', fonte: 'constructor' }));
  assert.equal(JSON.parse(JSON.stringify((setup({ storage: proto }).ctx.guifavOrigin as { current(): Origem }).current())).fonte, null);
  const s3 = setup({ storage: bad, referrer: '', href: 'https://guifav.dev/' });
  assert.deepEqual(JSON.parse(JSON.stringify((s3.ctx.guifavOrigin as { current(): Origem }).current())),
    { canal: 'busca', fonte: 'google', ...SEM, entrada: null });
});

test('origem: sem sessionStorage, vale a classificação da página aberta, e nada quebra', () => {
  const s = setup({ storage: Blocked, referrer: 'https://www.bing.com/', href: 'https://guifav.dev/blog/' });
  assert.deepEqual(JSON.parse(JSON.stringify((s.ctx.guifavOrigin as { current(): Origem }).current())),
    { canal: 'busca', fonte: 'bing', ...SEM, entrada: '/blog/' });
});

const CAMPOS = ['origem_canal', 'origem_fonte', 'utm_source', 'utm_medium', 'utm_campaign', 'origem_entrada'];
const campos = (form: El) => Object.fromEntries(form.children.filter((c) => c.tag === 'input' && CAMPOS.includes(c.attrs.name))
  .map((c) => [c.attrs.name, c.value]));

test('origem: os formulários de inscrição e de material recebem os campos ocultos no envio, com ou sem cookies', () => {
  for (const action of ['https://insights.guifav.dev/subscribe', 'https://insights.guifav.dev/material']) {
    const s = setup({ referrer: 'https://www.linkedin.com/feed/', href: 'https://guifav.dev/blog/x/?utm_source=linkedin&utm_campaign=post-12' });
    const form = new El('form', { method: 'post', action }, s.main);
    const btn = new El('button', { type: 'submit' }, form);
    s.fire('submit', btn);
    assert.deepEqual(campos(form), { origem_canal: 'social', origem_fonte: 'linkedin', utm_source: 'linkedin', utm_medium: '',
      utm_campaign: 'post-12', origem_entrada: '/blog/x/' }, action);
    for (const c of form.children.filter((c) => c.tag === 'input')) assert.equal(c.attrs.type, 'hidden');
    // Reenvio (voltar e enviar de novo): os mesmos campos, sem duplicar.
    s.fire('submit', btn);
    assert.equal(form.children.filter((c) => c.tag === 'input').length, 6);
  }
});

test('origem: outros formulários não recebem campos de origem', () => {
  for (const action of ['https://guifav.dev/busca', 'https://insights.guifav.dev/confirm', 'http://insights.guifav.dev/subscribe',
    'https://insights.guifav.dev.evil.example/subscribe', '']) {
    const s = setup({ referrer: 'https://www.google.com/' });
    const form = new El('form', action ? { action } : {}, s.main);
    s.fire('submit', form);
    assert.deepEqual(campos(form), {}, action);
  }
});

test('origem: contact_click e schedule_click levam origin_channel e origin_source', () => {
  const s = setup({ referrer: 'https://www.perplexity.ai/', href: 'https://guifav.dev/' });
  s.fire('click', new El('a', { href: 'mailto:contact@guifav.dev', 'data-cta-location': 'hero' }, s.main));
  s.fire('click', new El('a', { href: 'https://calendar.app.google/x', 'data-cta-location': 'hero' }, s.main));
  assert.deepEqual(events(s.calls), [
    ['event', 'contact_click', { cta_location: 'hero', origin_channel: 'assistente', origin_source: 'perplexity', transport_type: B }],
    ['event', 'schedule_click', { cta_location: 'hero', origin_channel: 'assistente', origin_source: 'perplexity', transport_type: B }],
  ]);
  // O rodapé e os outros eventos não mudam.
  s.fire('click', new El('a', { href: 'https://github.com/guifav' }, s.footer));
  assert.deepEqual(events(s.calls).at(-1), ['event', 'footer_click', { link_label: 'github', transport_type: B }]);
});
