// Tema claro/escuro e troca PT/EN, compartilhados pelas páginas do site.
// Cada página traz as traduções em <script type="application/json" id="i18n-en">,
// com uma chave para cada data-i18n (innerHTML) e data-i18n-alt (atributo alt) da página.
(function () {
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* sem storage: só não lembra */ } }
  };

  var tt = document.getElementById('theme-toggle');
  function applyTheme(t) {
    document.body.setAttribute('data-theme', t);
    tt.textContent = t === 'dark' ? '☀' : '☾';
    tt.setAttribute('aria-label', t === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
  }
  var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  applyTheme(store.get('theme') || (prefersDark ? 'dark' : 'light'));
  tt.addEventListener('click', function () {
    var next = document.body.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    store.set('theme', next);
  });

  var en = {};
  var src = document.getElementById('i18n-en');
  if (src) en = JSON.parse(src.textContent);

  var html = document.querySelectorAll('[data-i18n]');
  var alts = document.querySelectorAll('[data-i18n-alt]');
  var pt = {}, ptAlt = {};
  html.forEach(function (el) { pt[el.getAttribute('data-i18n')] = el.innerHTML; });
  alts.forEach(function (el) { ptAlt[el.getAttribute('data-i18n-alt')] = el.getAttribute('alt'); });

  var lang = 'pt';
  var lt = document.getElementById('lang-toggle');
  function applyLang(l) {
    lang = l;
    lt.textContent = l === 'pt' ? 'EN' : 'PT';
    document.documentElement.lang = l === 'pt' ? 'pt-BR' : 'en';
    html.forEach(function (el) {
      var k = el.getAttribute('data-i18n');
      el.innerHTML = l === 'en' && en[k] !== undefined ? en[k] : pt[k];
    });
    alts.forEach(function (el) {
      var k = el.getAttribute('data-i18n-alt');
      el.setAttribute('alt', l === 'en' && en[k] !== undefined ? en[k] : ptAlt[k]);
    });
  }
  lt.addEventListener('click', function () {
    applyLang(lang === 'pt' ? 'en' : 'pt');
    store.set('lang', lang);
  });
  if (store.get('lang') === 'en') applyLang('en');
})();
