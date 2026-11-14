// Eventos próprios do GA4 (issue #60), compartilhados pela home, pelo portfólio e pelo blog.
// Completa a medição automática do GA4 (page_view, scroll, cliques de saída), que não vê mailto,
// não distingue botões com o mesmo link e não vê o que muda sem trocar a URL.
//
// - contact_click (mailto:contact@guifav.dev) e schedule_click (https://calendar.app.google/...),
//   com cta_location: "menu" ou "rodape" pela posição do link; nos demais, o atributo
//   data-cta-location do link; sem ele (link dentro de um post), "conteudo".
// - footer_click em todo link do rodapé, com link_label estável, igual em português e inglês.
// - language_change e theme_change, mais as propriedades do usuário site_language e site_theme,
//   gravadas ao abrir a página e a cada troca.
// - sign_up (method expert-insights) na página com data-ga-event="sign_up", uma vez por sessão.
//
// Carregado depois do site.js (ou do script de tema do blog): o ouvinte fica no document e roda
// depois do handler do próprio botão, então já lê o idioma e o tema novos.
// Sem gtag, nada é enviado e nada quebra.
(function () {
  var d = document;
  var CONTACT = 'contact@guifav.dev';
  var SCHEDULE_HOST = 'calendar.app.google';
  var HOST_LABELS = { 'github.com': 'github', 'linkedin.com': 'linkedin', 'huggingface.co': 'huggingface', 'youtube.com': 'youtube' };
  var PATH_LABELS = { '/': 'inicio', '/blog/assinar/': 'expert-insights', '/blog/privacidade/': 'cookies' };
  var SIGNUP_KEY = 'ga_sign_up_sent';

  function send() {
    if (typeof window.gtag === 'function') window.gtag.apply(window, arguments);
  }

  function parse(a) {
    try { return new URL(a.getAttribute('href'), d.baseURI); } catch (e) { return null; }
  }

  function ctaEvent(u) {
    if (!u) return null;
    if (u.protocol === 'mailto:') {
      var to;
      try { to = decodeURIComponent(u.pathname); } catch (e) { return null; }
      return to.toLowerCase() === CONTACT ? 'contact_click' : null;
    }
    if (u.protocol === 'https:' && u.hostname === SCHEDULE_HOST) return 'schedule_click';
    return null;
  }

  function ctaLocation(a) {
    if (a.closest('nav')) return 'menu';
    if (a.closest('footer')) return 'rodape';
    return a.getAttribute('data-cta-location') || 'conteudo';
  }

  function footerLabel(u) {
    if (!u) return 'outro';
    if (u.protocol === 'mailto:') return ctaEvent(u) === 'contact_click' ? 'contato' : 'email';
    if (u.origin === new URL(d.baseURI).origin) return PATH_LABELS[u.pathname] || u.pathname;
    var host = u.hostname.replace(/^www\./, '');
    return HOST_LABELS[host] || host;
  }

  function onLink(a) {
    var u = parse(a);
    if (!u) return; // href que o navegador não interpreta: nenhum evento, nem de rodapé
    var ev = ctaEvent(u);
    if (ev) send('event', ev, { cta_location: ctaLocation(a), transport_type: 'beacon' });
    if (a.closest('footer')) send('event', 'footer_click', { link_label: footerLabel(u), transport_type: 'beacon' });
  }

  function language() { return /^en(?:-|$)/i.test(d.documentElement.lang || '') ? 'en' : 'pt'; }
  function theme() { return d.body.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'; }

  var state = { site_language: language(), site_theme: theme() };
  send('set', 'user_properties', { site_language: state.site_language, site_theme: state.site_theme });

  function onToggle(b) {
    var isLang = b.getAttribute('id') === 'lang-toggle';
    var key = isLang ? 'site_language' : 'site_theme';
    var now = isLang ? language() : theme();
    if (now === state[key]) return;
    state[key] = now;
    var props = {};
    props[key] = now;
    // A propriedade vem antes do evento, para o próprio evento já sair com o valor novo.
    send('set', 'user_properties', props);
    send('event', isLang ? 'language_change' : 'theme_change', props);
  }

  function handle(e) {
    var t = e.target;
    if (!t || typeof t.closest !== 'function') return;
    var a = t.closest('a[href]');
    if (a) { onLink(a); return; }
    if (e.type !== 'click') return;
    var b = t.closest('#lang-toggle, #theme-toggle');
    if (b) onToggle(b);
  }

  d.addEventListener('click', handle);
  // Clique do meio abre o link em outra aba sem disparar click.
  d.addEventListener('auxclick', function (e) { if (e.button === 1) handle(e); });

  if (d.querySelector('[data-ga-event="sign_up"]')) {
    var seen = false;
    try { seen = window.sessionStorage.getItem(SIGNUP_KEY) === '1'; } catch (e) { /* sem storage: envia */ }
    if (!seen) {
      send('event', 'sign_up', { method: 'expert-insights' });
      try { window.sessionStorage.setItem(SIGNUP_KEY, '1'); } catch (e) { /* sem storage: só não lembra */ }
    }
  }
})();
