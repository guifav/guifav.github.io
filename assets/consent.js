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
// - Sim: consent update granted (se a sessão já está identificada, concede só analytics_storage e mantém
//   ad_storage/ad_user_data/ad_personalization negados) e carrega o Clarity; Não: consent update denied e apaga os
//   cookies do GA4 e do Clarity (se o Clarity já estava gravando, recarrega a página para ele parar);
// - qualquer link com data-consent-open (o "Cookies" do rodapé) reabre o banner;
// - texto em português ou inglês conforme o idioma da página (e muda junto com o seletor de idioma).
// Sem localStorage, o banner aparece a cada página e nada é gravado; sem gtag, nada quebra.
(function () {
  var d = document;
  var KEY = 'consent-v1';
  var MAX_AGE = 365 * 24 * 3600 * 1000;
  var UID_KEY = 'heating-uid-v1';
  var UID_MAX_AGE = 18e5; // 30 minutos (mesma regra do snippet do <head>)
  var UID_RE = /^[0-9A-Za-z]{1,128}$/;
  var POLICY = '/privacidade/#cookies';
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
      // Idade finita e não negativa: data futura ou Infinity (t: 1e400) nunca vale como escolha.
      if (c && (c.v === 'granted' || c.v === 'denied') && typeof c.t === 'number' && Date.now() - c.t >= 0 && Date.now() - c.t < MAX_AGE) return c.v;
    } catch (e) { /* sem storage ou valor quebrado: sem escolha */ }
    return null;
  }

  function write(v) {
    try { localStorage.setItem(KEY, JSON.stringify({ v: v, t: Date.now() })); } catch (e) { /* sem storage */ }
  }

  function hasValidHeatingUid() {
    try {
      var u = JSON.parse(localStorage.getItem(UID_KEY) || 'null');
      if (u && typeof u.uid === 'string' && UID_RE.test(u.uid) && typeof u.t === 'number') {
        var age = Date.now() - u.t;
        return age >= 0 && age < UID_MAX_AGE;
      }
    } catch (e) { /* sem storage ou valor quebrado */ }
    return false;
  }

  function isIdentified() {
    if (hasValidHeatingUid()) return true;
    try {
      if (typeof window.guifavAuthIdentified === 'function' && window.guifavAuthIdentified()) return true;
    } catch (e) { /* ignora */ }
    return false;
  }

  function consent(v) {
    var o = {};
    if (v === 'granted' && isIdentified()) {
      o.ad_storage = 'denied';
      o.ad_user_data = 'denied';
      o.ad_personalization = 'denied';
      o.analytics_storage = 'granted';
    } else {
      for (var i = 0; i < ALL.length; i++) o[ALL[i]] = v;
    }
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

  function notify(v) {
    try {
      if (typeof window.dispatchEvent === 'function' && typeof window.CustomEvent === 'function') {
        window.dispatchEvent(new window.CustomEvent('guifav-consent', { detail: v }));
      }
    } catch (e) { /* sem CustomEvent */ }
  }

  function choose(v) {
    write(v);
    consent(v);
    hide();
    notify(v);
    if (v === 'granted') {
      if (typeof window.guifavClarity === 'function') window.guifavClarity();
    } else {
      clearCookies();
      // O Clarity não para depois de carregado: se ele está rodando nesta página (só carrega depois de um Sim),
      // recarrega para ela voltar sem ele. Não depende do que estava gravado (storage bloqueado, outra aba).
      if (window.clarity) location.reload();
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
