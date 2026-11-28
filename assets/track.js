// Eventos próprios do GA4 (issue #60), compartilhados pela home, pelo portfólio e pelo blog.
// Completa a medição automática do GA4 (page_view, scroll, cliques de saída), que não vê mailto,
// não distingue botões com o mesmo link e não vê o que muda sem trocar a URL.
//
// - contact_click (mailto:contact@guifav.dev) e schedule_click (https://calendar.app.google/...),
//   com cta_location: "menu" ou "rodape" pela posição do link; nos demais, o atributo
//   data-cta-location do link; sem ele (link dentro de um post), "conteudo".
// - footer_click em todo link do rodapé, com link_label estável, igual em português e inglês.
// - file_download em todo link com data-ga-download não vazio (ex.: "Baixar GIF" do diagrama da home,
//   issue #148), com file_name (atributo download ou nome no caminho), file_extension, link_url (sem
//   query/fragmento), site_language (pt ou en no momento do clique), origin_channel e origin_source.
// - language_change e theme_change, mais as propriedades do usuário site_language e site_theme,
//   gravadas ao abrir a página e a cada troca.
// - sign_up (method expert-insights) na página com data-ga-event="sign_up", uma vez por sessão.
// - generate_lead (material_id) na página de enviado de um material, com data-ga-event="generate_lead" e
//   data-material-id, uma vez por sessão e por material (issue #99).
// - Formulário com data-ga-client-id: no envio, o campo oculto ga_cid recebe o client_id do cookie _ga, só com
//   o consentimento aceito (consent-v1, a mesma regra do snippet do GA4); sem ele, o campo vai vazio. O Worker
//   guarda esse id com o pedido e manda o evento de download com ele, ligando pedido e download no GA4 (#99).
// - Origem da visita (issue #114): na primeira página da aba, um rótulo da origem (canal, fonte de uma lista fixa, UTM
//   que casa com [a-z0-9._-]{1,64} e o caminho da página de entrada, sem query) vai para o sessionStorage, e nenhuma
//   navegação depois o troca. Não é cookie e some ao fechar a aba. No envio dos formulários de inscrição e de material
//   (action em insights.guifav.dev), ele vai em campos ocultos, com ou sem o aceite dos cookies; o Worker revalida tudo
//   (workers/newsletter/src/origem.ts, com as mesmas listas). contact_click e schedule_click levam origin_channel e
//   origin_source. Nunca guarda nem envia a URL de onde a pessoa veio, o termo buscado, gclid e afins nem id algum.
//
// Carregado depois do site.js (ou do script de tema do blog): o ouvinte fica no document e roda
// depois do handler do próprio botão, então já lê o idioma e o tema novos.
// Sem gtag, nada é enviado e nada quebra.
(function () {
  var d = document;
  var CONTACT = 'contact@guifav.dev';
  var SCHEDULE_HOST = 'calendar.app.google';
  var HOST_LABELS = { 'github.com': 'github', 'linkedin.com': 'linkedin', 'huggingface.co': 'huggingface', 'youtube.com': 'youtube' };
  var PATH_LABELS = { '/': 'inicio', '/blog/assinar/': 'expert-insights', '/privacidade/': 'privacidade', '/privacidade/#cookies': 'cookies', '/termos/': 'termos' };
  var SIGNUP_KEY = 'ga_sign_up_sent';
  var LEAD_KEY = 'ga_generate_lead_sent:';
  var CONSENT_TTL = 31536e6;
  var ORIGIN_KEY = 'site_origin_v1';
  var WORKER_HOST = 'insights.guifav.dev';
  var ORIGIN_FORMS = { '/subscribe': 1, '/material': 1 };
  // Login com Google One Tap e user_id no GA4 (épico #197, corte 2): carregado em idle, só em guifav.dev e fora de /admin.
  var AUTH_SRC = '/assets/auth.js?v=202611281030';

  // ---- Origem da visita (issue #114). Listas iguais às de workers/newsletter/src/origem.ts e da migração 0006. ----
  var CANAIS = ['busca', 'assistente', 'social', 'email', 'pago', 'outro_site', 'direto', 'desconhecida'];
  var UTM_RE = /^[a-z0-9._-]{1,64}$/;
  var PATH_RE = /^\/(?:[A-Za-z0-9._-]+\/)*[A-Za-z0-9._-]*$/; // segmentos não vazios; até 200 caracteres (pathOk)
  // Canal de cada fonte da lista fixa, quando nada mais (utm_medium, clique pago) diz outra coisa.
  var FONTE_CANAL = { google: 'busca', bing: 'busca', chatgpt: 'assistente', perplexity: 'assistente', claude: 'assistente',
    copilot: 'assistente', gemini: 'assistente', linkedin: 'social', youtube: 'social', github: 'outro_site', outro: '' };
  // Host de onde a visita veio (sem www.), na ordem: o primeiro que casar vale. [expressão, fonte, canal]
  // Nos apps Android, o referrer é android-app://<pacote>, e o pacote faz o papel do host.
  var HOSTS = [
    [/^(?:chatgpt\.com|chat\.openai\.com|com\.openai\.chatgpt)$/, 'chatgpt', 'assistente'],
    [/^(?:perplexity\.ai|ai\.perplexity\.app\.android)$/, 'perplexity', 'assistente'],
    [/^(?:claude\.ai|com\.anthropic\.claude)$/, 'claude', 'assistente'],
    [/^(?:copilot\.microsoft\.com|copilot\.bing\.com|edgeservices\.bing\.com|copilot\.cloud\.microsoft|m365\.cloud\.microsoft|com\.microsoft\.copilot)$/,
      'copilot', 'assistente'],
    [/^(?:gemini\.google\.com|bard\.google\.com|com\.google\.android\.apps\.bard)$/, 'gemini', 'assistente'],
    [/^(?:mail\.google\.com|com\.google\.android\.gm|outlook\.(?:live|office|office365)\.com|mail\.yahoo\.com)$/, 'outro', 'email'],
    [/^(?:google\.(?:[a-z]{2,3}|com?\.[a-z]{2})|com\.google\.android\.googlequicksearchbox)$/, 'google', 'busca'],
    [/^(?:(?:cn|m|www2)\.)?bing\.com$|^com\.microsoft\.bing$/, 'bing', 'busca'],
    [/^(?:duckduckgo\.com|search\.yahoo\.com|ecosia\.org|search\.brave\.com|yandex\.[a-z]{2,3}|baidu\.com)$/, 'outro', 'busca'],
    [/^(?:[a-z0-9-]+\.)?linkedin\.com$|^(?:lnkd\.in|com\.linkedin\.android)$/, 'linkedin', 'social'],
    [/^(?:m\.)?youtube\.com$|^(?:youtu\.be|com\.google\.android\.youtube)$/, 'youtube', 'social'],
    [/^(?:[a-z0-9-]+\.)?github\.com$/, 'github', 'outro_site'],
    [/^(?:x\.com|t\.co|twitter\.com|(?:[a-z]+\.)?facebook\.com|(?:l\.)?instagram\.com|(?:old\.|out\.)?reddit\.com|news\.ycombinator\.com|bsky\.app|threads\.net|threads\.com)$/,
      'outro', 'social']
  ];
  // utm_source com o nome do serviço (sem o domínio): o que não está aqui passa pelos HOSTS (chatgpt.com, por exemplo).
  var SOURCE_NAMES = { google: 'google', bing: 'bing', chatgpt: 'chatgpt', openai: 'chatgpt', perplexity: 'perplexity',
    claude: 'claude', copilot: 'copilot', gemini: 'gemini', linkedin: 'linkedin', youtube: 'youtube', github: 'github' };
  // Clique de anúncio, com a fonte que o parâmetro já indica: só classifica; o valor nunca é guardado.
  var PAID_IDS = { gclid: 'google', gbraid: 'google', wbraid: 'google', dclid: 'google', msclkid: 'bing', li_fat_id: 'linkedin',
    ttclid: 'outro' };
  // O fbclid não é anúncio: a Meta o acrescenta a todo link que sai do Facebook e do Instagram, orgânico ou pago. Ele só
  // indica a rede social quando o referrer não vem (app, no-referrer); o anúncio da Meta se reconhece pelo utm_medium.
  var SOCIAL_IDS = ['fbclid'];
  var PAID_MEDIUM = /^(?:cpc|ppc|cpm|cpv|cpa|paid|paidsearch|paid[._-]?(?:search|social|media)|display|banner|ads?)$/;
  // utm_medium que decide o canal sozinho, na frente da fonte e do referrer.
  var MEDIUM_FORTE = [[/^(?:e-?mail|newsletter)$/, 'email'], [/^(?:social|social[._-]?(?:media|network)|sm)$/, 'social']];
  // utm_medium genérico: só vale depois da fonte conhecida e do referrer (utm_source=chatgpt.com&utm_medium=referral é
  // assistente, não outro site).
  var MEDIUM_FRACO = [[/^organic$/, 'busca'], [/^referral$/, 'outro_site']];

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
    if (u.origin === new URL(d.baseURI).origin) return own(PATH_LABELS, u.pathname + u.hash) || own(PATH_LABELS, u.pathname) || u.pathname;
    var host = u.hostname.replace(/^www\./, '');
    return HOST_LABELS[host] || host;
  }

  // Só chaves próprias: utm_source=constructor (que casa com o padrão) não pode achar nada do protótipo.
  function own(obj, k) { return Object.prototype.hasOwnProperty.call(obj, k) ? obj[k] : null; }

  function pathOk(p) { return typeof p === 'string' && p.length <= 200 && PATH_RE.test(p); }

  function host(h) { return String(h || '').toLowerCase().replace(/\.$/, '').replace(/^www\./, ''); }

  function matchHost(h) {
    for (var i = 0; i < HOSTS.length; i++) if (HOSTS[i][0].test(h)) return { fonte: HOSTS[i][1], canal: HOSTS[i][2] };
    return null;
  }

  function medium(lista, v) {
    for (var i = 0; v && i < lista.length; i++) if (lista[i][0].test(v)) return lista[i][1];
    return null;
  }

  function utm(params, k) {
    var v = params.get(k);
    v = v === null ? '' : v.toLowerCase();
    return UTM_RE.test(v) ? v : null;
  }

  // Classifica a visita a partir do referrer e da URL da página de entrada. Só o host do referrer é lido, e da URL
  // só o caminho, os três utm_* e a presença de um id de clique pago. Exposta para os testes.
  function classify(referrer, href, selfHost) {
    var url, params;
    try { url = new URL(href); params = url.searchParams; } catch (e) { return null; }
    var o = { canal: 'direto', fonte: null, utm_source: utm(params, 'utm_source'), utm_medium: utm(params, 'utm_medium'),
      utm_campaign: utm(params, 'utm_campaign'), entrada: pathOk(url.pathname) ? url.pathname : null };
    var ref = '';
    if (referrer) {
      try {
        var r = new URL(referrer);
        ref = r.protocol === 'android-app:' ? host(r.hostname || r.href.replace(/^android-app:\/\//, '').split('/')[0]) : host(r.hostname);
      } catch (e) { ref = ''; }
    }
    var internal = ref !== '' && ref === host(selfHost);
    var refHit = ref && !internal ? matchHost(ref) : null;
    var src = o.utm_source;
    var srcName = src ? (own(SOURCE_NAMES, src) || (matchHost(host(src)) || {}).fonte || null) : null;
    var paid = false, paidFonte = null;
    for (var k in PAID_IDS) {
      if (own(PAID_IDS, k) && params.has(k)) { paid = true; paidFonte = paidFonte || PAID_IDS[k]; }
    }
    if (o.utm_medium && PAID_MEDIUM.test(o.utm_medium)) paid = true;
    var forte = medium(MEDIUM_FORTE, o.utm_medium);
    var fraco = medium(MEDIUM_FRACO, o.utm_medium);
    var socialId = false;
    for (var i = 0; i < SOCIAL_IDS.length; i++) if (params.has(SOCIAL_IDS[i])) socialId = true;
    var srcCanal = src ? (own(SOURCE_NAMES, src) ? own(FONTE_CANAL, srcName) : (matchHost(host(src)) || {}).canal || null) : null;
    // Precedência: clique pago, utm_medium forte, fonte conhecida do utm_source, referrer, fbclid, utm_medium fraco.
    if (paid) o.canal = 'pago';
    else if (forte) o.canal = forte;
    else if (srcCanal) o.canal = srcCanal;
    else if (refHit) o.canal = refHit.canal;
    else if (socialId) o.canal = 'social';
    else if (fraco) o.canal = fraco;
    else if (ref && !internal) o.canal = 'outro_site';
    else if (o.utm_source || o.utm_medium || o.utm_campaign || internal) o.canal = 'desconhecida';
    if (o.canal !== 'direto' && o.canal !== 'desconhecida') {
      // Canal vindo das etiquetas (pago ou utm_medium forte) com um utm_source: a fonte é a dele, ou "outro", e nunca a do
      // referrer, que pode ser outro serviço (utm_source=substack&utm_medium=email aberto a partir do Google).
      if (src && (paid || forte)) o.fonte = srcName || 'outro';
      else if (paid) o.fonte = paidFonte || (refHit && refHit.fonte) || 'outro';
      else o.fonte = srcName || (refHit && refHit.fonte) || 'outro';
    }
    return o;
  }

  function validOrigin(o) {
    if (!o || typeof o !== 'object' || CANAIS.indexOf(o.canal) < 0) return null;
    var fonte = o.canal === 'direto' || o.canal === 'desconhecida' || typeof o.fonte !== 'string' ? null
      : (own(FONTE_CANAL, o.fonte) !== null ? o.fonte : null);
    var keep = function (rx, v) { return typeof v === 'string' && rx.test(v) ? v : null; };
    return { canal: o.canal, fonte: fonte, utm_source: keep(UTM_RE, o.utm_source), utm_medium: keep(UTM_RE, o.utm_medium),
      utm_campaign: keep(UTM_RE, o.utm_campaign), entrada: pathOk(o.entrada) ? o.entrada : null };
  }

  // Primeira página da aba: classifica e grava. Nas seguintes, só lê. Sem storage, vale a classificação desta página.
  function sessionOrigin() {
    var saved = null;
    try { saved = validOrigin(JSON.parse(window.sessionStorage.getItem(ORIGIN_KEY) || 'null')); } catch (e) { saved = null; }
    if (saved) return saved;
    var o = validOrigin(classify(d.referrer || '', window.location.href, window.location.hostname)) ||
      { canal: 'desconhecida', fonte: null, utm_source: null, utm_medium: null, utm_campaign: null, entrada: null };
    try { window.sessionStorage.setItem(ORIGIN_KEY, JSON.stringify(o)); } catch (e) { /* sem storage: só não lembra */ }
    return o;
  }

  var origin;
  try { origin = sessionOrigin(); } catch (e) { origin = null; } // a origem nunca derruba o resto da medição
  origin = origin || { canal: 'desconhecida', fonte: null, utm_source: null, utm_medium: null, utm_campaign: null, entrada: null };
  window.guifavOrigin = { classify: classify, current: function () { return origin; } };

  function originForm(f) {
    var a = f.getAttribute('action');
    var u;
    try { u = new URL(a || '', d.baseURI); } catch (e) { return false; }
    return u.protocol === 'https:' && u.hostname === WORKER_HOST && ORIGIN_FORMS[u.pathname] === 1;
  }

  function setHidden(f, name, value) {
    var input = f.querySelector('input[name="' + name + '"]');
    if (!input) {
      input = d.createElement('input');
      input.setAttribute('type', 'hidden');
      input.setAttribute('name', name);
      f.appendChild(input);
    }
    input.value = value === null ? '' : value;
  }

  function downloadParams(a, u) {
    var id = (a.getAttribute('data-ga-download') || '').trim();
    if (!id || (u.protocol !== 'https:' && u.protocol !== 'http:')) return null;
    var rawName = (a.getAttribute('download') || '').trim();
    if (!rawName) {
      var seg = u.pathname.split('/').pop() || '';
      try { rawName = decodeURIComponent(seg); } catch (e) { rawName = seg; }
    }
    if (!rawName) rawName = id;
    var extMatch = rawName.match(/[^./]\.([a-z0-9]{1,10})$/i) || u.pathname.match(/[^./]\.([a-z0-9]{1,10})$/i);
    var p = {
      file_name: rawName,
      link_url: u.origin + u.pathname,
      site_language: language(),
      origin_channel: origin.canal,
      transport_type: 'beacon'
    };
    if (extMatch) p.file_extension = extMatch[1].toLowerCase();
    if (origin.fonte) p.origin_source = origin.fonte;
    return p;
  }

  function onLink(a) {
    var u = parse(a);
    if (!u) return; // href que o navegador não interpreta: nenhum evento, nem de rodapé
    var ev = ctaEvent(u);
    if (ev) {
      var p = { cta_location: ctaLocation(a), origin_channel: origin.canal, transport_type: 'beacon' };
      if (origin.fonte) p.origin_source = origin.fonte;
      send('event', ev, p);
    }
    var dl = downloadParams(a, u);
    if (dl) send('event', 'file_download', dl);
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

  function consentGranted() {
    try {
      var c = JSON.parse(window.localStorage.getItem('consent-v1') || 'null');
      var age = c && typeof c.t === 'number' ? Date.now() - c.t : -1;
      return Boolean(c && c.v === 'granted' && age >= 0 && age < CONSENT_TTL);
    } catch (e) {
      return false;
    }
  }

  // _ga = GA1.<n>.<aleatório>.<epoch>; o client_id do GA4 são os dois últimos números.
  function gaClientId() {
    var m = (d.cookie || '').match(/(?:^|;\s*)_ga=GA\d+\.\d+\.(\d{1,20}\.\d{1,20})(?:;|$)/);
    return m ? m[1] : '';
  }

  // Captura: roda antes de qualquer outro ouvinte de envio, e o valor já sai no POST.
  d.addEventListener('submit', function (e) {
    var t = e.target;
    var f = t && typeof t.closest === 'function' ? t.closest('form[data-ga-client-id]') : null;
    var input = f && typeof f.querySelector === 'function' ? f.querySelector('input[name="ga_cid"]') : null;
    if (input) input.value = consentGranted() ? gaClientId() : '';
    // Origem (issue #114): só nos formulários do Worker, com ou sem o aceite dos cookies (legítimo interesse).
    var of = t && typeof t.closest === 'function' ? t.closest('form') : null;
    if (of && typeof of.querySelector === 'function' && originForm(of)) {
      setHidden(of, 'origem_canal', origin.canal);
      setHidden(of, 'origem_fonte', origin.fonte);
      setHidden(of, 'utm_source', origin.utm_source);
      setHidden(of, 'utm_medium', origin.utm_medium);
      setHidden(of, 'utm_campaign', origin.utm_campaign);
      setHidden(of, 'origem_entrada', origin.entrada);
    }
  }, true);

  d.addEventListener('click', handle);
  // Clique do meio abre o link em outra aba sem disparar click.
  d.addEventListener('auxclick', function (e) { if (e.button === 1) handle(e); });

  var lead = d.querySelector('[data-ga-event="generate_lead"]');
  var materialId = lead ? lead.getAttribute('data-material-id') || '' : '';
  if (/^[a-z0-9-]{1,64}$/.test(materialId)) {
    var leadSeen = false;
    try { leadSeen = window.sessionStorage.getItem(LEAD_KEY + materialId) === '1'; } catch (e) { /* sem storage: envia */ }
    if (!leadSeen) {
      send('event', 'generate_lead', { material_id: materialId });
      try { window.sessionStorage.setItem(LEAD_KEY + materialId, '1'); } catch (e) { /* sem storage: só não lembra */ }
    }
  }

  if (d.querySelector('[data-ga-event="sign_up"]')) {
    var seen = false;
    try { seen = window.sessionStorage.getItem(SIGNUP_KEY) === '1'; } catch (e) { /* sem storage: envia */ }
    if (!seen) {
      send('event', 'sign_up', { method: 'expert-insights' });
      try { window.sessionStorage.setItem(SIGNUP_KEY, '1'); } catch (e) { /* sem storage: só não lembra */ }
    }
  }

  // Carregamento tardio do login (épico #197, corte 2): só em guifav.dev, nunca em prévias locais nem em /admin.
  function scheduleAuth() {
    var loc = window.location;
    if (!loc || loc.hostname !== 'guifav.dev') return;
    var p = String(loc.pathname || '/');
    if (p === '/admin' || p.indexOf('/admin/') === 0) return;
    var load = function () {
      try {
        if (typeof d.createElement !== 'function') return;
        var parent = d.body || d.head || d.documentElement;
        if (!parent || typeof parent.appendChild !== 'function') return;
        var s = d.createElement('script');
        s.setAttribute('src', AUTH_SRC);
        s.async = true;
        parent.appendChild(s);
      } catch (e) { /* sem DOM para injetar: ignora */ }
    };
    var schedule = function () {
      if (typeof window.requestIdleCallback === 'function') {
        window.requestIdleCallback(load, { timeout: 3000 });
      } else if (typeof window.setTimeout === 'function') {
        window.setTimeout(load, 1500);
      }
    };
    // Só depois do load da página (Firebase e GIS nunca disputam os recursos do primeiro carregamento).
    if (d.readyState === 'complete' || typeof window.addEventListener !== 'function') {
      schedule();
    } else {
      window.addEventListener('load', schedule, { once: true });
    }
  }
  scheduleAuth();
})();
