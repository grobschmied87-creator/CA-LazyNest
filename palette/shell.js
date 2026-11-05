/* CA LazyNest 2.0.5 – Fensterrahmen: Register, Theme, Auswahlliste, Einstellungen, Ergebnis, Infobox/Lizenz.
   Die Vorschau/Engine bleibt in app.js (window.ContourNestApp); Nachrichten an Fusion über adsk.fusionSendData. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var APP = function () { return window.ContourNestApp; };
  var TABS = ['Vorschau', 'Nesting', 'Ergebnis'];   // 2.0.1: Auswahl + Platte stehen links in der Vorschau
  var S = { session: null, rows: [], tab: 'Vorschau', job: null, layout: null, applied: null, settings: null, theme: 'light' };
  var GRAIN = [['laengs', 'längs'], ['quer', 'quer'], ['frei', 'frei']];
  var PRESETS = { fast: [20, 40, 8], normal: [60, 150, 12], best: [240, 500, 24] };

  function fmt(x, d) { return Number(x).toLocaleString('de-DE', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); }
  function el(tag, attrs, parent, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === 'className') e.className = attrs[k]; else e.setAttribute(k, attrs[k]); });
    if (text !== undefined) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  function send(action, obj, cb) {
    if (!window.adsk || !window.adsk.fusionSendData) { notice('error', 'Keine Verbindung zu Fusion (adsk.fusionSendData fehlt).'); return; }
    var r = window.adsk.fusionSendData(action, JSON.stringify(obj || {}));
    if (!cb) return;
    var done = function (rep) { var j = null; try { j = JSON.parse(rep); } catch (e) { j = rep; } cb(j); };
    if (r && typeof r.then === 'function') r.then(done); else done(r);
  }
  function notice(level, text, list) {
    var n = $('notice');
    if (!text) { n.hidden = true; return; }
    n.className = 'notice ' + (level || 'info'); n.innerHTML = '';
    el('span', {}, n, text);
    if (list && list.length) { var ul = el('ul', {}, n); list.slice(0, 6).forEach(function (s) { el('li', {}, ul, s.name + ' – ' + s.reason); }); }
    var x = el('button', { className: 'icon close', title: 'Ausblenden' }, n, '✕'); x.onclick = function () { n.hidden = true; };
    n.hidden = false;
  }

  // ---------------------------------------------------------------- Theme / Register
  function setTheme(t) {
    S.theme = t === 'dark' ? 'dark' : 'light';
    window.CACore.setTheme(S.theme);
  }
  function showTab(name, persist) {
    if (name === 'Auswahl' || name === 'Platte') {          // alte Registernamen (Prefs/Python) -> linke Spalte
      var box = $(name === 'Auswahl' ? 'boxSel' : 'boxPlate'); if (box) box.open = true;
      $('side').classList.remove('collapsed');
      name = 'Vorschau';
    }
    if (TABS.indexOf(name) < 0) name = 'Vorschau';
    S.tab = name;
    Array.prototype.forEach.call(document.querySelectorAll('#tabs .tab'), function (b) { b.classList.toggle('on', b.getAttribute('data-tab') === name); });
    Array.prototype.forEach.call(document.querySelectorAll('.pane'), function (p) { p.hidden = p.getAttribute('data-pane') !== name; });
    if (name === 'Vorschau' && APP()) { APP().redraw(); }
    if (name === 'Ergebnis') renderResult();
    if (persist) send('pref', { lastTab: name });
  }

  // ---------------------------------------------------------------- Einstellungen
  var NUM = { sMax: 'max_sheets', sGap: 'gap', sVacA: 'vac_area_cm2', sTol: 'tol', sTime: 'time_limit', sGens: 'generations',
              sPop: 'population', sCamProg: 'cam_prog' };
  var CHK = { sConv: 'convert', sBody: 'stock_body', sReplace: 'replace', sCam: 'cam' };
  var SEL = { sFix: 'fix', sCamOrigin: 'cam_origin', sCamStock: 'cam_stock' };
  function fillOptions(sel, list, cur) {
    sel.innerHTML = '';
    (list || []).forEach(function (o) { var e = el('option', { value: o[0] }, sel, o[1]); if (String(o[0]) === String(cur)) e.selected = true; });
  }
  function fillSettings(st, opt) {
    if (!st) return;
    S.settings = st;
    Object.keys(NUM).forEach(function (id) { if (st[NUM[id]] !== undefined) $(id).value = st[NUM[id]]; });
    Object.keys(CHK).forEach(function (id) { $(id).checked = !!st[CHK[id]]; });
    if (opt) {
      fillOptions($('sFix'), opt.fixModes, st.fix);
      fillOptions($('sCamOrigin'), opt.camOrigins, st.cam_origin);
      fillOptions($('sCamStock'), opt.camStocks, st.cam_stock);
    }
    markPreset();
  }
  function num(id) { var v = parseFloat($(id).value); return isFinite(v) ? v : undefined; }
  function collectSettings() {
    var a = APP(); if (a) a.readQuick();
    var q = (a && a.quickSettings()) || {};
    var st = {
      length: num('qL'), width: num('qB'), thickness: num('qT'), material: $('qMatTxt').value.trim(),
      grain: $('qG').value, frame: num('qF'), spacing: num('qS'), rot_step: parseFloat($('qR').value),
      vac_weight: parseInt($('qV').value, 10), feat_view: q.featView || 'top'
    };
    Object.keys(NUM).forEach(function (id) { var v = num(id); if (v !== undefined) st[NUM[id]] = v; });
    Object.keys(CHK).forEach(function (id) { st[CHK[id]] = $(id).checked; });
    Object.keys(SEL).forEach(function (id) { if ($(id).value) st[SEL[id]] = $(id).value; });
    Object.keys(st).forEach(function (k) { if (st[k] === undefined || (typeof st[k] === 'number' && !isFinite(st[k]))) delete st[k]; });
    return st;
  }
  function applyOpts() {
    var st = collectSettings();
    return { stock_body: st.stock_body, replace: st.replace, fix: st.fix, cam: st.cam, cam_origin: st.cam_origin,
             cam_stock: st.cam_stock, cam_prog: st.cam_prog };
  }
  function markPreset() {
    var t = num('sTime'), g = num('sGens'), p = num('sPop');
    Array.prototype.forEach.call(document.querySelectorAll('#qPreset button'), function (b) {
      var v = PRESETS[b.getAttribute('data-q')]; b.classList.toggle('on', v[0] === t && v[1] === g && v[2] === p);
    });
  }

  // ---------------------------------------------------------------- Auswahlliste
  function rowSettings() {
    var out = {};
    S.rows.forEach(function (r) { out[r.key] = { qty: r.qty, mode: r.mode, enabled: r.enabled }; });
    return out;
  }
  var rowsTimer = null;
  function refreshRows() {          // Hinweise neu berechnen (Dicke/Material/Passt nicht) – entprellt
    if (rowsTimer) clearTimeout(rowsTimer);
    rowsTimer = setTimeout(function () {
      if (!S.rows.length) return;
      send('rows', { settings: collectSettings(), parts: rowSettings() }, function (r) { if (r && r.rows) renderRows(r.rows); });
    }, 400);
  }
  function renderRows(rows) {
    S.rows = rows || [];
    var tb = $('selBody'); tb.innerHTML = '';
    $('selEmpty').hidden = S.rows.length > 0; $('selTable').hidden = !S.rows.length;
    var linked = [], nPcs = 0, nTypes = 0, area = 0, bad = 0, kopien = 0;
    S.rows.forEach(function (r) {
      var tr = el('tr', { className: (r.usable ? '' : 'bad') + (r.enabled ? '' : ' off') }, tb);
      var td = el('td', { className: 'c-en' }, tr);
      var cb = el('input', { type: 'checkbox' }, td); cb.checked = r.enabled;
      cb.onchange = function () { r.enabled = cb.checked; tr.classList.toggle('off', !r.enabled); summary(); dirty(); };
      td = el('td', { className: 'c-name' }, tr);
      var nm = el('div', { className: 'nm', title: r.name }, td, r.name);
      if (r.kind === 'kopie') el('span', { className: 'tag', title: 'Baugruppenmodus: wird als Körper-Kopie genestet' }, nm, 'Kopie');
      if (r.kind === 'projekt') el('span', { className: 'tag', title: 'aus dem gespeicherten Projekt' }, nm, 'Projekt');
      var meta = [];
      if (r.dims) meta.push(fmt(r.dims[0], 0) + ' × ' + fmt(r.dims[1], 0) + ' mm');
      if (r.thickness) meta.push('D ' + fmt(r.thickness, 1));
      if (r.areaCm2) meta.push(fmt(r.areaCm2, 0) + ' cm²');
      meta.push('gewählt ' + r.count + (r.projectQty ? ', Projekt ' + r.projectQty : ''));
      el('div', { className: 'meta muted' }, td, meta.join(' · '));
      (r.warnings || []).forEach(function (w) { el('div', { className: w.charAt(0) === '✖' ? 'w err' : 'w' }, td, w); });
      td = el('td', { className: 'c-g' }, tr);
      var sg = el('select', {}, td); fillOptions(sg, GRAIN, r.mode);
      sg.onchange = function () { r.mode = sg.value; refreshRows(); dirty(); };
      td = el('td', { className: 'c-q' }, tr);
      var q = el('input', { type: 'number', min: 0, max: 9999, step: 1 }, td); q.value = r.qty;
      q.onchange = function () { var v = parseInt(q.value, 10); r.qty = isFinite(v) && v >= 0 ? v : 0; q.value = r.qty; summary(); dirty(); };
      td = el('td', { className: 'c-x' }, tr);
      var x = el('button', { className: 'icon', title: 'Aus der Liste entfernen' }, td, '✕');
      x.onclick = function () { send('removePart', { key: r.key }, function (rep) { if (rep && rep.rows) renderRows(rep.rows); }); };
      if (r.linked && r.kind !== 'kopie') linked.push(r.linked);
      if (r.kind === 'kopie') kopien++;
    });
    function summary() {
      nPcs = 0; nTypes = 0; area = 0; bad = 0;
      S.rows.forEach(function (r) {
        if (!r.usable) { bad++; return; }
        if (r.enabled && r.qty > 0) { nTypes++; nPcs += r.qty; area += r.areaCm2 * r.qty; }
      });
      var st = S.settings || {}, sa = (num('qL') || st.length || 1) * (num('qB') || st.width || 1) / 100;
      $('selSum').textContent = nTypes + ' Teiletyp(en), ' + nPcs + ' Stück, ' + fmt(area / 1e4, 2) + ' m² ≈ ' +
        fmt(area / sa, 2) + ' Rohteil(e) bei 100 %' + (bad ? ' · ' + bad + ' nicht verwendbar' : '');
      $('btnNest').disabled = !nPcs;
    }
    summary();
    $('selCount').textContent = S.rows.length ? '(' + S.rows.length + ')' : '';
    var lh = $('linkHint');
    if (linked.length) {
      lh.textContent = '⚠ ' + linked.length + ' Teil(e) liegen in verknüpften Komponenten (' + linked.slice(0, 3).join(', ') +
        (linked.length > 3 ? ' …' : '') + '). Fusion kann sie nicht direkt einfügen: „Baugruppenmodus“ aktivieren und die Teile neu wählen ' +
        '(werden als Kopien genestet) oder die Verknüpfung lösen.';
      lh.hidden = false;
    } else lh.hidden = true;
    $('btnRefreshCopies').hidden = !(kopien || (S.session && S.session.hasCopies));
  }

  function dirty() { if (S.job && !S.job.draft) $('btnNest').classList.add('pulse'); }
  function clean() { $('btnNest').classList.remove('pulse'); }
  function plateSum() {
    var L = num('qL'), B = num('qB'), T = num('qT');
    $('plateSum').textContent = (L && B) ? fmt(L, 0) + ' × ' + fmt(B, 0) + (T ? ' × ' + fmt(T, 1) : '') + ' mm' : '';
  }

  // ---------------------------------------------------------------- Ergebnis
  function renderResult() {
    var lay = S.layout, job = S.job, ap = S.applied;
    var has = !!(lay && lay.sheets && job && !job.draft);
    $('resEmpty').hidden = has || !!ap; $('resCards').hidden = !has;
    var box = $('resSheets'); box.innerHTML = '';
    var skip = [];
    if (job && job.skipped) job.skipped.forEach(function (s) { skip.push({ name: s.name, reason: 'beim Nesten: ' + s.reason }); });
    if (has) {
      var A = APP(), sheetArea = 0, partArea = 0, nUsed = 0, nPl = 0;
      var t = el('table', { className: 'grid small' }, box);
      var h = el('tr', {}, el('thead', {}, t)); ['Rohteil', 'Maß mm', 'Teile', 'Ausnutzung'].forEach(function (c) { el('th', {}, h, c); });
      var tb = el('tbody', {}, t);
      lay.sheets.forEach(function (Sh) {
        if (!Sh.parts.length) return;
        var gm = A.geomOf(Sh), a = gm.W * gm.H, pa = 0;
        Sh.parts.forEach(function (p) { pa += p.area || 0; });
        sheetArea += a; partArea += pa; nUsed++; nPl += Sh.parts.length;
        var tr = el('tr', {}, tb);
        el('td', {}, tr, A.labelOf(Sh)); el('td', {}, tr, fmt(gm.W, 0) + ' × ' + fmt(gm.H, 0));
        el('td', {}, tr, String(Sh.parts.length)); el('td', {}, tr, fmt(pa / a * 100, 1) + ' %');
      });
      $('rUtil').textContent = sheetArea ? fmt(partArea / sheetArea * 100, 1) + ' %' : '–';
      $('rWaste').textContent = fmt((sheetArea - partArea) / 1e6, 2) + ' m²';
      $('rSheets').textContent = String(nUsed);
      var nUn = (lay.unplaced || []).reduce(function (s, u) { return s + u.count; }, 0);
      var pk = {};     // 2.0.5: geparkte Teile zählen als „nicht platziert“
      lay.sheets.forEach(function (Sh) { (Sh.parked || []).forEach(function (p) { pk[p.name] = (pk[p.name] || 0) + 1; nUn++; }); });
      $('rParts').textContent = nPl + (nUn ? ' / ' + (nPl + nUn) : '');
      (lay.unplaced || []).forEach(function (u) { skip.push({ name: u.count + '× ' + u.name, reason: 'nicht platziert (kein Platz / Max. Anzahl Rohteile)' }); });
      Object.keys(pk).forEach(function (n) { skip.push({ name: pk[n] + '× ' + n, reason: 'geparkt – nicht platziert („Neu nesten“ ordnet es ein)' }); });
    }
    if (ap && ap.skipped) ap.skipped.forEach(function (s) { skip.push({ name: s.name, reason: 'beim Übernehmen: ' + s.reason }); });
    var ul = $('resSkip'); ul.innerHTML = '';
    skip.forEach(function (s) { var li = el('li', {}, ul); el('b', {}, li, s.name); li.appendChild(document.createTextNode(' – ' + s.reason)); });
    $('resSkipBox').hidden = !skip.length;
    var ra = $('resApplied');
    if (ap) {
      ra.hidden = false;
      ra.textContent = '✔ Übernommen: ' + ap.sheets + ' Rohteil(e), ' + ap.parts + ' Teil(e), Projekt ' + ap.project + ' v' + ap.version +
        (ap.skipped && ap.skipped.length ? ' – ' + ap.skipped.length + ' übersprungen' : '') + '.';
      ra.title = ap.text || '';
    } else ra.hidden = true;
    var lp = (ap && ap.diagFile) || (S.session && S.session.logFile) || '';
    $('logPath').textContent = lp;
  }

  // ---------------------------------------------------------------- Infobox / Lizenz
  function renderLicense(l) { if (S.session) S.session.license = l; window.CACore.renderLicense(l); }
  function openInfo() { window.CACore.openInfo(); }

  // ---------------------------------------------------------------- Fusion -> Palette
  
  function ensureLangSelect() {
    if ($('langSel')) return $('langSel');
    var hdr = $('hdr'); if (!hdr) return null;
    var sel = document.createElement('select');
    sel.id = 'langSel'; sel.className = 'lang';
    sel.style.cssText = 'margin-left:auto;max-width:7.5rem;font-size:12px;';
    hdr.insertBefore(sel, $('btnInfo') || null);
    sel.addEventListener('change', function () {
      send('language', { language: sel.value }, function (r) {
        var d = r; try { if (typeof r === 'string') d = JSON.parse(r); } catch (e) {}
        if (d && d.i18n) applyI18n(d.i18n);
      });
    });
    return sel;
  }
  function applyI18n(payload) {
    if (!payload || !window.CA_I18n) return;
    CA_I18n.set(payload);
    CA_I18n.apply(document);
    var sel = ensureLangSelect();
    if (sel) CA_I18n.fillSelect(sel, payload.language);
    var map = { 'Vorschau': 'tab_preview', 'Nesting': 'tab_nest', 'Ergebnis': 'tab_result',
      'Auswahl': 'tab_select', 'Platte': 'tab_plate' };
    document.querySelectorAll('#tabs .tab').forEach(function (btn) {
      var k = map[btn.getAttribute('data-tab')]; if (!k) return;
      var sp = btn.querySelector('span'); if (sp) sp.textContent = CA_I18n.t(k);
    });
    if ($('btnInfo')) $('btnInfo').title = CA_I18n.t('info');
  }

  function onSession(s) {
    S.session = s;
    if (s.info) window.CACore.setInfo(s.info);
    setTheme(s.theme);
    if (s.i18n) applyI18n(s.i18n);
    $('ver').textContent = 'v' + s.version;
    // 2.0.5: Fusion hat keine Sitzung mehr (Dokument gewechselt/geschlossen, Liste geleert, Rohteile gelöscht),
    // die Palette zeigt aber noch ein altes Nesting -> verwerfen, damit nichts Veraltetes übernommen wird
    if (!s.job && APP() && APP().hasJob && APP().hasJob()) {
      APP().reset(s.resetMessage || 'Sitzung zurückgesetzt – bereit für ein neues Nesting.');
      S.job = null; S.layout = null; S.applied = null;
    }
    if (s.settings) {
      fillSettings(s.settings, s.options);
      if (APP() && !s.job) APP().setDraft(s.settings, s.stock, s.version);
    }
    if (s.rows) renderRows(s.rows);
    $('chkAsm').checked = !!s.assembly;
    S.applied = s.applied || null;
    renderLicense(s.license);
    if (s.migration && s.migration.copied) notice('ok', 'Einstellungen/Projekte aus ContourNest übernommen (' + s.migration.copied + ' Datei(en)).');
    if (!s.design) notice('warn', 'Keine Konstruktion aktiv – bitte im Arbeitsbereich KONSTRUKTION öffnen.');
    if (s.job && APP()) APP().loadJob(s.job);
    else showTab((s.prefs && s.prefs.lastTab) || 'Vorschau');
    plateSum();
  }
  var api = {
    onSession: onSession,
    beforeJob: function (j) { S.job = j; },
    onJob: function (j) {
      S.job = j; S.applied = null;
      clean();
      if (window.innerWidth <= 700) { $('side').classList.add('collapsed'); $('content').scrollTop = 0; }   // schmal: Ansicht zuerst
      if (j.maxSheets) $('sMax').value = j.maxSheets;
      plateSum();
      $('sheetNone').hidden = true;
      showTab('Vorschau', false);
      var sk = j.skipped || [], wn = j.warnings || [];
      if (sk.length || wn.length) {
        notice('warn', (sk.length ? sk.length + ' Teil(e) beim Nesten nicht berücksichtigt' : '') + (sk.length && wn.length ? ' · ' : '') +
          (wn.length ? 'Baugruppenmodus: ' + wn.length + ' Kopie(n) aus veralteter Quelle' : '') + ':', sk.concat(wn));
      }
    },
    onLayout: function (lay) {
      S.layout = lay;
      $('sheetNone').hidden = !!lay;
      if (S.tab === 'Ergebnis') renderResult();
    },
    onButtons: function (running) {
      $('btnNest').disabled = running || !S.rows.some(function (r) { return r.usable && r.enabled && r.qty > 0; });
      $('btnPick').disabled = running;
    },
    applyOpts: applyOpts,
    handle: function (action, data) {
      var d = null; try { d = JSON.parse(data); } catch (e) { d = data; }
      if (action === 'session') onSession(d);
      else if (action === 'rows') { if (d.assembly !== undefined) $('chkAsm').checked = !!d.assembly; renderRows(d.rows); if (d.tab) showTab(d.tab, false); }
      else if (action === 'notice') { notice(d.level, d.text, d.skipped); if (d.tab) showTab(d.tab, false); if (d.skipped && S.job) { S.job.skipped = d.skipped; } }
      else if (action === 'applied') { S.applied = d; showTab('Ergebnis', false); notice('ok', 'Nesting übernommen – Details im Register „Ergebnis“.'); }
      else if (action === 'reset') { S.job = null; S.layout = null; S.applied = null; S.rows = []; renderRows([]); notice(); showTab('Auswahl', false); }
      else if (action === 'theme') setTheme(d);
      else if (action === 'license') renderLicense(d);
      else return 'Unbekannte Aktion: ' + action;
      return 'OK';
    },
    state: function () { return S; }, showTab: showTab, setTheme: setTheme, renderRows: renderRows, collectSettings: collectSettings
  };
  window.LazyShell = api;

  document.addEventListener('DOMContentLoaded', function () {
    ensureLangSelect();
    Array.prototype.forEach.call(document.querySelectorAll('#tabs .tab'), function (b) {
      b.onclick = function () { showTab(b.getAttribute('data-tab'), true); };
    });
    window.CACore.init({ send: send, logo: 'img/logo_', onLicense: function (l) { if (S.session) S.session.license = l; } });
    $('btnInfo').onclick = openInfo;
    $('btnPick').onclick = function () { notice(); send('pick', { assembly: $('chkAsm').checked }); APP().setStatus('Teile in Fusion wählen – OK kehrt hierher zurück …'); };
    // 2.0.5: „Liste leeren“ = komplette LazyNest-Sitzung zurücksetzen; Knopf kurz sperren (Mehrfachklick)
    $('btnClear').onclick = function () {
      var b = $('btnClear'); if (b.disabled) return;
      b.disabled = true; setTimeout(function () { b.disabled = false; }, 800);
      send('clearParts', {}, function (r) {
        if (r && r.reset) {
          if (APP()) APP().reset('Liste geleert – LazyNest-Sitzung zurückgesetzt.');
          api.handle('reset', '{}');
          if (r.session) onSession(r.session);
        }
        renderRows((r && r.rows) || []);
      });
    };
    $('btnDeleteNest').onclick = function () { send('deleteNesting', {}); APP().setStatus('Nesting löschen – Rückfrage in Fusion …'); };
    $('btnRefreshCopies').onclick = function () { send('refreshCopies', {}); APP().setStatus('Kopien werden aktualisiert …'); };
    $('btnNest').onclick = function () {
      notice(); clean();
      var b = APP().backLayout();
      send('nest', { settings: collectSettings(), parts: rowSettings(), assembly: $('chkAsm').checked,
                     jobId: b.jobId, overrides: b.overrides, layout: b.jobId ? b.layout : null });
      APP().setStatus('Nesting wird vorbereitet …');
    };
    $('chkAsm').onchange = function () {
      // 2.0.7: Modus sofort nach Fusion; Liste sync (fehlerhafte teil → kopie / bereinigt)
      var on = $('chkAsm').checked;
      send('setAssembly', { assembly: on }, function (r) {
        if (!r) return;
        if (r.assembly !== undefined) $('chkAsm').checked = !!r.assembly;
        if (r.rows) renderRows(r.rows);
        if (r.notice) notice(r.dropped ? 'warn' : 'info', r.notice);
        else if (S.rows.length && on) notice('info', 'Baugruppenmodus an – verknüpfte/Unterbaugruppen-Teile als Kopien; ggf. fehlende neu wählen.');
        else if (S.rows.length && !on) notice('info', 'Baugruppenmodus aus – Einzelteile neu geprüft.');
      });
    };
    Array.prototype.forEach.call(document.querySelectorAll('#qPreset button'), function (b) {
      b.onclick = function () { var v = PRESETS[b.getAttribute('data-q')]; $('sTime').value = v[0]; $('sGens').value = v[1]; $('sPop').value = v[2]; markPreset(); };
    });
    ['sTime', 'sGens', 'sPop'].forEach(function (id) { $(id).addEventListener('change', markPreset); });
    ['qL', 'qB', 'qT', 'qMatTxt', 'qG', 'qF', 'qS', 'qR', 'sTol', 'sVacA', 'sConv', 'qThk', 'qMat', 'qFmt'].forEach(function (id) {
      $(id).addEventListener('change', refreshRows);
    });
    // 2.0.1: Platte/Rand/Abstand nesten NICHT automatisch neu – „Neu nesten“ blinkt; Rand/Abstand gelten sofort für Ziehen/Prüfung
    ['qL', 'qB', 'qT', 'qMatTxt', 'qG', 'qF', 'qS', 'sMax', 'sGap', 'qThk', 'qMat', 'qFmt'].forEach(function (id) {
      $(id).addEventListener('change', function () { dirty(); plateSum(); });
    });
    ['qF', 'qS', 'qL', 'qB'].forEach(function (id) {
      $(id).addEventListener('change', function () { if (APP() && APP().applyLive) APP().applyLive(); });
    });
    $('sideToggle').onclick = function () { $('side').classList.toggle('collapsed'); };
    $('btnLog').onclick = function () { send('openLogs', {}, function (r) { if (r && r.path && APP()) APP().setStatus((r.ok ? 'Geöffnet: ' : 'Ordner: ') + r.path); }); };
    var rt = null;
    window.addEventListener('resize', function () { if (rt) clearTimeout(rt); rt = setTimeout(function () { send('pref', { size: true }); }, 800); });
    if (!S.session) showTab('Vorschau', false);
  });
}());
