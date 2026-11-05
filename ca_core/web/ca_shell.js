/* CA Tools (ca_core) – Infobox, Spendenhinweis, Diagnose und Farbschema (LazyNest / LazySetup).
   Nutzung: CACore.init({ send, logo, infoButton }); CACore.setInfo(session.info); CACore.setTheme('dark');
   Python: 'openLogs' -> {ok, path}, 'diag' -> {text, file}. */
(function () {
  'use strict';
  var C = { cfg: null, info: null, theme: 'light' };
  var PAYPAL = 'chris.n.adam@gmx.de';
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, parent, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === 'className') e.className = attrs[k]; else e.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  function t(key, fb) { return (window.CA_I18n && CA_I18n.t(key)) || fb; }
  function fmtDate(s) { if (!s) return ''; var p = String(s).slice(0, 10).split('-'); return p.length === 3 ? p[2] + '.' + p[1] + '.' + p[0] : s; }
  function build() {
    if ($('caInfo')) return;
    var m = el('div', { id: 'caInfo', className: 'ca-modal', hidden: '' }, document.body);
    var d = el('div', { className: 'ca-dlg', role: 'dialog' }, m);
    var x = el('button', { className: 'ca-close', title: t('close', 'Schließen') }, d, '✕');
    el('img', { id: 'caInfoLogo', className: 'ca-logo', alt: '' }, d);
    var v = el('div', { className: 'ca-ver' }, d); el('b', { id: 'caInfoName' }, v); el('span', { id: 'caInfoVer', className: 'ca-mute' }, v);
    el('p', { id: 'caSlogan', className: 'ca-slogan' }, d);
    el('p', { id: 'caAddr', className: 'ca-addr' }, d);
    var don = el('div', { className: 'ca-lic ca-donate', id: 'caDonate' }, d);
    el('b', {}, don, t('donate_title', 'Spenden / Support'));
    el('p', { id: 'caDonateText', className: 'ca-donate-text' }, don);
    var row = el('div', { className: 'ca-row' }, don);
    el('span', { className: 'ca-mute' }, row, 'PayPal: ');
    el('a', { id: 'caDonateMail', href: 'mailto:' + PAYPAL }, row, PAYPAL);
    var r2 = el('div', { className: 'ca-row' }, d);
    el('button', { id: 'caLogs' }, r2, t('open_logs', 'Log-Ordner öffnen'));
    el('button', { id: 'caDiag' }, r2, t('diag_copy', 'Diagnose kopieren'));
    el('div', { id: 'caDiagMsg', className: 'ca-mute' }, d);
    el('textarea', { id: 'caDiagText', rows: '5', readonly: '', hidden: '' }, d);
    x.onclick = close;
    m.onclick = function (ev) { if (ev.target === m) close(); };
    document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && !m.hidden) close(); });
    $('caLogs').onclick = function () { send('openLogs', {}, function (r) { if (r && r.path) $('caDiagMsg').textContent = (r.ok ? t('opened', 'Geöffnet: ') + ' ' : '') + r.path; }); };
    $('caDiag').onclick = function () {
      send('diag', {}, function (r) {
        if (!r || !r.text) { $('caDiagMsg').textContent = 'Diagnose nicht verfügbar.'; return; }
        var ok = copyText(r.text);
        $('caDiagMsg').textContent = (ok ? 'In die Zwischenablage kopiert' : 'Bitte Text unten markieren und kopieren') + (r.file ? ' – Datei: ' + r.file : '');
      });
    };
  }
  function send(a, o, cb) { if (C.cfg && C.cfg.send) C.cfg.send(a, o, cb); }
  function copyText(txt) {
    var ok = false, ta = $('caDiagText');
    ta.hidden = false; ta.value = txt; ta.select();
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(txt).then(function () { }, function () { }); ok = true; }
    return ok;
  }
  function renderDonate() {
    if (!$('caDonateText')) return;
    $('caDonateText').textContent = t('donate_body',
      'Dieses Add-in ist kostenlos nutzbar – ohne Einschränkungen. Wenn es dir hilft, freue ich mich über eine Spende zur Pflege und Weiterentwicklung via PayPal an chris.n.adam@gmx.de. Danke für deine Unterstützung!');
    if ($('caDonateMail')) { $('caDonateMail').href = 'mailto:' + PAYPAL; $('caDonateMail').textContent = PAYPAL; }
  }
  function setInfo(info) {
    build();
    C.info = info || {};
    var a = C.info.author || {};
    $('caInfoName').textContent = (C.info.appName || 'CA Tools') + ' ';
    $('caInfoVer').textContent = 'Version ' + (C.info.version || '') + (C.info.buildDate ? ' · Build ' + fmtDate(C.info.buildDate) : '');
    $('caSlogan').textContent = C.info.slogan || '';
    var ad = $('caAddr'); ad.innerHTML = '';
    ad.appendChild(document.createTextNode('© ' + (a.year || '2026') + ' ' + (a.company || ''))); el('br', {}, ad);
    ad.appendChild(document.createTextNode('E-Mail: '));
    el('a', { href: 'mailto:' + (a.mail || '') }, ad, a.mail || '');
    renderDonate();
    setTheme(C.theme);
  }
  function setTheme(th) {
    C.theme = th === 'dark' ? 'dark' : 'light';
    document.body.classList.remove('light', 'dark'); document.body.classList.add(C.theme);
    var src = ((C.cfg && C.cfg.logo) || 'img/logo_') + C.theme + '.svg';
    if ($('logo')) $('logo').src = src;
    if ($('caInfoLogo')) $('caInfoLogo').src = src;
  }
  function openInfo() { build(); renderDonate(); $('caInfo').hidden = false; }
  function close() { if ($('caInfo')) $('caInfo').hidden = true; }
  window.CACore = {
    version: '1.1.0',
    init: function (cfg) { C.cfg = cfg || {}; build(); if (cfg && cfg.infoButton && $(cfg.infoButton)) $(cfg.infoButton).onclick = openInfo; },
    setInfo: setInfo, setTheme: setTheme, openInfo: openInfo, closeInfo: close,
    renderLicense: function () { renderDonate(); }, renderDonate: renderDonate,
    theme: function () { return C.theme; }
  };
}());
