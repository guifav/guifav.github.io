// Tema claro/escuro, troca PT/EN e vitrine de projetos, compartilhados pelas páginas do site.
// Cada página traz as traduções em <script type="application/json" id="i18n-en">,
// com uma chave para cada data-i18n (innerHTML), data-i18n-alt (alt) e data-i18n-aria (aria-label).
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
  var arias = document.querySelectorAll('[data-i18n-aria]');
  var pt = {}, ptAlt = {}, ptAria = {};
  html.forEach(function (el) { pt[el.getAttribute('data-i18n')] = el.innerHTML; });
  alts.forEach(function (el) { ptAlt[el.getAttribute('data-i18n-alt')] = el.getAttribute('alt'); });
  arias.forEach(function (el) { ptAria[el.getAttribute('data-i18n-aria')] = el.getAttribute('aria-label'); });

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
    arias.forEach(function (el) {
      var k = el.getAttribute('data-i18n-aria');
      el.setAttribute('aria-label', l === 'en' && en[k] !== undefined ? en[k] : ptAria[k]);
    });
  }
  lt.addEventListener('click', function () {
    applyLang(lang === 'pt' ? 'en' : 'pt');
    store.set('lang', lang);
  });
  if (store.get('lang') === 'en') applyLang('en');

  // Vitrine em carrossel: o projeto ativo fica no centro, de frente; os demais recolhem para os lados.
  var cf = document.getElementById('coverflow');
  if (cf) {
    var stage = cf.querySelector('.cf-stage');
    var items = Array.prototype.slice.call(cf.querySelectorAll('.cf-item'));
    var caps = cf.querySelectorAll('.cf-cap');
    var count = cf.querySelector('.cf-count');
    var active = Math.min(parseInt(cf.getAttribute('data-start'), 10) || 0, items.length - 1);

    var render = function () {
      var w = items[0].offsetWidth;
      var narrow = w < 480;
      items.forEach(function (el, i) {
        var d = i - active, a = Math.abs(d), s = d < 0 ? -1 : 1;
        var t;
        if (d === 0) {
          t = 'translateX(0) translateZ(0) rotateY(0deg)';
        } else {
          var x = s * (w * (narrow ? 0.42 : 0.62) + (a - 1) * w * (narrow ? 0.1 : 0.2));
          t = 'translateX(' + x + 'px) translateZ(' + (-w * 0.38) + 'px) rotateY(' + (-s * 62) + 'deg)';
        }
        el.style.transform = t;
        el.style.zIndex = String(100 - a);
        el.style.opacity = a > (narrow ? 2 : 4) ? '0' : '1';
        el.style.pointerEvents = a > (narrow ? 2 : 4) ? 'none' : '';
        el.classList.toggle('is-active', d === 0);
        el.setAttribute('aria-selected', d === 0 ? 'true' : 'false');
        el.tabIndex = -1;
      });
      caps.forEach(function (c, i) { c.hidden = i !== active; });
      count.textContent = (active + 1) + ' / ' + items.length;
    };
    var go = function (i) { active = Math.max(0, Math.min(items.length - 1, i)); render(); };

    items.forEach(function (el, i) {
      el.setAttribute('role', 'option');
      el.addEventListener('click', function () {
        if (swiped) { swiped = false; return; }
        if (i === active) { window.location.href = el.getAttribute('data-href'); } else { go(i); }
      });
    });
    cf.querySelectorAll('.cf-arrow').forEach(function (b) {
      b.addEventListener('click', function () { go(active + parseInt(b.getAttribute('data-dir'), 10)); });
    });
    stage.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowLeft') { go(active - 1); e.preventDefault(); }
      else if (e.key === 'ArrowRight') { go(active + 1); e.preventDefault(); }
      else if (e.key === 'Enter') { window.location.href = items[active].getAttribute('data-href'); }
    });
    var x0 = null, swiped = false;
    stage.addEventListener('pointerdown', function (e) { x0 = e.clientX; });
    stage.addEventListener('pointerup', function (e) {
      if (x0 === null) return;
      var dx = e.clientX - x0; x0 = null;
      if (Math.abs(dx) > 40) { swiped = true; go(active + (dx < 0 ? 1 : -1)); }
    });
    window.addEventListener('resize', render);
    render();
  }
})();
