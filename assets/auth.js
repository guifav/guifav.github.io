// Login com Google One Tap, aviso próprio do login e user_id no GA4 (épico #197, corte 2).
// Carregado tardiamente pelo assets/track.js, só em guifav.dev e nunca em prévias locais nem em /admin.
//
// - Nada do Google (Firebase SDK em gstatic.com nem GIS em accounts.google.com) carrega antes de:
//   (a) o banner de cookies ter sido respondido (granted ou denied), (b) haver sessão Firebase salva
//   no IndexedDB (firebaseLocalStorageDb / firebaseLocalStorage), ou (c) clique em "Entrar com Google".
// - Compartilha o app [DEFAULT] e o tenant guifav-dev-g6dp4 com o curso (mesma origem): quem entrou no curso
//   já aparece logado aqui. Sessões de outro tenant são ignoradas.
// - Google One Tap (GIS): só para quem não está logado, só depois que o visitante respondeu ao banner de
//   cookies (consent-v1 válido, granted ou denied), e nunca em /privacidade/, /termos/, /prioridade/ nem 404.
// - Aviso do login (heating-link-v1 no localStorage, 365 dias por uid): só aparece depois do banner respondido
//   e com consent-v1 granted.
// - Só com as 3 condições (logado no tenant, consent-v1 granted e heating-link-v1 granted para o uid):
//   mantém heating-uid-v1 = {uid, t} no localStorage (lida pelo snippet do <head> antes do gtag('config')),
//   nega ad_storage/ad_user_data/ad_personalization (mantendo analytics_storage granted), desliga sinais de
//   anúncio no GA4, grava user_id, envia auth_identified uma vez por sessão e envia
//   POST https://insights.guifav.dev/identidade com o ID token uma vez por sessão e por uid.
// - Quando qualquer condição deixa de valer (ou em outra aba via evento storage): apaga heating-uid-v1,
//   envia gtag('set', { user_id: null }), devolve ad_storage/ad_user_data/ad_personalization para granted
//   se consent-v1 continuar granted, e nada mais é enviado.
// - Nunca acessa nem envia email; nunca chama clarity('identify'); mascara botão, menu e aviso no Clarity.
//   Falha em silêncio se o Firebase ou o GIS não carregarem.
(function () {
  var d = document;
  var w = window;
  var loc = w.location;
  if (!loc || loc.hostname !== 'guifav.dev') return;
  var initialPath = String(loc.pathname || '/');
  if (initialPath === '/admin' || initialPath.indexOf('/admin/') === 0) return;

  var FIREBASE_CONFIG = {
    apiKey: 'AIzaSyC16_hENAEO4CH6hHDcbFA9St8P1oLdYgI',
    authDomain: 'guifav.dev',
    projectId: 'mindapps',
    messagingSenderId: '486000748530',
    appId: '1:486000748530:web:bcd94317a7e48bf0c4ad6b'
  };
  var TENANT_ID = 'guifav-dev-g6dp4';
  var GOOGLE_CLIENT_ID = '486000748530-fj4un9nf6q94dcfjkttbrb87v026eboi.apps.googleusercontent.com';
  var FIREBASE_APP_URL = 'https://www.gstatic.com/firebasejs/12.12.1/firebase-app.js';
  var FIREBASE_AUTH_URL = 'https://www.gstatic.com/firebasejs/12.12.1/firebase-auth.js';
  var GIS_URL = 'https://accounts.google.com/gsi/client';
  var IDENTITY_URL = 'https://insights.guifav.dev/identidade';
  var IDB_NAME = 'firebaseLocalStorageDb';
  var IDB_STORE = 'firebaseLocalStorage';
  var IDB_USER_KEY = 'firebase:authUser:' + FIREBASE_CONFIG.apiKey + ':[DEFAULT]';

  var CONSENT_KEY = 'consent-v1';
  var LINK_KEY = 'heating-link-v1';
  var UID_KEY = 'heating-uid-v1';
  var IDENTIFIED_PREFIX = 'guifav_auth_identified:';
  var IDENTITY_SENT_PREFIX = 'guifav_identidade_sent:';
  var ONE_TAP_SUPPRESSED_KEY = 'guifav_one_tap_suppressed';
  var TTL_MS = 365 * 24 * 3600 * 1000;
  var UID_RE = /^[0-9A-Za-z]{1,128}$/;

  var NO_ONE_TAP_PREFIXES = ['/privacidade', '/termos', '/prioridade', '/404'];
  var NOTICE_TEXT = 'Ao entrar, sua atividade neste site fica ligada ao seu contato: páginas vistas, tempo de leitura, rolagem e cliques em Agende e Contato. Uso para saber quem abordar primeiro. Você pode desligar quando quiser no menu da conta.';

  var fbAuth = null;
  var authInstance = null;
  var firebasePromise = null;
  var currentUser = null;
  var authResolved = false;
  var activeUid = null;
  var headSentUid = false;
  var identityInFlightUid = null;
  var oneTapStarted = false;
  var menuOpen = false;
  var menuFocusIdx = -1;
  var popupBusy = false;

  var rootEl = null;
  var triggerBtn = null;
  var menuEl = null;
  var toggleBtn = null;
  var toggleStateEl = null;
  var signOutBtn = null;
  var noticeEl = null;

  function logWarn(msg) {
    try {
      if (w.console && typeof w.console.warn === 'function') w.console.warn('guifav-auth: ' + msg);
    } catch (e) { /* ignora */ }
  }

  function sendGtag() {
    try {
      if (typeof w.gtag === 'function') w.gtag.apply(w, arguments);
    } catch (e) { /* ignora */ }
  }

  function validAge(t) {
    if (typeof t !== 'number' || !isFinite(t)) return false;
    var age = Date.now() - t;
    return age >= 0 && age < TTL_MS;
  }

  function readConsent() {
    try {
      var c = JSON.parse(w.localStorage.getItem(CONSENT_KEY) || 'null');
      if (c && (c.v === 'granted' || c.v === 'denied') && validAge(c.t)) return c.v;
    } catch (e) { /* sem storage ou valor quebrado */ }
    return null;
  }

  function readHeatingLink(uid) {
    if (!uid) return null;
    try {
      var c = JSON.parse(w.localStorage.getItem(LINK_KEY) || 'null');
      if (c && (c.v === 'granted' || c.v === 'denied') && c.uid === uid && validAge(c.t)) return c.v;
    } catch (e) { /* sem storage ou valor quebrado */ }
    return null;
  }

  function writeHeatingLink(uid, v) {
    if (!uid || (v !== 'granted' && v !== 'denied')) return;
    try {
      w.localStorage.setItem(LINK_KEY, JSON.stringify({ v: v, t: Date.now(), uid: uid }));
    } catch (e) { /* sem storage */ }
  }

  function readHeatingUid() {
    try {
      var u = JSON.parse(w.localStorage.getItem(UID_KEY) || 'null');
      if (u && typeof u.uid === 'string' && UID_RE.test(u.uid) && validAge(u.t)) return u.uid;
    } catch (e) { /* sem storage ou valor quebrado */ }
    return null;
  }

  function hasRawHeatingUid() {
    try { return w.localStorage.getItem(UID_KEY) !== null; } catch (e) { return false; }
  }

  function writeHeatingUid(uid) {
    if (!uid || !UID_RE.test(uid)) return;
    if (readHeatingUid() === uid) return;
    try {
      w.localStorage.setItem(UID_KEY, JSON.stringify({ uid: uid, t: Date.now() }));
    } catch (e) { /* sem storage */ }
  }

  function removeHeatingUid() {
    if (!hasRawHeatingUid()) return;
    try {
      w.localStorage.removeItem(UID_KEY);
    } catch (e) { /* sem storage */ }
  }

  function sessionGet(key) {
    try { return w.sessionStorage.getItem(key); } catch (e) { return null; }
  }

  function sessionSet(key, val) {
    try { w.sessionStorage.setItem(key, val); } catch (e) { /* sem storage */ }
  }

  function sessionRemove(key) {
    try { w.sessionStorage.removeItem(key); } catch (e) { /* sem storage */ }
  }

  function isNoOneTapPath(pathname) {
    var p = String(pathname || '/').toLowerCase();
    for (var i = 0; i < NO_ONE_TAP_PREFIXES.length; i++) {
      var pref = NO_ONE_TAP_PREFIXES[i];
      if (p === pref || p === pref + '.html' || p.indexOf(pref + '/') === 0) return true;
    }
    if (typeof d.title === 'string' && d.title.indexOf('Página não encontrada') === 0) return true;
    return false;
  }

  function firstName(user) {
    var name = user && typeof user.displayName === 'string' ? user.displayName.trim() : '';
    if (!name) return 'Conta';
    var part = name.split(/\s+/)[0] || 'Conta';
    return part.slice(0, 24);
  }

  function validTenantUser(user) {
    return Boolean(user && user.tenantId === TENANT_ID && typeof user.uid === 'string' && UID_RE.test(user.uid));
  }

  function clearUserId(removeStored) {
    var wasIdentified = activeUid !== null || headSentUid;
    activeUid = null;
    headSentUid = false;
    if (removeStored !== false) removeHeatingUid();
    sendGtag('set', { user_id: null });
    if (wasIdentified && readConsent() === 'granted') {
      sendGtag('consent', 'update', { ad_storage: 'granted', ad_user_data: 'granted', ad_personalization: 'granted' });
    }
  }

  function canIdentify(uid) {
    return Boolean(
      validTenantUser(currentUser)
      && currentUser.uid === uid
      && readConsent() === 'granted'
      && readHeatingLink(uid) === 'granted'
    );
  }

  function sendIdentity(user) {
    var uid = user.uid;
    if (sessionGet(IDENTITY_SENT_PREFIX + uid) === '1' || identityInFlightUid === uid) return;
    if (typeof w.fetch !== 'function' || typeof user.getIdToken !== 'function') return;
    identityInFlightUid = uid;
    Promise.resolve(user.getIdToken()).then(function (token) {
      if (!token || typeof token !== 'string') return;
      if (!canIdentify(uid)) return;
      return w.fetch(IDENTITY_URL, {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + token }
      }).then(function (res) {
        if (res && res.ok) {
          sessionSet(IDENTITY_SENT_PREFIX + uid, '1');
        } else {
          logWarn('identidade nao registrada');
        }
      });
    }).catch(function () {
      logWarn('falha ao enviar identidade');
    }).finally(function () {
      if (identityInFlightUid === uid) identityInFlightUid = null;
    });
  }

  function syncIdentityState(forceClear) {
    if (validTenantUser(currentUser) && readConsent() === 'granted' && readHeatingLink(currentUser.uid) === 'granted') {
      var uid = currentUser.uid;
      writeHeatingUid(uid);
      sendGtag('consent', 'update', { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' });
      sendGtag('set', 'allow_google_signals', false);
      sendGtag('set', 'allow_ad_personalization_signals', false);
      sendGtag('set', { user_id: uid });
      activeUid = uid;
      headSentUid = false;
      if (sessionGet(IDENTIFIED_PREFIX + uid) !== '1') {
        sendGtag('event', 'auth_identified');
        sessionSet(IDENTIFIED_PREFIX + uid, '1');
      }
      sendIdentity(currentUser);
      return;
    }
    removeHeatingUid();
    if (activeUid !== null || headSentUid || forceClear) {
      clearUserId(false);
    }
  }

  function injectStyles() {
    if (!d.head || typeof d.createElement !== 'function') return;
    if (typeof d.getElementById === 'function' && d.getElementById('guifav-auth-style')) return;
    var style = d.createElement('style');
    style.setAttribute('id', 'guifav-auth-style');
    style.textContent = [
      '.guifav-auth{position:fixed;right:16px;bottom:16px;z-index:900;min-width:168px;display:flex;flex-direction:column-reverse;align-items:flex-end;gap:8px;font-family:inherit}',
      '.guifav-auth[hidden]{display:none}',
      '.guifav-auth-btn{display:inline-flex;align-items:center;justify-content:center;min-width:168px;height:36px;padding:0 14px;border:1px solid var(--border,#e5e5e5);border-radius:999px;background:var(--bg-card,#fff);color:var(--text,#171717);box-shadow:var(--shadow,0 4px 16px rgba(0,0,0,.12));font:600 13px/1 inherit;cursor:pointer;white-space:nowrap}',
      '.guifav-auth-btn:hover{border-color:var(--text-secondary,#525252)}',
      '.guifav-auth-btn:focus-visible,.guifav-auth-item:focus-visible,.guifav-auth-yes:focus-visible,.guifav-auth-no:focus-visible{outline:2px solid var(--hl,#FFBE00);outline-offset:2px}',
      '.guifav-auth-menu{min-width:250px;max-width:calc(100vw - 32px);padding:8px;border:1px solid var(--border,#e5e5e5);border-radius:12px;background:var(--bg-card,#fff);color:var(--text,#171717);box-shadow:var(--shadow,0 12px 32px rgba(0,0,0,.18));display:flex;flex-direction:column;gap:4px}',
      '.guifav-auth-menu[hidden]{display:none}',
      '.guifav-auth-item{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;padding:9px 10px;border:0;border-radius:8px;background:transparent;color:var(--text,#171717);font:500 13px/1.35 inherit;text-align:left;cursor:pointer}',
      '.guifav-auth-item:hover{background:var(--tag-bg,#f5f5f5)}',
      '.guifav-auth-state{font-size:12px;font-weight:700;color:var(--text-secondary,#525252);flex-shrink:0}',
      '.guifav-auth-notice{position:fixed;left:16px;right:16px;bottom:64px;z-index:950;max-width:560px;margin:0 auto;padding:16px 18px;border:1px solid var(--border,#e5e5e5);border-radius:12px;background:var(--bg-card,#fff);color:var(--text,#171717);box-shadow:var(--shadow,0 12px 32px rgba(0,0,0,.18));font-size:14px;line-height:1.55}',
      '.guifav-auth-notice[hidden]{display:none}',
      '.guifav-auth-notice p{margin:0 0 12px}',
      '.guifav-auth-actions{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap}',
      '.guifav-auth-yes,.guifav-auth-no{font:600 14px/1 inherit;padding:8px 16px;border-radius:8px;cursor:pointer;border:1px solid transparent}',
      '.guifav-auth-yes{background:#2563eb;color:#fff}',
      '.guifav-auth-yes:hover{background:#1d4ed8}',
      '.guifav-auth-no{background:var(--tag-bg,#e5e5e5);color:var(--text,#171717);border-color:var(--border,#d4d4d4)}',
      '@media(max-width:700px){.guifav-auth{right:12px;bottom:12px}.guifav-auth-notice{left:12px;right:12px;bottom:58px}}'
    ].join('');
    d.head.appendChild(style);
  }

  function el(tag, attrs, parent) {
    var node = d.createElement(tag);
    for (var k in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) node.setAttribute(k, attrs[k]);
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  function getMenuItems() {
    var items = [];
    if (toggleBtn) items.push(toggleBtn);
    if (signOutBtn) items.push(signOutBtn);
    return items;
  }

  function focusMenuItem(idx) {
    var items = getMenuItems();
    if (!items.length) return;
    var next = ((idx % items.length) + items.length) % items.length;
    menuFocusIdx = next;
    if (typeof items[next].focus === 'function') items[next].focus();
  }

  function setMenuOpen(open) {
    menuOpen = Boolean(open);
    if (menuEl) menuEl.hidden = !menuOpen;
    if (triggerBtn) triggerBtn.setAttribute('aria-expanded', menuOpen ? 'true' : 'false');
    if (menuOpen) {
      focusMenuItem(0);
    } else {
      menuFocusIdx = -1;
    }
  }

  function updateToggleLabel() {
    if (!toggleBtn || !toggleStateEl || !currentUser) return;
    var on = readHeatingLink(currentUser.uid) === 'granted';
    toggleBtn.setAttribute('aria-checked', on ? 'true' : 'false');
    toggleStateEl.textContent = on ? 'Ligado' : 'Desligado';
  }

  function hideNotice() {
    if (noticeEl) noticeEl.hidden = true;
  }

  function showNotice() {
    if (!d.body) return;
    if (!noticeEl) {
      noticeEl = el('div', {
        'class': 'guifav-auth-notice',
        role: 'dialog',
        'aria-label': 'Aviso de atividade da conta',
        'aria-describedby': 'guifav-auth-notice-desc',
        'data-clarity-mask': 'true'
      });
      var p = el('p', { id: 'guifav-auth-notice-desc' }, noticeEl);
      p.textContent = NOTICE_TEXT;
      var actions = el('div', { 'class': 'guifav-auth-actions' }, noticeEl);
      var yesBtn = el('button', { type: 'button', 'class': 'guifav-auth-yes' }, actions);
      yesBtn.textContent = 'Ligar';
      var noBtn = el('button', { type: 'button', 'class': 'guifav-auth-no' }, actions);
      noBtn.textContent = 'Agora não';

      yesBtn.addEventListener('click', function () {
        if (!currentUser) return;
        writeHeatingLink(currentUser.uid, 'granted');
        hideNotice();
        updateToggleLabel();
        syncIdentityState(false);
      });

      noBtn.addEventListener('click', function () {
        if (!currentUser) return;
        writeHeatingLink(currentUser.uid, 'denied');
        hideNotice();
        updateToggleLabel();
        syncIdentityState(true);
      });

      d.body.appendChild(noticeEl);
    }
    noticeEl.hidden = false;
  }

  function syncNotice() {
    if (validTenantUser(currentUser) && readConsent() === 'granted' && readHeatingLink(currentUser.uid) === null) {
      showNotice();
    } else {
      hideNotice();
    }
  }

  function isExpectedAuthError(err) {
    var code = err && typeof err.code === 'string' ? err.code : '';
    return code === 'auth/popup-closed-by-user' || code === 'auth/cancelled-popup-request';
  }

  function signInPopup() {
    if (popupBusy) return;
    if (!authInstance || !fbAuth) {
      popupBusy = true;
      loadFirebase().then(function (ok) {
        popupBusy = false;
        if (ok && !currentUser) signInPopup();
      });
      return;
    }
    popupBusy = true;
    try {
      authInstance.tenantId = TENANT_ID;
      var provider = new fbAuth.GoogleAuthProvider();
      if (typeof provider.setCustomParameters === 'function') {
        provider.setCustomParameters({ prompt: 'select_account' });
      }
      Promise.resolve(fbAuth.signInWithPopup(authInstance, provider)).then(function () {
        sessionRemove(ONE_TAP_SUPPRESSED_KEY);
      }).catch(function (err) {
        if (!isExpectedAuthError(err)) logWarn('falha no login');
      }).finally(function () {
        popupBusy = false;
      });
    } catch (e) {
      popupBusy = false;
      logWarn('falha no login');
    }
  }

  function disableOneTapAutoSelect() {
    try {
      var id = w.google && w.google.accounts && w.google.accounts.id;
      if (id && typeof id.disableAutoSelect === 'function') id.disableAutoSelect();
    } catch (e) { /* ignora */ }
  }

  function renderWidget() {
    if (!d.body) return;
    if (!rootEl) {
      injectStyles();
      rootEl = el('div', { 'class': 'guifav-auth', 'data-clarity-mask': 'true' });

      // Botão antes do menu no DOM; flex-direction: column-reverse coloca o menu visualmente acima.
      triggerBtn = el('button', { type: 'button', 'class': 'guifav-auth-btn' }, rootEl);
      triggerBtn.addEventListener('click', function () {
        if (!currentUser) {
          signInPopup();
          return;
        }
        updateToggleLabel();
        setMenuOpen(!menuOpen);
      });

      menuEl = el('div', {
        'class': 'guifav-auth-menu',
        id: 'guifav-auth-menu',
        role: 'menu',
        'data-clarity-mask': 'true'
      }, rootEl);
      menuEl.hidden = true;

      toggleBtn = el('button', {
        type: 'button',
        'class': 'guifav-auth-item',
        role: 'menuitemcheckbox',
        'aria-checked': 'false'
      }, menuEl);
      var toggleLabel = el('span', {}, toggleBtn);
      toggleLabel.textContent = 'Ligar minha atividade ao meu contato';
      toggleStateEl = el('span', { 'class': 'guifav-auth-state' }, toggleBtn);
      toggleStateEl.textContent = 'Desligado';

      toggleBtn.addEventListener('click', function () {
        if (!currentUser) return;
        var next = readHeatingLink(currentUser.uid) === 'granted' ? 'denied' : 'granted';
        writeHeatingLink(currentUser.uid, next);
        hideNotice();
        updateToggleLabel();
        syncIdentityState(next === 'denied');
      });

      signOutBtn = el('button', { type: 'button', 'class': 'guifav-auth-item', role: 'menuitem' }, menuEl);
      signOutBtn.textContent = 'Sair';
      signOutBtn.addEventListener('click', function () {
        sessionSet(ONE_TAP_SUPPRESSED_KEY, '1');
        disableOneTapAutoSelect();
        setMenuOpen(false);
        hideNotice();
        clearUserId(true);
        if (authInstance && fbAuth && typeof fbAuth.signOut === 'function') {
          Promise.resolve(fbAuth.signOut(authInstance)).catch(function () {
            logWarn('falha ao sair');
          });
        }
      });

      d.body.appendChild(rootEl);

      d.addEventListener('click', function (e) {
        if (!menuOpen || !rootEl) return;
        var t = e && e.target;
        if (t && typeof rootEl.contains === 'function' && rootEl.contains(t)) return;
        setMenuOpen(false);
      });

      d.addEventListener('keydown', function (e) {
        if (!e || typeof e.key !== 'string') return;
        if (menuOpen) {
          var items = getMenuItems();
          var active = (d && d.activeElement) || (e && e.target);
          var idx = items.indexOf(active);
          if (idx < 0) idx = menuFocusIdx;
          if (e.key === 'Escape') {
            if (typeof e.preventDefault === 'function') e.preventDefault();
            setMenuOpen(false);
            if (triggerBtn && typeof triggerBtn.focus === 'function') triggerBtn.focus();
          } else if (e.key === 'ArrowDown') {
            if (typeof e.preventDefault === 'function') e.preventDefault();
            focusMenuItem(idx < 0 ? 0 : idx + 1);
          } else if (e.key === 'ArrowUp') {
            if (typeof e.preventDefault === 'function') e.preventDefault();
            focusMenuItem(idx < 0 ? items.length - 1 : idx - 1);
          } else if (e.key === 'Home') {
            if (typeof e.preventDefault === 'function') e.preventDefault();
            focusMenuItem(0);
          } else if (e.key === 'End') {
            if (typeof e.preventDefault === 'function') e.preventDefault();
            focusMenuItem(items.length - 1);
          }
          return;
        }
        if (e.key === 'Escape' && noticeEl && !noticeEl.hidden) {
          if (typeof e.preventDefault === 'function') e.preventDefault();
          hideNotice();
          if (triggerBtn && typeof triggerBtn.focus === 'function') triggerBtn.focus();
        }
      });
    }

    if (currentUser) {
      var name = firstName(currentUser);
      triggerBtn.textContent = name;
      triggerBtn.setAttribute('aria-label', name + ', menu da conta');
      triggerBtn.setAttribute('aria-haspopup', 'menu');
      triggerBtn.setAttribute('aria-controls', 'guifav-auth-menu');
      triggerBtn.setAttribute('aria-expanded', menuOpen ? 'true' : 'false');
      updateToggleLabel();
    } else {
      setMenuOpen(false);
      triggerBtn.textContent = 'Entrar com Google';
      triggerBtn.setAttribute('aria-label', 'Entrar com Google');
      triggerBtn.removeAttribute('aria-haspopup');
      triggerBtn.removeAttribute('aria-controls');
      triggerBtn.removeAttribute('aria-expanded');
    }
  }

  function cancelOneTap() {
    try {
      var id = w.google && w.google.accounts && w.google.accounts.id;
      if (id && typeof id.cancel === 'function') id.cancel();
    } catch (e) { /* ignora */ }
  }

  function initOneTap() {
    if (currentUser || !authInstance || !fbAuth) return;
    var id = w.google && w.google.accounts && w.google.accounts.id;
    if (!id || typeof id.initialize !== 'function' || typeof id.prompt !== 'function') return;

    id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      itp_support: true,
      cancel_on_tap_outside: false,
      callback: function (response) {
        if (!response || !response.credential) return;
        try {
          sessionRemove(ONE_TAP_SUPPRESSED_KEY);
          authInstance.tenantId = TENANT_ID;
          var cred = fbAuth.GoogleAuthProvider.credential(response.credential);
          Promise.resolve(fbAuth.signInWithCredential(authInstance, cred)).catch(function (err) {
            if (!isExpectedAuthError(err)) logWarn('falha no One Tap');
          });
        } catch (e) {
          logWarn('falha no One Tap');
        }
      }
    });

    id.prompt(function (notification) {
      try {
        if (!notification || typeof notification.isSkippedMoment !== 'function' || !notification.isSkippedMoment()) return;
        var reason = typeof notification.getSkippedReason === 'function' ? notification.getSkippedReason() : '';
        if (reason === 'user_cancel' || reason === 'tap_outside') {
          sessionSet(ONE_TAP_SUPPRESSED_KEY, '1');
        }
      } catch (e) { /* ignora */ }
    });
  }

  function maybeStartOneTap() {
    if (!authResolved || currentUser || oneTapStarted) return;
    if (sessionGet(ONE_TAP_SUPPRESSED_KEY) === '1') return;
    if (isNoOneTapPath(loc.pathname)) return;
    if (readConsent() === null) return;

    oneTapStarted = true;
    if (w.google && w.google.accounts && w.google.accounts.id) {
      initOneTap();
      return;
    }
    var existing = typeof d.querySelector === 'function'
      ? d.querySelector('script[src="' + GIS_URL + '"]')
      : null;
    if (existing) {
      existing.addEventListener('load', initOneTap, { once: true });
      return;
    }
    var parent = d.head || d.body || d.documentElement;
    if (!parent || typeof d.createElement !== 'function') return;
    var s = d.createElement('script');
    s.setAttribute('src', GIS_URL);
    s.async = true;
    s.addEventListener('load', initOneTap, { once: true });
    s.addEventListener('error', function () {
      logWarn('GIS indisponivel');
    }, { once: true });
    parent.appendChild(s);
  }

  function hasSavedFirebaseSession() {
    return new Promise(function (resolve) {
      try {
        var idb = w.indexedDB;
        if (!idb || typeof idb.open !== 'function') {
          resolve(false);
          return;
        }
        var done = false;
        var finish = function (val, db) {
          if (done) return;
          done = true;
          try { if (db && typeof db.close === 'function') db.close(); } catch (e) { /* ignora */ }
          resolve(Boolean(val));
        };
        var req = idb.open(IDB_NAME);
        if (!req) {
          finish(false);
          return;
        }
        req.onupgradeneeded = function () {
          try {
            if (req.transaction && typeof req.transaction.abort === 'function') req.transaction.abort();
          } catch (e) { /* ignora */ }
          finish(false, req.result);
        };
        req.onerror = function () { finish(false); };
        req.onblocked = function () { finish(false); };
        req.onsuccess = function () {
          var db = req.result;
          try {
            var names = db && db.objectStoreNames;
            var hasStore = Boolean(
              names && (
                (typeof names.contains === 'function' && names.contains(IDB_STORE))
                || (typeof names.indexOf === 'function' && names.indexOf(IDB_STORE) >= 0)
              )
            );
            if (!hasStore) {
              finish(false, db);
              return;
            }
            var tx = db.transaction(IDB_STORE, 'readonly');
            tx.onerror = function () { finish(false, db); };
            var store = tx.objectStore(IDB_STORE);
            var getReq = store.get(IDB_USER_KEY);
            getReq.onerror = function () { finish(false, db); };
            getReq.onsuccess = function () {
              var res = getReq.result;
              var has = Boolean(res && (!Object.prototype.hasOwnProperty.call(res, 'value') || res.value));
              finish(has, db);
            };
          } catch (e) {
            finish(false, db);
          }
        };
      } catch (e) {
        resolve(false);
      }
    });
  }

  function loadFirebase() {
    if (authInstance && fbAuth) return Promise.resolve(true);
    if (firebasePromise) return firebasePromise;
    firebasePromise = Promise.all([import(FIREBASE_APP_URL), import(FIREBASE_AUTH_URL)]).then(function (mods) {
      var fbApp = mods[0];
      fbAuth = mods[1];
      var apps = typeof fbApp.getApps === 'function' ? fbApp.getApps() : [];
      var app = apps.length ? fbApp.getApp() : fbApp.initializeApp(FIREBASE_CONFIG);
      authInstance = fbAuth.getAuth(app);
      authInstance.tenantId = TENANT_ID;

      fbAuth.onAuthStateChanged(authInstance, function (user) {
        authResolved = true;
        if (validTenantUser(user)) {
          currentUser = user;
          cancelOneTap();
          renderWidget();
          syncNotice();
          syncIdentityState(false);
        } else {
          var hadIdentity = currentUser !== null || activeUid !== null || headSentUid;
          currentUser = null;
          hideNotice();
          renderWidget();
          syncIdentityState(hadIdentity);
          maybeStartOneTap();
        }
      });
      return true;
    }).catch(function () {
      firebasePromise = null;
      logWarn('Firebase indisponivel');
      return false;
    });
    return firebasePromise;
  }

  if (typeof w.addEventListener === 'function') {
    w.addEventListener('guifav-consent', function (e) {
      var detail = e && e.detail;
      loadFirebase();
      syncNotice();
      if (detail === 'granted') {
        syncIdentityState(false);
        maybeStartOneTap();
      } else {
        syncIdentityState(true);
        maybeStartOneTap();
      }
    });

    w.addEventListener('storage', function (e) {
      var k = e ? e.key : null;
      if (k !== null && k !== CONSENT_KEY && k !== LINK_KEY && k !== UID_KEY) return;
      if (readConsent() !== null) loadFirebase();
      updateToggleLabel();
      syncNotice();
      if (k === UID_KEY) {
        if (!validTenantUser(currentUser) || readHeatingUid() !== currentUser.uid) {
          if (activeUid !== null || headSentUid) clearUserId(false);
        } else {
          syncIdentityState(false);
        }
        return;
      }
      if (validTenantUser(currentUser) && readConsent() === 'granted' && readHeatingLink(currentUser.uid) === 'granted') {
        syncIdentityState(false);
      } else {
        syncIdentityState(true);
      }
      if (k === CONSENT_KEY || k === null) maybeStartOneTap();
    });
  }

  headSentUid = readConsent() === 'granted' && readHeatingUid() !== null;
  if (readConsent() !== 'granted') removeHeatingUid();
  renderWidget();

  if (readConsent() !== null) {
    loadFirebase();
  } else {
    hasSavedFirebaseSession().then(function (has) {
      if (has) loadFirebase();
    });
  }
})();
