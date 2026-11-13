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
}

function setup(opts: Opts = {}) {
  const calls: unknown[][] = [];
  const html = new El('html', { lang: opts.lang ?? 'pt-BR' });
  const body = new El('body', { 'data-theme': opts.theme ?? 'light' }, html);
  const nav = new El('nav', {}, body);
  const main = new El('main', {}, body);
  const footer = new El('footer', {}, body);
  if (opts.signup) new El('article', { 'data-ga-event': 'sign_up' }, main);
  const listeners: Record<string, ((e: unknown) => void)[]> = {};
  const document = {
    documentElement: html,
    body,
    baseURI: 'https://guifav.dev/portfolio/',
    addEventListener(type: string, fn: (e: unknown) => void) {
      (listeners[type] ??= []).push(fn);
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
    location: { href: 'https://guifav.dev/portfolio/', origin: 'https://guifav.dev', host: 'guifav.dev' },
    sessionStorage: opts.storage ?? new Storage(),
  };
  ctx.window = ctx;
  // Cópia por JSON: os objetos nascem em outro realm (vm), e o deepStrictEqual compara protótipos.
  if (opts.gtag !== false) ctx.gtag = (...a: unknown[]) => calls.push(JSON.parse(JSON.stringify(a)));
  vm.runInContext(SRC, vm.createContext(ctx));
  const fire = (type: string, target: El, extra: Record<string, unknown> = {}) => {
    for (const fn of listeners[type] ?? []) fn({ type, target, button: 0, ...extra });
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
    ['event', 'contact_click', { cta_location: 'menu', transport_type: B }],
    ['event', 'schedule_click', { cta_location: 'menu', transport_type: B }],
    ['event', 'contact_click', { cta_location: 'rodape', transport_type: B }],
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
    ['event', 'schedule_click', { cta_location: 'projetos', transport_type: B }],
    ['event', 'schedule_click', { cta_location: 'consultoria', transport_type: B }],
    ['event', 'contact_click', { cta_location: 'hero', transport_type: B }],
    ['event', 'schedule_click', { cta_location: 'conteudo', transport_type: B }],
  ]);
});

test('clique num filho do link (ícone, span) conta como clique no link', () => {
  const s = setup();
  const a = new El('a', { href: 'https://calendar.app.google/x', 'data-cta-location': 'hero' }, s.main);
  s.fire('click', new El('span', {}, a));
  assert.deepEqual(events(s.calls), [['event', 'schedule_click', { cta_location: 'hero', transport_type: B }]]);
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
  s.fire('click', new El('div', {}, s.main));
  assert.deepEqual(events(s.calls), []);
});

test('variações do mailto do Gui continuam sendo contato', () => {
  const s = setup();
  s.fire('click', new El('a', { href: 'mailto:Contact@GuiFav.dev?subject=Oi' }, s.main));
  s.fire('click', new El('a', { href: 'MAILTO:contact%40guifav.dev' }, s.main));
  assert.deepEqual(events(s.calls), [
    ['event', 'contact_click', { cta_location: 'conteudo', transport_type: B }],
    ['event', 'contact_click', { cta_location: 'conteudo', transport_type: B }],
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
  s.calls.length = 0;
  // O handler do próprio botão (site.js) roda antes, no alvo; o do track.js, depois, no document.
  s.html.lang = 'en';
  s.fire('click', lt);
  s.body.setAttribute('data-theme', 'dark');
  s.fire('click', new El('span', {}, tt));
  s.html.lang = 'pt-BR';
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
  assert.deepEqual(events(s.calls), [['event', 'schedule_click', { cta_location: 'hero', transport_type: B }]]);
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
  for (const fn of s.listeners.click) {
    fn({ type: 'click', target: null, button: 0 });
    fn({ type: 'click', target: { nodeType: 3 }, button: 0 });
  }
  assert.deepEqual(s.calls, []);
});

test('registra só os ouvintes de click e auxclick', () => {
  assert.deepEqual(Object.keys(setup().listeners).sort(), ['auxclick', 'click']);
});
