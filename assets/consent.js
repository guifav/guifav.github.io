// Consentimento de cookies (issue #68), compartilhado pela home, pelo portfólio e pelo blog.
//
// O padrão é negado: o script inline do GA4 de cada página chama gtag('consent','default', tudo 'denied')
// antes do config e, se o visitante já aceitou, gtag('consent','update', tudo 'granted'). É o Consent Mode
// avançado: sem aceite, o GA4 manda só sinais sem cookie e sem identificador. O Microsoft Clarity só carrega
// depois do aceite (window.guifavClarity, definido no script inline do Clarity).
//
// Este arquivo:
// - mostra o banner ("Aceitar os cookies?", Sim e Não, link para a política) enquanto não há escolha válida;
// - grava a escolha em localStorage "consent-v1" ({v: "granted"|"denied", t: epoch ms}), válida por 365 dias;
// - Sim: consent update granted e carrega o Clarity; Não: consent update denied e apaga os cookies do GA4 e do
//   Clarity (se o Clarity já estava gravando, recarrega a página para ele parar);
// - qualquer link com data-consent-open (o "Cookies" do rodapé) reabre o banner;
// - texto em português ou inglês conforme o idioma da página (e muda junto com o seletor de idioma).
// Sem localStorage, o banner aparece a cada página e nada é gravado; sem gtag, nada quebra.
(function () {
  var d = document;
  var KEY = 'consent-v1';
  var MAX_AGE = 365 * 24 * 3600 * 1000;
  var POLICY = '/blog/privacidade/#cookies';
  var TEXT = {
    pt: { q: 'Aceitar os cookies?', yes: 'Sim', no: 'Não', policy: 'Política de privacidade e cookies' },
    en: { q: 'Accept cookies?', yes: 'Yes', no: 'No', policy: 'Privacy and cookie policy' }
  };
  var ALL = ['ad_storage', 'ad_user_data', 'ad_personalization', 'analytics_storage'];
  var banner = null;
  var parts = null; // q, policy, yes, no

  function read() {
    try {
      var c = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (c && (c.v === 'granted' || c.v === 'denied') && typeof c.t === 'number' && Date.now() - c.t < MAX_AGE) return c.v;
    } catch (e) { /* sem storage ou valor quebrado: sem escolha */ }
    return null;
  }

  function write(v) {
    try { localStorage.setItem(KEY, JSON.stringify({ v: v, t: Date.now() })); } catch (e) { /* sem storage */ }
  }

  function consent(v) {
    var o = {};
    for (var i = 0; i < ALL.length; i++) o[ALL[i]] = v;
    if (typeof window.gtag === 'function') window.gtag('consent', 'update', o);
  }

  function clearCookies() {
    var names = (d.cookie || '').split(';').map(function (c) { return c.split('=')[0].trim(); });
    var host = location.hostname.replace(/\.$/, '');
    for (var i = 0; i < names.length; i++) {
      var n = names[i];
      if (!/^(_ga|_ga_.+|_gid|_gcl_.+|_clck|_clsk|CLID|MUID|_uetsid|_uetvid)$/.test(n)) continue;
      var past = n + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
      d.cookie = past;
      d.cookie = past + '; domain=' + host;
      d.cookie = past + '; domain=.' + host;
    }
  }

  function lang() { return /^en(?:-|$)/i.test(d.documentElement.lang || '') ? 'en' : 'pt'; }

  function paint() {
    if (!banner) return;
    var t = TEXT[lang()];
    parts.q.textContent = t.q;
    parts.policy.textContent = t.policy;
    parts.yes.textContent = t.yes;
    parts.no.textContent = t.no;
    banner.setAttribute('aria-label', t.q);
  }

  function hide() {
    if (banner) banner.hidden = true;
  }

  function choose(v) {
    var before = read();
    write(v);
    consent(v);
    hide();
    if (v === 'granted') {
      if (typeof window.guifavClarity === 'function') window.guifavClarity();
    } else {
      clearCookies();
      // O Clarity não para depois de carregado: recarrega para a página voltar sem ele.
      if (before === 'granted' && window.clarity) location.reload();
    }
  }

  function el(tag, attrs, parent) {
    var e = d.createElement(tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function show() {
    if (!banner) {
      banner = el('div', { 'class': 'consent', role: 'dialog', 'aria-live': 'polite' });
      var p = el('p', {}, banner);
      parts = { q: el('span', {}, p) };
      p.appendChild(d.createTextNode(' '));
      parts.policy = el('a', { href: POLICY }, p);
      var btns = el('div', { 'class': 'consent-btns' }, banner);
      parts.yes = el('button', { type: 'button', 'class': 'consent-yes' }, btns);
      parts.no = el('button', { type: 'button', 'class': 'consent-no' }, btns);
      parts.yes.addEventListener('click', function () { choose('granted'); });
      parts.no.addEventListener('click', function () { choose('denied'); });
      d.body.appendChild(banner);
    }
    paint();
    banner.hidden = false;
  }

  d.addEventListener('click', function (e) {
    var t = e.target;
    var a = t && t.closest ? t.closest('[data-consent-open]') : null;
    if (a) {
      if (e.preventDefault) e.preventDefault();
      show();
      return;
    }
    // Depois do handler do seletor de idioma (que roda no próprio botão), o texto acompanha.
    if (t && t.closest && t.closest('#lang-toggle')) paint();
  });

  if (!read()) show();
})();
