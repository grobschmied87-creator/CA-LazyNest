/* CA LazyNest 2.0.7 (vormals ContourNest) – Palettenlogik: Fusion <-> Engine <-> interaktive SVG-Vorschau.
   Kein Web Worker: die Engine rechnet in Häppchen per setTimeout.
   Manuelle Änderungen arbeiten direkt am Layout; Fixierungen, Wenden, Faser-Modus und Ausschlüsse
   werden als „overrides“ (Schlüssel „Teil-ID#Kopie“) gespeichert und überleben „Neu berechnen“.
   1.9.0: Sperrzonen beim manuellen Platzieren (Teileabstand / Randbesäumung + ½ Abstand) – Ziehen gleitet an
   den Zonen entlang, Drehen/Wenden weicht aus, Strg übersteuert bewusst, „Abstände korrigieren“.
   1.10.0: Rohteile je Blatt – Ausrichtung längs/quer (Teile drehen mit), Abschnitte (eigenes Maß/Faser/Besäumung/
   Material/Dicke), Füllreihenfolge, Nullpunkt je Rohteil (Achsenkreuz, Klick auf Ecke/Mitte), „Neu nesten“ je Blatt.
   Koordinaten der Palette = Maschinentisch; Fusion rechnet bei „quer“ ins Materialsystem um.
   2.0.0: Hauptfenster mit Registern – Rahmen, Auswahl, Theme, Infobox in shell.js (window.LazyShell).
   2.0.5: Parkzone – Teile, die nach Drehen/Wenden nicht passen oder neben die Platte gezogen werden, liegen grau
   rechts neben ihrem Rohteil (S.parked, „nicht platziert“) statt abgelehnt zu werden; „Neu nesten“ ordnet sie ein.
   2.0.7: Baugruppenmodus-Sync; 2.0.6: Unplatzierte nach Nesten/Faserwechsel immer parken (sichtbar, drehbar, Neu-Nesten); fixRot nach Faser-Snap;
   Reset leert die Rohteil-Liste (#sheetList).
   2.0.11: Rechtsklick-Kontextmenü am Teil (Hotkeys und rechtes Panel bleiben); Teile per Ziehen auf ein anderes Rohteil
   legen (Koordinaten ins Zielblatt, sonst findSpot, sonst parken). */
(function () {
  'use strict';
  var E = window.ContourNestEngine;
  var job = null, nester = null, running = false, lastDraw = 0, seed = 1;
  var layout = null;          // aktuelles Layout (Ergebnis + manuelle Änderungen)
  var ov = {};                // overrides je Stück
  var selected = null;        // Schlüssel des gewählten Teils
  var conflicts = {};         // key -> Grund
  var view = null;            // {x,y,w,h} viewBox
  var drag = null;            // laufende Maus-Aktion
  var ctxEl = null;           // Rechtsklick-Menü (#partCtx)
  var geo = null;             // Zeichengeometrie (Blattversätze)
  var selSheet = 0;           // gewähltes Rohteil (Index in layout.sheets) für die Rohteil-Liste
  var sheetJob = null;        // laufendes „Neu nesten“ eines Blatts
  var $ = function (id) { return document.getElementById(id); };
  var COLORS = ['#4e79a7', '#f28e2b', '#59a14f', '#e15759', '#76b7b2', '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac'];
  var GRAIN_TXT = { laengs: 'längs', quer: 'quer', frei: 'frei' };

  function fmt(x, d) { return Number(x).toLocaleString('de-DE', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); }
  function setStatus(t, warn) { $('status').textContent = t; $('status').className = warn ? 'warn' : ''; }
  function norm(a) { a = a % 360; if (a < 0) a += 360; return Math.round(a * 1e6) / 1e6; }

  function sendToFusion(action, obj) {
    if (!window.adsk || !window.adsk.fusionSendData) { setStatus('Keine Verbindung zu Fusion (adsk.fusionSendData fehlt).', true); return null; }
    return window.adsk.fusionSendData(action, JSON.stringify(obj || {}));
  }

  // ================================================================ Job / Berechnung
  function loadJob(j) {
    if (!j || !j.parts) return;
    if (job && j.jobId && job.jobId === j.jobId && nester) return;   // doppelt erhalten
    if (window.LazyShell) window.LazyShell.beforeJob(j);
    job = j; seed = 1; ov = JSON.parse(JSON.stringify(j.overrides || {}));
    selected = null; view = null; layout = null;
    fillQuick(); fillStock(); updateFeatViewUi();
    if (j.version) { $('ver').textContent = 'v' + j.version; document.title = 'CA LazyNest ' + j.version; }
    if (j.initialLayout && j.initialLayout.sheets && j.initialLayout.sheets.length) loadFixed(j.initialLayout);
    else startNesting();
    if (window.LazyShell) window.LazyShell.onJob(j);
  }
  // Vorhandenes Nesting (Projekt) exakt übernehmen – ohne Neuberechnung
  function loadFixed(init) {
    stopNesting(true);
    try {
      // 2.0.4: Fixierungen für Stücke, die in der behaltenen Anordnung NICHT vorkommen (Menge geändert, Teil
      // neu), sind veraltet – sonst legt die Engine sie ungeprüft an alte Koordinaten (Überlappung).
      var inKeys = {}, stale = 0;
      init.sheets.forEach(function (S) { S.forEach(function (p) { inKeys[p.key] = 1; }); });
      Object.keys(ov).forEach(function (k) {
        var o = ov[k];
        if (o && o.pinned && !inKeys[k]) { delete o.pinned; delete o.sheet; delete o.x; delete o.y; delete o.rot; stale++; }
      });
      var jj = JSON.parse(JSON.stringify(job)); delete jj.initialLayout;
      jj.overrides = ov; jj.seed = job.seed || 1;
      nester = new E.Nester(jj);
    } catch (e) { setStatus('Fehler beim Laden: ' + e.message, true); return; }
    var used = {}, missing = 0;
    layout = { sheet: { length: job.sheet.length, width: job.sheet.width, frame: job.sheet.frame,
                        spacing: job.sheet.spacing, grain: job.sheet.grain },
               rotStep: job.rotStep, sheets: [], unplaced: [], excluded: [], utilisation: 0 };
    var idefs = init.defs || [], parkDefs = {};
    Object.keys(ov).forEach(function (k) { var o = ov[k]; if (o && o.parked && !o.excluded && !inKeys[k] && o.parkDef) parkDefs[o.parkDef] = 1; });
    init.sheets.forEach(function (S, si) {
      var parts = [], def = idefs[si] ? JSON.parse(JSON.stringify(idefs[si])) : null;
      S.forEach(function (p) {
        var inst = nester.instanceByKey(p.key), t = nester.typeById[p.id];
        if (!inst || !t || used[p.key]) { missing++; return; }
        used[p.key] = 1;
        var r = nester.partRecord(t, inst, norm(p.rot), !!p.flip, p.x, p.y);
        r.pinned = !!p.pinned;
        if (r.pinned) { var o = ov[p.key] || {}; o.pinned = true; ov[p.key] = o; }
        parts.push(r);
      });
      if (parts.length || (def && (def.kind === 'abschnitt' || parkDefs[def.id])))
        layout.sheets.push({ index: layout.sheets.length + 1, utilisation: 0, parts: parts, def: def });
    });
    ensureDefs();
    // 2.0.5: geparkte Teile (Overrides) wieder in die Parkzone ihres Rohteils – nicht automatisch platzieren
    var nPk = 0;
    nester.instances.forEach(function (inst) {
      var o = ov[inst.key];
      if (!o || !o.parked || o.excluded || used[inst.key]) return;
      var t = nester.types[inst.type], S = null;
      layout.sheets.forEach(function (X) { if (!S && X.def && X.def.id === o.parkDef) S = X; });
      if (!S) S = layout.sheets[layout.sheets.length - 1];
      if (!S) { S = { index: 1, utilisation: 0, parts: [], def: newDef() }; layout.sheets.push(S); }
      var gm = geomOf(S), rots = rotsOn(inst, gm);
      var r = nester.partRecord(t, inst, norm(rots[0] || 0), !!inst.flip, 0, 0, gm);
      parkRecords(S, [r]); used[inst.key] = 1; nPk++;
    });
    // 2.0.4: behaltene, nicht fixierte Teile mit Konflikt (geänderter Abstand/Rand/Kontur/Maß) neu platzieren
    var evicted = 0;
    layout.sheets.forEach(function (S) {
      var ev = E.evictConflicts(sheetRecOf(S), S.parts);
      if (!ev.length) return;
      S.parts = S.parts.filter(function (p) { return ev.indexOf(p.key) < 0; });
      ev.forEach(function (k) { delete used[k]; }); evicted += ev.length;
    });
    // neue (noch nicht platzierte) Stücke: größte zuerst in freie Flächen, sonst neues Rohteil (bis maxSheets)
    var added = 0, newSheets = 0, maxS = Math.max(1, job.maxSheets || 1);
    var rest = nester.instances.filter(function (inst) { return !used[inst.key] && !(ov[inst.key] && ov[inst.key].excluded); });
    rest.sort(function (a, b) { return nester.types[b.type].area - nester.types[a.type].area; });
    rest.forEach(function (inst) {
      var t = nester.types[inst.type], placed = false;
      for (var si = 0; si < layout.sheets.length + 1 && !placed; si++) {
        if (si === layout.sheets.length) {
          if (layout.sheets.length >= maxS) break;
          layout.sheets.push({ index: layout.sheets.length + 1, utilisation: 0, parts: [], def: newDef() }); newSheets++;
        }
        var S = layout.sheets[si], gm = geomOf(S), rots = rotsOn(inst, gm);
        var spot = nester.findSpot(S.parts, t.id, rots, !!inst.flip, gm);
        if (spot) {
          S.parts.push(nester.partRecord(t, inst, norm(spot.rot), !!inst.flip, spot.x, spot.y, gm));
          placed = true; added++;
        } else if (!S.parts.length && si === layout.sheets.length - 1 && S.def.kind !== 'abschnitt') { layout.sheets.pop(); newSheets--; break; }
      }
      if (!placed) {
        // 2.0.6: nicht einfügbar -> Parkzone (nicht nur unplaced/unsichtbar)
        if (!layout.sheets.length) { layout.sheets.push({ index: 1, utilisation: 0, parts: [], def: newDef() }); newSheets++; }
        var S2 = layout.sheets[layout.sheets.length - 1], gm2 = geomOf(S2), rots2 = rotsOn(inst, gm2);
        var rec2 = nester.partRecord(t, inst, norm(rots2[0] || 0), !!inst.flip, 0, 0, gm2);
        if (inst.fixRot !== null && inst.fixRot !== undefined) rec2.fixRot = norm(inst.fixRot);
        parkRecords(S2, [rec2]); used[inst.key] = 1; nPk++;
        setOv(parkedOf(S2)[parkedOf(S2).length - 1], S2.index);
      }
    });
    layout.unplaced = [];
    ensureDefs(); syncPinnedFromLayout(); afterChange();
    var pr = job.project ? ('Projekt ' + job.project.id + ' v' + job.project.version + ': ') : '';
    var nun = rest.length - added;
    setStatus(pr + 'vorhandene Anordnung geladen' + (missing ? ' (' + missing + ' Stück fehlen)' : '') +
              (evicted ? ', ' + evicted + ' Teil(e) wegen Abstand/Rand neu platziert' : '') +
              (stale ? ', ' + stale + ' veraltete Fixierung(en) gelöst' : '') +
              (nPk ? ', ' + nPk + ' Teil(e) geparkt (nicht platziert)' : '') +
              (added ? ', ' + added + ' neue(s) Stück in freie Flächen' + (newSheets ? ' / ' + newSheets + ' neue(s) Rohteil' : '') + ' gesetzt' : '') +
              (nun ? ', ' + nun + ' passen nicht und sind geparkt' : '') +
              '. „Neu berechnen“ ordnet nur nicht fixierte Teile neu an.', nun > 0);
  }
  // 2.0.4: Nesting gelöscht -> alles verwerfen (Auftrag, Anordnung, Fixierungen); danach Entwurf aus der Sitzung
  function resetAll(msg) {
    if (running) stopNesting(true);
    job = null; layout = null; nester = null; ov = {}; conflicts = {}; selected = null; view = null; geo = null;
    selSheet = 0; placerFor = null; placers = {}; zoneCache = {};
    var host = $('view'); if (host) host.innerHTML = '';
    if ($('summary')) $('summary').innerHTML = '';
    if (window.LazyShell) window.LazyShell.onLayout(null);
    // 2.0.6: Rohteil-Liste links leeren (updateSheetPanel setzt #sheetList/#sheetNone/#shEdit)
    updateSheetPanel(); updateButtons();
    setStatus(msg || 'Nesting gelöscht – bereit für ein neues Nesting.');
  }
  // 2.0.0: Entwurf vor dem ersten Nesten (nur Felder/Bibliothek, keine Berechnung)
  function setDraft(st, stock, version) {
    if (job && !job.draft) return;
    job = { draft: true, jobId: '', parts: [], material: st.material || '', stock: stock || [], version: version,
            sheet: { length: st.length, width: st.width, thickness: st.thickness, frame: st.frame, spacing: st.spacing, grain: st.grain === 'Y' ? 'Y' : 'X' },
            rotStep: st.rot_step, vacuumWeight: st.vac_weight, featView: st.feat_view, maxSheets: st.max_sheets };
    fillQuick(); fillStock(); updateFeatViewUi(); updateButtons();
  }
  function fillQuick() {
    var s = job.sheet;
    $('qL').value = s.length; $('qB').value = s.width; $('qF').value = s.frame; $('qS').value = s.spacing;
    $('qT').value = s.thickness || ''; $('qG').value = s.grain === 'Y' ? 'Y' : 'X'; $('qMatTxt').value = job.material || '';
    $('qR').value = String(job.rotStep || 0); $('qV').value = job.vacuumWeight || 0; $('qVv').textContent = $('qV').value;
    fillQuickInfo();
  }
  function readQuick() {
    var num = function (id, min) { var v = parseFloat($(id).value); return isFinite(v) && v >= min ? v : null; };
    var L = num('qL', 1), B = num('qB', 1), F = num('qF', 0), S = num('qS', 0);
    if (L === null || B === null || F === null || S === null) { setStatus('Ungültige Rohteil-Werte.', true); return false; }
    if (2 * F + S >= Math.min(L, B)) { setStatus('Randbesäumung/Teileabstand zu groß für das Rohteil.', true); return false; }
    job.sheet.length = L; job.sheet.width = B; job.sheet.frame = F; job.sheet.spacing = S;
    job.rotStep = parseFloat($('qR').value); job.vacuumWeight = parseInt($('qV').value, 10);
    var T = num('qT', 0.1); if (T !== null) job.sheet.thickness = T;
    job.sheet.grain = $('qG').value === 'Y' ? 'Y' : 'X'; job.material = $('qMatTxt').value.trim();
    var M = parseInt($('sMax').value, 10); if (isFinite(M) && M >= 1) job.maxSheets = M;
    return true;
  }
  // ---------------------------------------------------------------- Rohteilbibliothek (kompakt)
  var UNV = ' (ungeprüft)';
  function stockList() { return (job && job.stock) || []; }
  function fkey(f) { return f[0] + 'x' + f[1]; }
  function stockThk(m, fi) {
    var ft = (fi !== null && fi >= 0 && m.formats[fi]) ? (m.format_thicknesses || {})[fkey(m.formats[fi])] : null;
    return (ft && ft.length) ? ft.slice() : m.thicknesses.slice();
  }
  function isUnv(list, v) {
    return (list || []).some(function (u) { return Array.isArray(v) ? (u[0] === v[0] && u[1] === v[1]) : u === v; });
  }
  function opt(sel, value, text, on) {
    var o = document.createElement('option'); o.value = value; o.textContent = text; if (on) o.selected = true; sel.appendChild(o); return o;
  }
  function fillStock() {
    var lst = stockList(), sel = $('qMat');
    $('qStock').hidden = !lst.length;
    sel.innerHTML = '';
    var s = job.sheet, mi = -1;
    lst.forEach(function (m, i) { if (mi < 0 && m.material === job.material) mi = i; });
    opt(sel, '', '(freie Eingabe)', mi < 0);
    var groups = {};
    lst.forEach(function (m, i) {
      var g = m.group || 'Benutzerdefiniert';
      if (!groups[g]) { groups[g] = document.createElement('optgroup'); groups[g].label = g; sel.appendChild(groups[g]); }
      opt(groups[g], String(i), m.material + (m.user ? ' ★' : ''), i === mi);
    });
    var m = mi >= 0 ? lst[mi] : null, fi = -1;
    if (m) m.formats.forEach(function (f, k) { if (fi < 0 && Math.abs(f[0] - s.length) < 0.05 && Math.abs(f[1] - s.width) < 0.05) fi = k; });
    fillFmt(m, fi);
    fillThk(m, fi >= 0 ? fi : null, s.thickness);
  }
  function fillFmt(m, fi) {
    var sel = $('qFmt'); sel.innerHTML = '';
    if (!m) { opt(sel, '', '–', true); sel.disabled = true; return; }
    sel.disabled = false;
    if (fi < 0) opt(sel, '', '(eigenes Maß)', true);
    m.formats.forEach(function (f, k) { opt(sel, String(k), f[0] + ' × ' + f[1] + (isUnv(m.unverified_formats, f) ? UNV : ''), k === fi); });
  }
  function fillThk(m, fi, cur) {
    var sel = $('qThk'); sel.innerHTML = '';
    if (!m) { opt(sel, '', '–', true); sel.disabled = true; return null; }
    sel.disabled = false;
    var th = stockThk(m, fi), best = -1;
    th.forEach(function (t, k) { if (best < 0 || Math.abs(t - cur) < Math.abs(th[best] - cur)) best = k; });
    var exact = best >= 0 && Math.abs(th[best] - cur) < 0.005;
    th.forEach(function (t, k) { opt(sel, String(t), fmt(t, t % 1 ? 1 : 0) + ' mm' + (isUnv(m.unverified_thicknesses, t) ? UNV : ''), k === best); });
    return exact ? th[best] : (best >= 0 ? th[best] : null);
  }
  function stockSel() { var v = $('qMat').value; return v === '' ? null : stockList()[parseInt(v, 10)]; }
  function setThickness(t) {
    if (t === null || t === undefined || !isFinite(t)) return;
    job.sheet.thickness = t; $('qT').value = t;
    var tp = (job.parts || []).filter(function (p) { return p.thickness && Math.abs(p.thickness - t) > 0.5; });
    fillQuickInfo();
    if (tp.length) setStatus('Achtung: Rohteildicke ' + fmt(t, 1) + ' mm passt nicht zu ' + tp.length + ' Teiltyp(en) (' +
      tp.map(function (p) { return p.name + ' ' + fmt(p.thickness, 1) + ' mm'; }).slice(0, 3).join(', ') + ').', true);
  }
  function fillQuickInfo() {
    var s = job.sheet;
    $('qInfo').textContent = (job.material || 'ohne Material') + ', ' + fmt(s.thickness || 0, 1) + ' mm, Faser ' + (s.grain === 'Y' ? 'Y' : 'X');
  }
  var stockUi = {
    material: function () {
      var m = stockSel();
      if (!m) { fillFmt(null, -1); fillThk(null); fillQuickInfo(); return; }    // freie Eingabe: Werte bleiben
      job.material = m.material; job.sheet.grain = m.grain === 'Y' ? 'Y' : 'X';
      $('qMatTxt').value = m.material; $('qG').value = job.sheet.grain;
      fillFmt(m, m.formats.length ? 0 : -1);
      stockUi.format();
    },
    format: function () {
      var m = stockSel(); if (!m) return;
      var fi = $('qFmt').value === '' ? null : parseInt($('qFmt').value, 10);
      if (fi !== null) { $('qL').value = m.formats[fi][0]; $('qB').value = m.formats[fi][1]; }
      setThickness(fillThk(m, fi, job.sheet.thickness));
    },
    thickness: function () {
      var v = parseFloat($('qThk').value);
      if (isFinite(v)) setThickness(v);
    },
    save: function () {
      if (!readQuick()) return;
      var m = stockSel();
      var d = { group: m ? m.group : 'Benutzerdefiniert', material: job.material || 'Eigene Vorlage',
        length: job.sheet.length, width: job.sheet.width, thickness: job.sheet.thickness, grain: job.sheet.grain };
      var res = sendToFusion('saveStock', d), jid = job.jobId;
      var done = function (rep) {
        if (!job || job.jobId !== jid) return;
        try { var l = JSON.parse(rep); if (Array.isArray(l)) { job.stock = l; fillStock(); } } catch (e) { /* ohne Antwort */ }
        setStatus('Vorlage „' + d.material + ' ' + d.length + ' × ' + d.width + ' × ' + fmt(d.thickness, 1) + ' mm“ in user_stock.json gespeichert.');
      };
      if (res && typeof res.then === 'function') res.then(done); else done(res);
    }
  };
  function quickSettings() {
    return { length: job.sheet.length, width: job.sheet.width, frame: job.sheet.frame, spacing: job.sheet.spacing,
      grain: job.sheet.grain, rotStep: job.rotStep, vacuumWeight: job.vacuumWeight,
      thickness: job.sheet.thickness, material: job.material || '', featView: featView() };
  }
  function startNesting() {
    if (!job || job.draft) return;
    stopNesting(true);
    try {
      syncPinnedFromLayout();
      var jj = JSON.parse(JSON.stringify(job));
      jj.overrides = ov; jj.seed = (job.seed || 1) * 7919 + seed++;
      jj.sheets = layout ? sheetDefs() : (job.sheets || []);   // 1.10.0: Blätter in Füllreihenfolge (Abschnitte, Ausrichtung, Nullpunkt)
      delete jj.initialLayout;
      nester = new E.Nester(jj);
    } catch (e) { setStatus('Fehler beim Vorbereiten: ' + e.message, true); return; }
    if (!nester.instances.length) { setStatus('Keine aktiven Teile.', true); return; }
    running = true; updateButtons(); tick();
  }
  // 2.0.1: Rand/Teileabstand/Plattenmaß sofort für Ziehen (Sperrzonen) und Abstandsprüfung – ohne neu zu nesten
  function applyLive() {
    if (!job || job.draft || running || sheetJob) return false;
    if (!readQuick()) return false;
    if (layout && layout.sheet) {
      layout.sheet.spacing = job.sheet.spacing; layout.sheet.frame = job.sheet.frame;
      layout.sheet.length = job.sheet.length; layout.sheet.width = job.sheet.width;
    }
    try {
      syncPinnedFromLayout();
      var jj = JSON.parse(JSON.stringify(job));
      jj.overrides = ov; jj.sheets = layout ? sheetDefs() : (job.sheets || []);
      delete jj.initialLayout;
      nester = new E.Nester(jj);            // nur Geometrie (Abstand in den Formen) – keine Berechnung
    } catch (e) { setStatus('Abstand übernehmen: ' + e.message, true); return false; }
    placerFor = null; placers = {}; zoneCache = {};
    afterChange();
    setStatus('Rand ' + fmt(job.sheet.frame, 1) + ' mm / Teileabstand ' + fmt(job.sheet.spacing, 1) +
              ' mm gelten jetzt für Ziehen und Abstandsprüfung – „Neu nesten“ ordnet neu an.');
    return true;
  }
  function tick() {
    if (!running || !nester) return;
    var more;
    try { more = nester.step(60); } catch (e) { running = false; setStatus('Fehler in der Berechnung: ' + e.message, true); updateButtons(); return; }
    var now = Date.now();
    if (nester.bestChanged && now - lastDraw > 250) { nester.bestChanged = false; lastDraw = now; takeResult(); }
    progress();
    if (more) setTimeout(tick, 0);
    else {
      running = false; if (nester.best) takeResult(); progress(true); updateButtons();
      var nd = (nester.demoted || []).length, np = nParked();
      if (nd || np) setStatus($('status') ? ($('status').textContent + (nd ? ' – ' + nd + ' ungültige Fixierung(en) (außerhalb/Kollision) neu platziert' : '') +
        (np ? ' – ' + np + ' Teil(e) passen nicht und bleiben geparkt' : '')) : '', np > 0);
    }
  }
  function stopNesting(silent) {
    if (sheetJob) { sheetJob.sn.stop(); finishSheetJob(); return; }
    var was = running;
    running = false;
    if (nester && was) { nester.stop(); if (nester.best && !silent) takeResult(); progress(true); }
    updateButtons();
  }
  function takeResult() {
    var r = nester.result();
    if (!r) return;
    layout = r;
    ensureDefs();
    // 2.0.5: ungültige Fixierungen (außerhalb/Kollision) hat die Engine als freie Gene neu angeordnet -> lösen
    (r.demoted || []).forEach(function (k) {
      var o = ov[k]; if (o) { delete o.pinned; delete o.sheet; delete o.x; delete o.y; delete o.rot; }
    });
    // 2.0.6: alle unplatzierten Teile in die Parkzone (sichtbar, drehbar, Neu-Nesten) – nicht nur zuvor geparkte
    layout.unplaced.forEach(function (u) {
      (u.keys || []).forEach(function (k) {
        var inst = nester.instanceByKey(k), t = nester.typeById[u.id];
        if (!inst || !t) return;
        if (!layout.sheets.length) layout.sheets.push({ index: 1, utilisation: 0, parts: [], def: newDef() });
        var o = ov[k] || {}, S = null;
        if (o.parkDef) layout.sheets.forEach(function (X) { if (!S && X.def && X.def.id === o.parkDef) S = X; });
        S = S || layout.sheets[layout.sheets.length - 1];
        var gm = geomOf(S), rots = rotsOn(inst, gm);
        var rec = nester.partRecord(t, inst, norm(rots[0] || 0), !!inst.flip, 0, 0, gm);
        if (inst.fixRot !== null && inst.fixRot !== undefined) rec.fixRot = norm(inst.fixRot);
        parkRecords(S, [rec]);
        var q = parkedOf(S)[parkedOf(S).length - 1];
        setOv(q, S.index);
      });
    });
    layout.unplaced = [];
    if (selected && !findPart(selected)) selected = null;
    syncPinnedFromLayout();
    afterChange();
  }
  function progress(final) {
    if (!nester || nester.t0 === null) return;
    var st = nester.stats, el = (Date.now() - nester.t0) / 1000;
    var p = Math.max(Math.min(1, (st.generation + 1) / nester.maxGen), Math.min(1, el * 1000 / nester.timeLimit));
    $('bar').style.width = (final ? 100 : p * 100) + '%';
    var txt = (final ? 'Fertig' : 'Berechne') + ' – Generation ' + (st.generation + 1) + '/' + nester.maxGen +
      ', ' + st.evaluated + ' Varianten, ' + fmt(el, 1) + ' s';
    if (layout) txt += ' – ' + layout.sheets.length + ' Rohteil(e)';
    setStatus(txt);
  }
  function updateButtons() {
    var real = !!job && !job.draft;
    $('btnStart').disabled = running || !real;
    $('btnRecalc').disabled = running || !real;
    $('btnStop').disabled = !running;
    $('btnApply').disabled = running || !real || !(layout && layout.sheets.some(function (S) { return S.parts.length; }));
    if (window.LazyShell) window.LazyShell.onButtons(running);
    if ($('shRenest') && layout) $('shRenest').disabled = running || !(layout.sheets[selSheet] && (layout.sheets[selSheet].parts.length || (layout.sheets[selSheet].parked || []).length));
  }

  // ================================================================ Layout-Hilfen
  function findPart(key) {
    if (!layout) return null;
    for (var i = 0; i < layout.sheets.length; i++) {
      var S = layout.sheets[i], ps = S.parts;
      for (var j = 0; j < ps.length; j++) if (ps[j].key === key) return { sheet: S, si: i, part: ps[j], pi: j, list: ps, parked: false };
      var pk = S.parked || [];
      for (var k = 0; k < pk.length; k++) if (pk[k].key === key) return { sheet: S, si: i, part: pk[k], pi: k, list: pk, parked: true };
    }
    return null;
  }
  // ---------------------------------------------------------------- 2.0.5: Parkzone
  function parkedOf(S) { return S.parked || (S.parked = []); }
  function nParked(lay) { lay = lay || layout; return lay ? lay.sheets.reduce(function (a, S) { return a + (S.parked ? S.parked.length : 0); }, 0) : 0; }
  function bboxOf(pts) {
    var b = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
    pts.forEach(function (q) { if (q[0] < b.x0) b.x0 = q[0]; if (q[0] > b.x1) b.x1 = q[0]; if (q[1] < b.y0) b.y0 = q[1]; if (q[1] > b.y1) b.y1 = q[1]; });
    return b;
  }
  function parkGap(gm) { return Math.max(40, Math.min(gm.W, gm.H) * 0.05); }
  // geparkte Teile eines Blatts rechts neben der Platte stapeln (von oben nach unten, dann nächste Spalte) –
  // Drehung/Wenden bleiben, nichts überlappt. Liefert die Breite der Parkzone (mm, 0 = leer).
  function relayoutParking(S) {
    var pk = S.parked || [];
    if (!pk.length) { S.parkW = 0; return 0; }
    var gm = geomOf(S), gap = parkGap(gm), pad = Math.max(10, gap * 0.4);
    var colX = gm.W + gap, y = gm.H, colW = 0, maxX = colX;
    for (var i = 0; i < pk.length; i++) {
      var p = pk[i], b = bboxOf(p.outer), w = b.x1 - b.x0, h = b.y1 - b.y0;
      if (y - h < 0 && y < gm.H) { colX += colW + pad; colW = 0; y = gm.H; }
      var np = rebuild(p, p.rot, p.flip, p.x + (colX - b.x0), p.y + (y - b.y1));
      np.parked = true; np.pinned = false; np.forced = false;
      pk[i] = np;
      y -= h + pad; colW = Math.max(colW, w); maxX = Math.max(maxX, colX + w);
    }
    S.parkW = maxX - gm.W;
    return S.parkW;
  }
  // Teil (f aus findPart) in die Parkzone seines Blatts legen; f wird auf den geparkten Datensatz umgestellt
  function parkPart(f) {
    var p = f.part;
    if (!f.parked) {
      f.list.splice(f.pi, 1);
      var q = rebuild(p, p.rot, p.flip, p.x, p.y);
      q.parked = true; q.pinned = false; q.forced = false;
      parkedOf(f.sheet).push(q);
    }
    relayoutParking(f.sheet);
    var nf = findPart(p.key);
    f.sheet = nf.sheet; f.si = nf.si; f.part = nf.part; f.pi = nf.pi; f.list = nf.list; f.parked = true;
    return f;
  }
  // geparktes Teil auf das Blatt zurück (fixiert, an x/y)
  function unparkPart(f, x, y) {
    var p = f.part;
    f.list.splice(f.pi, 1);
    var q = rebuild(p, p.rot, p.flip, x, y);
    q.parked = false; q.pinned = true;
    f.sheet.parts.push(q);
    relayoutParking(f.sheet);
    var nf = findPart(p.key);
    f.sheet = nf.sheet; f.si = nf.si; f.part = nf.part; f.pi = nf.pi; f.list = nf.list; f.parked = false;
    return f;
  }
  // Teil-Datensätze (Lage wie vorher) in die Parkzone eines Blatts legen (Neu nesten: Reste)
  function parkRecords(S, recs) {
    recs.forEach(function (r) { var q = rebuild(r, r.rot, r.flip, r.x, r.y); q.parked = true; q.pinned = false; q.forced = false; parkedOf(S).push(q); });
    relayoutParking(S);
  }
  var PARK_TXT = 'geparkt – „Neu nesten“ ordnet es ein (oder auf die Platte ziehen)';
  function typeOf(p) { return nester && nester.typeById[p.id]; }
  // ---------------------------------------------------------------- Rohteile je Blatt (1.10.0)
  function uid() { return 'R' + Date.now().toString(36) + Math.floor(Math.random() * 1e8).toString(36); }
  function newDef(kind) {
    var d = { id: uid(), kind: kind === 'abschnitt' ? 'abschnitt' : 'standard', orient: 'laengs',
              zero: E.ZERO_KEYS.indexOf(job.defaultZero) >= 0 ? job.defaultZero : 'lu_unten' };
    if (d.kind === 'abschnitt') {
      d.length = job.sheet.length; d.width = job.sheet.width; d.grain = job.sheet.grain === 'Y' ? 'Y' : 'X';
      d.frame = job.sheet.frame; d.thickness = job.sheet.thickness;
    }
    return d;
  }
  function geomOf(S) { return E.resolveSheet(S && S.def, job.sheet, job.material, job.defaultZero); }
  function sheetRecOf(S) { return E.sheetRec(geomOf(S), layout.sheet.spacing); }
  // Laufende Nummern: Index = Lage in der Liste (Füllreihenfolge), no = Rohteil-Nummer (nur Blätter mit Teilen)
  function numberSheets() {
    var n = 0;
    layout.sheets.forEach(function (S, i) { S.index = i + 1; S.no = S.parts.length ? ++n : 0; });
  }
  function ensureDefs() {
    if (!layout) return;
    layout.sheets.forEach(function (S) {
      if (!S.def) S.def = newDef();
      if (!S.def.id) S.def.id = uid();
      if (E.ZERO_KEYS.indexOf(S.def.zero) < 0) S.def.zero = newDef().zero;
      if (S.def.orient !== 'quer') S.def.orient = 'laengs';
    });
    numberSheets();
    if (selSheet >= layout.sheets.length) selSheet = Math.max(0, layout.sheets.length - 1);
  }
  function sheetDefs() { return layout ? layout.sheets.map(function (S) { return JSON.parse(JSON.stringify(S.def)); }) : []; }
  function sheetName(S) { return S.no ? 'Rohteil ' + String(S.no).padStart(2, '0') : ((S.def && S.def.kind === 'abschnitt') ? 'Abschnitt (leer)' : 'Rohteil (leer)'); }
  function labelOf(S) {
    var gm = geomOf(S);
    return S.no ? E.sheetLabel(gm, S.no) : ((S.def && S.def.kind === 'abschnitt') ? 'Abschnitt' : 'Rohteil') + ' (leer – wird nicht übernommen): ' + E.sheetLabel(gm, 0).split(': ').slice(1).join(': ');
  }
  // erlaubte Drehungen einer Instanz auf einem Blatt (Faser des Blatts)
  function rotsOn(inst, gm) {
    if (inst.fixRot !== null && inst.fixRot !== undefined) return [inst.fixRot];
    return nester.allowedRotations(nester.types[inst.type], inst.grainMode, inst.flip, inst.ignoreGrain, gm.G);
  }
  var placers = {}, placerFor = null;
  function placer(S) {         // Sperrzonen: gleiche Werte wie das aktuelle Nesting (Teileabstand, Besäumung je Blatt)
    if (!nester) return null;
    if (placerFor !== nester) { placers = {}; placerFor = nester; zoneCache = {}; }
    var gm = S ? geomOf(S) : E.resolveSheet(null, job.sheet, job.material, job.defaultZero);
    var k = gm.W + '|' + gm.H + '|' + gm.frame;
    return placers[k] || (placers[k] = new E.Placer(nester, gm));
  }
  var zoneCache = {};
  function zoneOf(p) {         // reale Kontur + Teileabstand (Anzeige beim Ziehen)
    var k = p.key + '|' + p.x + '|' + p.y + '|' + p.rot + '|' + p.flip;
    if (!zoneCache[k]) zoneCache[k] = placer().zoneOutline(p);
    return zoneCache[k];
  }
  function poseOf(p) { return { key: p.key, id: p.id, rot: p.rot, flip: p.flip }; }
  function instLike(p) {
    return { key: p.key, copy: p.copy, grainMode: p.grainMode, ignoreGrain: p.ignoreGrain, pinned: p.pinned,
             fixRot: (p.fixRot !== null && p.fixRot !== undefined) ? p.fixRot : null };
  }
  // Teil neu berechnen (Kontur, Schwerpunkt) nach Änderung von rot/flip/x/y
  function rebuild(p, rot, flip, x, y) {
    var t = typeOf(p);
    var r = nester.partRecord(t, instLike(p), norm(rot), flip, x, y);
    r.pinned = p.pinned;
    if (p.forced) r.forced = true;
    if (p.parked) { r.parked = true; r.pinned = false; }
    return r;
  }
  function replacePart(f, np) { (f.list || f.sheet.parts)[f.pi] = np; f.part = np; }
  function allowed(p, flip, mode, ignore, S) {
    if (!S) { var f0 = findPart(p.key); S = f0 && f0.sheet; }
    return nester.allowedRotations(typeOf(p), mode || p.grainMode, flip === undefined ? p.flip : flip,
      ignore === undefined ? p.ignoreGrain : ignore, S ? geomOf(S).G : undefined);
  }
  function grainOk(p) {
    var a = allowed(p);
    if (p.ignoreGrain || (p.grainMode === 'frei')) return true;
    return a.some(function (r) { return Math.abs(norm(r - p.rot)) < 1e-6 || Math.abs(norm(r - p.rot) - 360) < 1e-6; });
  }
  // Teil um seinen Schwerpunkt auf neue Drehung/Spiegelung setzen
  function snapToGrain(f) {       // auf nächste erlaubte Drehung schnappen; true wenn geändert
    var p = f.part;
    if (grainOk(p)) return false;
    var al = allowed(p), best = al[0], bd = 1e9, gm = p.grainMode, ig = p.ignoreGrain;
    al.forEach(function (r) { var d = Math.min(norm(r - p.rot), 360 - norm(r - p.rot)); if (d < bd) { bd = d; best = r; } });
    var old = p;
    setPose(f, best, p.flip);
    f = findPart(f.part.key) || f;
    f.part.grainMode = gm; f.part.ignoreGrain = ig;
    if (!settle(f, old, 'Faser-Drehung')) { f = findPart(old.key); return false; }
    f = findPart(old.key) || f;
    // 2.0.6: Faserrichtung gesetzt = Drehung fest (wie manuelles Drehen); geparkt bleibt nutzbar
    if (f) { f.part.fixRot = norm(f.part.rot); setOv(f.part, f.sheet.index); }
    return true;
  }
  function setPose(f, rot, flip) {
    var p = f.part, t = typeOf(p);
    var c0 = [p.cx, p.cy];
    var off = E.placePts([t.centroid], norm(rot), flip, 0, 0)[0];
    replacePart(f, rebuild(p, rot, flip, c0[0] - off[0], c0[1] - off[1]));
  }
  // Nach Drehen/Wenden: verletzt die neue Lage die Sperrzonen -> nächste freie Lage. 2.0.5: gibt es keine, wird
  // NICHT mehr abgelehnt – das Teil kommt mit der neuen Drehung/Wendung in die Parkzone (grau, nicht platziert).
  // old = Datensatz vor der Änderung (nur noch für die Meldung). Liefert immer true (die neue Lage/Pose gilt).
  var lastSettle = '';
  function settle(f, old, what) {
    lastSettle = '';
    if (f.parked) { parkPart(f); return true; }          // geparkt bleibt geparkt (neue Pose, neu gestapelt)
    var P = placer(f.sheet);
    if (!P) return true;
    var p = f.part, r = P.place(f.sheet.parts, poseOf(p), p.x, p.y);
    if (!r.ok) {
      parkPart(f);
      lastSettle = what + ': auf ' + sheetName(f.sheet) + ' ist in dieser Lage kein Platz (' + fmt(layout.sheet.spacing, 1) +
        ' mm Teileabstand / ' + fmt(geomOf(f.sheet).frame, 1) + ' mm Randbesäumung) – Teil ' + PARK_TXT + '.';
      setStatus(lastSettle, true);
      return true;
    }
    if (!r.free) {
      replacePart(f, rebuild(p, p.rot, p.flip, r.x, r.y));
      f.part.forced = false;
      lastSettle = what + ': Teil um ' + fmt(r.dist, 1) + ' mm auf die nächste freie Lage verschoben (Abstände eingehalten).';
      setStatus(lastSettle);
    } else f.part.forced = false;
    return true;
  }
  function syncPinnedFromLayout() {
    if (!layout) return;
    layout.sheets.forEach(function (S) {
      S.parts.forEach(function (p) {
        var o = ov[p.key] || {};
        if (o.pinned) { o.sheet = S.index; o.x = p.x; o.y = p.y; o.rot = p.rot; o.flip = p.flip; ov[p.key] = o; }
        if (o.parked) { delete o.parked; delete o.parkDef; }
      });
      (S.parked || []).forEach(function (p) {     // 2.0.5: geparkt = nicht fixiert, Pose (Wenden/Drehung) bleibt
        var o = ov[p.key] || {};
        delete o.pinned; delete o.sheet; delete o.x; delete o.y; delete o.rot;
        o.parked = true; o.parkDef = S.def && S.def.id; o.flip = p.flip; ov[p.key] = o;
      });
    });
  }
  function setOv(p, sheetIndex) {
    var o = ov[p.key] || {};
    o.flip = p.flip; o.grainMode = p.grainMode; o.ignoreGrain = p.ignoreGrain;
    // feste Drehung (ohne Fixierung): gilt immer für die aktuelle Drehung des Teils
    if (p.fixRot !== null && p.fixRot !== undefined) { p.fixRot = norm(p.rot); o.fixRot = p.fixRot; }
    else delete o.fixRot;
    if (p.pinned && !p.parked) { o.pinned = true; o.sheet = sheetIndex; o.x = p.x; o.y = p.y; o.rot = p.rot; }
    else { delete o.pinned; delete o.sheet; delete o.x; delete o.y; delete o.rot; }
    if (p.parked) {
      var S = layout && layout.sheets[sheetIndex - 1];
      o.parked = true; o.parkDef = S && S.def ? S.def.id : undefined;
    } else { delete o.parked; delete o.parkDef; }
    ov[p.key] = o;
  }
  function recomputeStats() {
    var tot = 0, used = 0;
    numberSheets();
    layout.sheets.forEach(function (S) {
      var gm = geomOf(S), A = Math.max(1e-9, gm.W * gm.H), a = 0;
      S.parts.forEach(function (p) { a += p.area; p.dist = Math.hypot(p.cx - gm.W / 2, p.cy - gm.H / 2); });
      S.utilisation = a / A; tot += a;
      if (S.parts.length) used += A;
    });
    layout.utilisation = used ? tot / used : 0;
  }
  function recomputeConflicts() {
    conflicts = {};
    if (!layout) return;
    layout.sheets.forEach(function (S) {
      var c = E.layoutConflicts(sheetRecOf(S), S.parts);
      for (var k in c) conflicts[k] = c[k];
      S.parts.forEach(function (p) { if (p.forced && !c[p.key]) p.forced = false; });
    });
  }
  function forcedKeys() {
    var out = [];
    if (layout) layout.sheets.forEach(function (S) { S.parts.forEach(function (p) { if (p.forced && conflicts[p.key]) out.push(p); }); });
    return out;
  }
  function afterChange() {
    if (window.LazyShell) setTimeout(function () { window.LazyShell.onLayout(layout); }, 0);
    recomputeStats(); recomputeConflicts(); draw(); updatePanel(); updateSheetPanel(); summary(); updateButtons();
  }
  function manualEdit() { if (running) stopNesting(true); }

  // ================================================================ Teile-Aktionen
  function stepOf(p) {
    if (!(p.ignoreGrain || p.grainMode === 'frei')) return 180;
    return (job.rotStep && job.rotStep > 0) ? job.rotStep : 90;
  }
  var actions = {
    rotate: function (dir) {
      var f = findPart(selected); if (!f) return;
      manualEdit();
      var old = f.part;
      setPose(f, f.part.rot + dir * stepOf(f.part), f.part.flip);
      if (!settle(f, old, 'Drehen')) { afterChange(); return false; }
      f.part.fixRot = norm(f.part.rot);          // gedreht = Drehung fest (auch ohne Fixierung)
      setOv(f.part, f.sheet.index); afterChange();
      return true;
    },
    setAngle: function (a) {
      var f = findPart(selected); if (!f || !isFinite(a)) return;
      var p = f.part, al = allowed(p);
      if (!(p.ignoreGrain || p.grainMode === 'frei') && al.indexOf(norm(a)) < 0) {
        $('ppWarn').textContent = 'Faser ' + GRAIN_TXT[p.grainMode] + ': nur ' + al.map(function (r) { return fmt(r, 1) + '°'; }).join(' / ') +
          ' erlaubt – „Faser ignorieren“ aktivieren, um frei zu drehen.';
        return false;
      }
      manualEdit();
      setPose(f, a, p.flip);
      if (!settle(f, p, 'Winkel ' + fmt(a, 1) + '°')) { afterChange(); $('ppWarn').textContent = lastSettle; return false; }
      f.part.fixRot = norm(f.part.rot);
      setOv(f.part, f.sheet.index); afterChange();
      return true;
    },
    flip: function () {
      var f = findPart(selected); if (!f) return;
      manualEdit();
      // Wenden um die Faserachse des Teils (Faserrichtung bleibt erhalten): r' = 2φ − r
      var p = f.part, g = typeOf(p).grainAngle;
      var phi = p.rot + (p.flip ? -g : g);
      setPose(f, 2 * phi - p.rot, !p.flip);
      if (!settle(f, p, 'Wenden')) { afterChange(); return false; }
      setOv(f.part, f.sheet.index); afterChange();
      return true;
    },
    grainMode: function (m) {
      var f = findPart(selected); if (!f) return;
      manualEdit();
      var key = f.part.key; f.part.grainMode = m;
      snapToGrain(f);
      f = findPart(key); if (!f) return;
      f.part.grainMode = m;
      // bereits grainOk: trotzdem Winkel festhalten (Faservorgabe), ggf. ohne Platz parken
      if (f.part.fixRot === null || f.part.fixRot === undefined) {
        f.part.fixRot = norm(f.part.rot);
        if (!f.parked) settle(f, f.part, 'Faser');
        f = findPart(key) || f;
      }
      setOv(f.part, f.sheet.index); afterChange();
      if (lastSettle) setStatus(lastSettle, /geparkt/.test(lastSettle));
    },
    ignoreGrain: function (on) {
      var f = findPart(selected); if (!f) return;
      manualEdit();
      f.part.ignoreGrain = !!on; f.part.grain = on ? 'frei' : f.part.grainMode;
      if (!on && snapToGrain(f)) setStatus('Faser wieder aktiv – Drehung auf ' + fmt(findPart(f.part.key).part.rot, 1) + '° angepasst.' + (lastSettle ? ' ' + lastSettle : ''));
      else if (!on && lastSettle) setStatus(lastSettle, true);
      f = findPart(f.part.key);
      setOv(f.part, f.sheet.index); afterChange();
    },
    releaseRot: function () {
      var f = findPart(selected); if (!f) return;
      manualEdit();
      f.part.fixRot = null;
      setOv(f.part, f.sheet.index);
      setStatus('Drehung freigegeben – „Neu berechnen“ darf das Teil wieder drehen.');
      afterChange();
    },
    pin: function (on) {
      var f = findPart(selected); if (!f) return;
      if (f.parked) { setStatus('Geparktes Teil: auf die Platte ziehen oder „Neu nesten“ – erst dann fixierbar.', true); return false; }
      manualEdit();
      f.part.pinned = (on === undefined) ? !f.part.pinned : !!on;
      setOv(f.part, f.sheet.index); afterChange();
    },
    moveTo: function (x, y, pin) {
      var f = findPart(selected); if (!f) return;
      manualEdit();
      var p = f.part;
      if (f.parked) unparkPart(f, x, y);
      else replacePart(f, rebuild(p, p.rot, p.flip, x, y));
      if (pin !== false || f.part.parked === false) f.part.pinned = true;
      setOv(f.part, f.sheet.index); afterChange();
    },
    toSheet: function (target, xy) {         // target: Index 1..n oder 'new'; xy: optional {x,y,forced,park}
      var f = findPart(selected); if (!f) return;
      manualEdit();
      var p = f.part, ti;
      if (target === 'new') {
        layout.sheets.push({ index: layout.sheets.length + 1, utilisation: 0, parts: [], def: newDef() });
        ti = layout.sheets.length - 1;
      } else ti = parseInt(target, 10) - 1;
      if (!layout.sheets[ti]) return;
      if (ti === f.si && !f.parked && !(xy && (xy.forceMove || xy.park))) return;
      var dest = layout.sheets[ti], al = allowed(p, undefined, undefined, undefined, dest);
      var rots = (p.fixRot !== null && p.fixRot !== undefined) ? [p.rot]
        : (al.indexOf(norm(p.rot)) >= 0 ? [p.rot] : []).concat(al.filter(function (r) { return r !== norm(p.rot); }));
      var src = f.sheet, placed = null, parked = false;
      f.list.splice(f.pi, 1);
      if (xy && xy.park) {
        parkRecords(dest, [p]);
        parked = true;
      } else {
        if (xy && isFinite(xy.x) && isFinite(xy.y)) {
          var trial = rebuild(p, p.rot, p.flip, xy.x, xy.y), gmT = geomOf(dest);
          var inside = trial.cx >= 0 && trial.cx <= gmT.W && trial.cy >= 0 && trial.cy <= gmT.H;
          if (inside) {
            dest.parts.push(trial);
            var cf = {};
            try { cf = E.layoutConflicts(sheetRecOf(dest), dest.parts); } catch (e0) { cf = {}; }
            if (!cf[p.key] || xy.forced) {
              trial.pinned = true; trial.parked = false; trial.forced = !!(xy.forced && cf[p.key]);
              placed = trial;
            } else dest.parts.pop();
          }
        }
        if (!placed) {
          var spot = nester.findSpot(dest.parts, p.id, rots, p.flip, geomOf(dest));
          if (spot) {
            var np = rebuild(p, spot.rot, p.flip, spot.x, spot.y);
            np.pinned = true; np.parked = false;
            dest.parts.push(np);
            placed = np;
          } else { parkRecords(dest, [p]); parked = true; }
        }
      }
      relayoutParking(src);
      dropEmptySheets();
      var nf = findPart(p.key);
      if (nf) {
        setOv(nf.part, nf.sheet.index);
        selSheet = nf.si;
        setStatus(placed ? 'Teil auf ' + sheetName(nf.sheet) + ' verschoben und fixiert.' :
          'Kein freier Platz auf ' + sheetName(nf.sheet) + ' – Teil ' + PARK_TXT + '.', !placed);
      }
      afterChange();
    },
    exclude: function () {
      var f = findPart(selected); if (!f) return;
      manualEdit();
      f.list.splice(f.pi, 1);
      if (f.parked) relayoutParking(f.sheet);
      ov[f.part.key] = { excluded: true, flip: f.part.flip, grainMode: f.part.grainMode, ignoreGrain: f.part.ignoreGrain, name: f.part.name };
      if (f.part.fixRot !== null && f.part.fixRot !== undefined) ov[f.part.key].fixRot = f.part.fixRot;
      selected = null; dropEmptySheets(); afterChange();
    },
    include: function (key) {
      var o = ov[key]; if (!o) return;
      delete o.excluded;
      setStatus('„' + (o.name || key) + '“ wird bei „Neu berechnen“ wieder platziert.');
      afterChange();
    },
    // „Abstände korrigieren“: rote Teile minimal auseinanderschieben (Drehung bleibt, fixierte zuletzt)
    fixSpacing: function () {
      var P = placer(); if (!P || !layout) return null;
      manualEdit();
      var moved = [], failed = [];
      layout.sheets.forEach(function (S) {
        var res = placer(S).correct(S.parts, function (p, x, y) { var np = rebuild(p, p.rot, p.flip, x, y); np.forced = false; return np; });
        moved = moved.concat(res.moved); failed = failed.concat(res.failed.length ? res.failed : res.remaining);
        S.parts.forEach(function (p) { if (p.pinned) setOv(p, S.index); });
      });
      afterChange();
      var nLeft = Object.keys(conflicts).length, dmax = moved.reduce(function (a, m) { return Math.max(a, m.dist); }, 0);
      if (!moved.length && !nLeft) setStatus('Keine Abstandsverletzungen.');
      else setStatus((moved.length ? moved.length + ' Teil(e) minimal verschoben (max. ' + fmt(dmax, 1) + ' mm)' : 'Nichts verschoben') +
        (nLeft ? ' – ' + nLeft + ' Teil(e) weiter rot: kein freier Platz in der Nähe, bitte manuell lösen.' : ' – alle Abstände eingehalten.'), nLeft > 0);
      return { moved: moved, left: nLeft };
    },
    select: function (key) { selected = key; draw(); updatePanel(); },
    deselect: function () { selected = null; draw(); updatePanel(); },
    // ---------------------------------------------------------------- Rohteile (1.10.0)
    selectSheet: function (si) { if (!layout || !layout.sheets[si]) return; selSheet = si; draw(); updateSheetPanel(); },
    // längs <-> quer: Blatt um 90° drehen, ALLE Teile (auch fixierte) drehen mit, Anordnung bleibt erhalten
    orient: function (si) {
      if (!layout) return false;
      si = (si === undefined) ? selSheet : si;
      var S = layout.sheets[si]; if (!S) return false;
      manualEdit();
      var gm = geomOf(S);
      S.parts = S.parts.map(function (p) {
        var q = E.turnPose(p, gm), np = rebuild(p, q.rot, p.flip, q.x, q.y);
        np.forced = !!p.forced;
        if (p.fixRot !== null && p.fixRot !== undefined) np.fixRot = q.rot;
        return np;
      });
      S.parked = (S.parked || []).map(function (p) {     // 2.0.5: geparkte Teile drehen mit (Faser bleibt stimmig)
        var q = E.turnPose(p, gm), np = rebuild(p, q.rot, p.flip, q.x, q.y);
        if (p.fixRot !== null && p.fixRot !== undefined) np.fixRot = q.rot;
        return np;
      });
      S.def.orient = gm.orient === 'quer' ? 'laengs' : 'quer';
      relayoutParking(S);
      S.parts.forEach(function (p) { var o = ov[p.key]; if (p.pinned || (o && o.fixRot !== undefined)) setOv(p, S.index); });
      (S.parked || []).forEach(function (p) { setOv(p, S.index); });
      selSheet = si; view = null; afterChange();
      setStatus(sheetName(S) + ' ' + (S.def.orient === 'quer' ? 'quer' : 'längs') + ' gelegt – alle ' + S.parts.length +
        ' Teile mitgedreht (Anordnung unverändert), Nullpunkt bleibt ' + E.zeroText(S.def.zero) + '. „Neu nesten“ ordnet dieses Rohteil neu an.');
      return true;
    },
    setZero: function (si, key) {
      var S = layout && layout.sheets[si]; if (!S || E.ZERO_KEYS.indexOf(key) < 0) return false;
      S.def.zero = key; selSheet = si; draw(); updateSheetPanel(); updateButtons();
      setStatus(sheetName(S) + ': Nullpunkt ' + E.zeroText(key) + '.');
      return true;
    },
    // Abschnitt/Standard + eigene Werte (Rechteck). Liefert Fehlertext oder ''.
    setSheetDef: function (si, d) {
      var S = layout && layout.sheets[si]; if (!S) return 'kein Rohteil';
      var nd = { id: S.def.id, kind: d.kind === 'abschnitt' ? 'abschnitt' : 'standard', orient: S.def.orient, zero: S.def.zero };
      if (nd.kind === 'abschnitt') {
        var base = geomOf(S);
        var v = function (k, min) { var x = (d[k] === undefined || d[k] === '') ? base[k] : parseFloat(d[k]); return (isFinite(x) && x >= min) ? x : null; };
        nd.length = v('length', 1); nd.width = v('width', 1); nd.frame = v('frame', 0); nd.thickness = v('thickness', 0.1);
        nd.grain = (d.grain === 'Y' || d.grain === 'X') ? d.grain : base.grain;
        if (d.material) nd.material = String(d.material).trim();
        if (nd.length === null || nd.width === null || nd.frame === null || nd.thickness === null) return 'Ungültige Abschnitt-Werte.';
        if (2 * nd.frame + layout.sheet.spacing >= Math.min(nd.length, nd.width)) return 'Randbesäumung/Teileabstand zu groß für den Abschnitt.';
      }
      manualEdit();
      S.def = nd; selSheet = si; view = null;
      afterChange();
      var bad = S.parts.filter(function (p) { return conflicts[p.key]; }).length;
      var gw = S.parts.filter(function (p) { return !grainOk(p); }).length;
      setStatus(labelOf(S) + (bad || gw ? ' – ' + (bad ? bad + ' Teil(e) außerhalb/zu nah' : '') + (bad && gw ? ', ' : '') +
        (gw ? gw + ' Teil(e) gegen die Faser' : '') + ': „Neu nesten“ ordnet das Rohteil neu an.' : ''), !!(bad || gw));
      return '';
    },
    moveSheet: function (si, dir) {
      var tj = si + dir;
      if (!layout || !layout.sheets[si] || !layout.sheets[tj]) return false;
      manualEdit();
      var a = layout.sheets[si]; layout.sheets[si] = layout.sheets[tj]; layout.sheets[tj] = a;
      numberSheets(); syncPinnedFromLayout(); selSheet = tj; view = null; afterChange();
      setStatus('Füllreihenfolge: ' + layout.sheets.map(function (S) { return S.def.kind === 'abschnitt' ? 'A' : 'S'; }).join(' → ') +
        ' (A = Abschnitt, S = Standard). Gilt für „Neu berechnen“.');
      return true;
    },
    addSheet: function (kind) {
      if (!layout) return false;
      manualEdit();
      layout.sheets.push({ index: layout.sheets.length + 1, utilisation: 0, parts: [], def: newDef(kind || 'abschnitt') });
      selSheet = layout.sheets.length - 1; view = null; afterChange();
      setStatus('Abschnitt angelegt – Maße eintragen, mit ▲ in der Füllreihenfolge nach vorn, dann „Neu berechnen“ (oder Teile hinüberschieben).');
      return true;
    },
    removeSheet: function (si) {
      var S = layout && layout.sheets[si]; if (!S || S.parts.length || (S.parked && S.parked.length)) return false;
      layout.sheets.splice(si, 1); numberSheets(); syncPinnedFromLayout();
      selSheet = Math.max(0, Math.min(selSheet, layout.sheets.length - 1)); view = null; afterChange();
      return true;
    },
    // „Neu nesten“ nur für dieses Blatt: seine Teile neu anordnen (fixierte bleiben), andere Blätter unverändert.
    // 2.0.5: geparkte Teile dieses Blatts sind freie Gene (Wenden + eingestellte Drehung bleiben); was nicht passt,
    // kommt zurück in die Parkzone (Teilergebnis) – nie weniger Teile auf der Platte als vorher.
    renestSheet: function (si, opt) {
      si = (si === undefined) ? selSheet : si;
      var S = layout && layout.sheets[si]; if (!S || !(S.parts.length || (S.parked && S.parked.length)) || running) return false;
      manualEdit(); syncPinnedFromLayout();
      var on = {}, sub = {}, recs = {};
      S.parts.forEach(function (p) { on[p.key] = p; recs[p.key] = p; });
      (S.parked || []).forEach(function (p) { on[p.key] = p; recs[p.key] = p; });
      var jj = JSON.parse(JSON.stringify(job)); delete jj.initialLayout;
      jj.parts.forEach(function (tp) {
        for (var k = 0; k < (tp.qty | 0); k++) {
          var key = tp.id + '#' + k, o = JSON.parse(JSON.stringify(ov[key] || {})), p = on[key];
          if (!p) o.excluded = true;
          else {
            delete o.excluded; o.flip = p.flip; o.grainMode = p.grainMode; o.ignoreGrain = p.ignoreGrain;
            if (p.pinned && !p.parked) { o.pinned = true; o.sheet = 1; o.x = p.x; o.y = p.y; o.rot = p.rot; }
            else { delete o.pinned; delete o.sheet; delete o.x; delete o.y; delete o.rot; }
            delete o.parked; delete o.parkDef;
          }
          sub[key] = o;
        }
      });
      jj.overrides = sub; jj.sheets = [JSON.parse(JSON.stringify(S.def))]; jj.maxSheets = 1;
      jj.timeLimit = Math.min(job.timeLimit || 60, (opt && opt.timeLimit) || 20); jj.seed = (job.seed || 1) * 31 + seed++;
      var sn;
      try { sn = new E.Nester(jj); } catch (e) { setStatus('Neu nesten: ' + e.message, true); return false; }
      // Maßstab „nie schlechter“: Teile, die vorher GÜLTIG (ohne Abstand-/Randverletzung) auf der Platte lagen
      var cf = {}; try { cf = E.layoutConflicts(sheetRecOf(S), S.parts); } catch (e) { cf = {}; }
      var nValid = S.parts.filter(function (p) { return !cf[p.key]; }).length;
      sheetJob = { sn: sn, def: S.def.id, n: nValid, nOn: S.parts.length, recs: recs, nPark: (S.parked || []).length };
      if (opt && opt.sync) { sn.runSync(); return finishSheetJob(); }
      running = true; updateButtons(); subTick();
      return true;
    }
  };
  function subTick() {
    if (!sheetJob) return;
    var more;
    try { more = sheetJob.sn.step(60); } catch (e) { sheetJob = null; running = false; setStatus('Neu nesten: ' + e.message, true); updateButtons(); return; }
    var st = sheetJob.sn.stats;
    $('bar').style.width = Math.min(100, (st.generation + 1) / sheetJob.sn.maxGen * 100) + '%';
    setStatus('Neu nesten (ein Rohteil) – Generation ' + (st.generation + 1) + '/' + sheetJob.sn.maxGen + ', ' + st.evaluated + ' Varianten');
    if (more) setTimeout(subTick, 0); else finishSheetJob();
  }
  function finishSheetJob() {
    var j = sheetJob; sheetJob = null; running = false;
    var si = -1;
    layout.sheets.forEach(function (S, i) { if (S.def.id === j.def) si = i; });
    var r = j.sn.best ? j.sn.result() : null, S = layout.sheets[si];
    var placed = (r && r.sheets.length) ? r.sheets[0].parts : [];
    // 2.0.5: Teilergebnis annehmen, wenn mindestens so viele Teile gültig auf der Platte liegen wie vorher
    var ok = !!(S && r && r.sheets.length && placed.length && placed.length >= j.n);
    var nLeft = 0, nDem = (j.sn.demoted || []).length;
    if (ok) {
      var got = {};
      placed.forEach(function (p) { got[p.key] = 1; });
      var rest = Object.keys(j.recs).filter(function (k) { return !got[k]; }).map(function (k) { return j.recs[k]; });
      S.parts = placed; S.parked = [];
      if (rest.length) parkRecords(S, rest);
      nLeft = rest.length;
      (j.sn.demoted || []).forEach(function (k) { if (ov[k] && got[k]) { delete ov[k].pinned; delete ov[k].sheet; delete ov[k].x; delete ov[k].y; delete ov[k].rot; } });
      S.parts.forEach(function (p) { if (j.sn.demoted && j.sn.demoted.indexOf(p.key) >= 0) p.pinned = false; });
      relayoutParking(S);
      syncPinnedFromLayout();
      $('bar').style.width = '100%';
    }
    afterChange();
    if (ok) setStatus(sheetName(S) + ' neu angeordnet (' + S.parts.length + ' Teile, Ausnutzung ' + fmt(S.utilisation * 100, 1) + ' %, fixierte Teile unverändert)' +
      (nLeft ? ' – ' + nLeft + ' Teil(e) passen nicht und sind geparkt (grau, nicht platziert).' : (j.nPark ? ' – alle geparkten Teile eingeordnet.' : '.')) +
      (nDem ? ' ' + nDem + ' ungültige Fixierung(en) (außerhalb/Kollision) neu platziert.' : ''), nLeft > 0);
    else {
      var nun = r ? r.unplaced.reduce(function (a, u) { return a + u.count; }, 0) : j.nOn;
      setStatus('Neu nesten ohne Verbesserung: ' + (nun || '?') + ' Teil(e) passen nicht auf ' + (S ? sheetName(S) : 'das Rohteil') +
        ' – Anordnung unverändert' + (j.nPark ? ', geparkte Teile bleiben geparkt' : '') + '.', true);
    }
    return ok;
  }
  function dropEmptySheets() {     // leere Standard-Blätter entfallen, leere Abschnitte bleiben (Definition)
    var keep = layout.sheets.filter(function (S) { return S.parts.length || (S.parked && S.parked.length) || (S.def && S.def.kind === 'abschnitt'); });
    if (keep.length !== layout.sheets.length) {
      layout.sheets = keep;
      numberSheets();
      syncPinnedFromLayout();
      selSheet = Math.min(selSheet, Math.max(0, layout.sheets.length - 1));
    }
  }

  // ================================================================ Zeichnen
  function svgEl(name, attrs, parent) {
    var e = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function ringD(pts) { return 'M' + pts.map(function (p) { return p[0].toFixed(2) + ',' + p[1].toFixed(2); }).join('L') + 'Z'; }
  function partD(p) { return ringD(p.outer) + p.holes.map(ringD).join(''); }
  var FEAT_COL = { top: '#0b5cd5', bottom: '#f07800' };
  function featView() { return (job && job.featView === 'all') ? 'all' : 'top'; }
  function setFeatView(v, persist) {
    if (!job) return;
    job.featView = v === 'all' ? 'all' : 'top';
    updateFeatViewUi();
    if (layout) draw();
    if (persist) sendToFusion('pref', { featView: job.featView });
  }
  function updateFeatViewUi() {
    var v = featView();
    $('fvTop').className = v === 'top' ? 'on' : ''; $('fvAll').className = v === 'all' ? 'on' : '';
    $('legBot').hidden = v === 'top';
    $('legTop').querySelector('i').className = 'sw ftop' + (v === 'top' ? ' plain' : '');
  }
  var SIDE_TXT = { top: 'oben', bottom: 'unten' };
  var PLURAL = { Tasche: 'Taschen', Nut: 'Nuten', Bohrung: 'Bohrungen' };
  function featureCounts(p) {
    var c = { top: {}, bottom: {} };
    (p.features || []).forEach(function (f) { c[f.side][f.kind] = (c[f.side][f.kind] || 0) + 1; });
    return c;
  }
  function sideText(cs) {
    var ks = Object.keys(cs);
    if (!ks.length) return '0';
    return ks.map(function (k) { return cs[k] + ' ' + (cs[k] === 1 ? k : (PLURAL[k] || k)); }).join(', ');
  }
  function featureText(p) {
    var c = featureCounts(p);
    return 'Bearbeitung oben: ' + sideText(c.top) + ', unten: ' + sideText(c.bottom);
  }
  // Durchbrüche (weiß) und Bearbeitungen (gestrichelt) eines Teils in eine Gruppe zeichnen
  function drawExtras(p, gx) {
    while (gx.firstChild) gx.removeChild(gx.firstChild);
    var L = geo.L;
    p.holes.forEach(function (h) {
      svgEl('path', { d: ringD(h), fill: '#ffffff', stroke: '#555', 'stroke-width': L / 1600, 'class': 'thru',
        'pointer-events': 'none' }, gx);
    });
    if (featView() === 'top') {
      // Standardansicht „Nur oben“: nur Bearbeitungen der Oberseite, schlichte dünne Kontur (nicht gestrichelt)
      (p.features || []).forEach(function (f) {
        if (f.side !== 'top') return;
        var d = ringD(f.pts) + (f.islands || []).map(ringD).join('');
        var e = svgEl('path', { d: d, fill: 'none', 'fill-rule': 'evenodd', stroke: '#1f3b70', 'stroke-width': L / 1400,
          'class': 'feat feat-top plain', 'data-key': p.key, 'data-side': 'top' }, gx);
        var t = svgEl('title', {}, e);
        t.textContent = p.name + ' #' + (p.copy + 1) + ': ' + f.kind + ' oben, Tiefe ' + fmt(f.depth, 1) + ' mm';
      });
      return;
    }
    // „Alle (gestrichelt)“: unten zuerst, oben darüber (oben ist die sichtbare Seite)
    ['bottom', 'top'].forEach(function (side) {
      (p.features || []).forEach(function (f) {
        if (f.side !== side) return;
        var d = ringD(f.pts) + (f.islands || []).map(ringD).join('');
        // heller Untergrund (Halo), damit die Strichelung auf jeder Teilfarbe lesbar ist
        svgEl('path', { d: d, fill: '#ffffff', 'fill-opacity': side === 'top' ? 0.55 : 0.3, 'fill-rule': 'evenodd',
          stroke: '#ffffff', 'stroke-width': L / 380, 'class': 'feathalo', 'pointer-events': 'none' }, gx);
        var e = svgEl('path', { d: d, fill: FEAT_COL[side], 'fill-opacity': side === 'top' ? 0.18 : 0.12,
          'fill-rule': 'evenodd', stroke: FEAT_COL[side], 'stroke-width': L / 600,
          'stroke-dasharray': (L / 160) + ' ' + (L / 260), 'class': 'feat feat-' + side,
          'data-key': p.key, 'data-side': side }, gx);
        var t = svgEl('title', {}, e);
        t.textContent = p.name + ' #' + (p.copy + 1) + ': ' + f.kind + ' ' + SIDE_TXT[side] + ', Tiefe ' + fmt(f.depth, 1) + ' mm';
      });
    });
  }

  function computeGeo() {      // 1.10.0: Maß je Blatt (Abschnitt/quer) – untereinander, links bündig
    var gms = layout.sheets.map(geomOf);
    // 2.0.5: Parkzone rechts neben dem Blatt mitzählen
    var L = Math.max.apply(null, gms.map(function (g, i) { var S = layout.sheets[i]; return g.W + ((S.parked && S.parked.length) ? (S.parkW || relayoutParking(S)) : 0); })
      .concat([layout.sheet.length || 1, layout.sheet.width || 1]));
    var B = Math.max.apply(null, gms.map(function (g) { return g.H; }).concat([1]));
    var fs = Math.max(14, L / 45), top = fs * 1.6, gap = Math.max(40, B * 0.06) + top, y = 0;
    var offs = gms.map(function (g) { var o = y + top; y += g.H + gap; return o; });
    return { L: L, B: B, fs: fs, offs: offs, gms: gms, H: y };
  }
  function fitView() { view = { x: -10, y: 0, w: geo.L + 20, h: geo.H + 10 }; applyView(); }
  function applyView() { var s = $('view').querySelector('svg'); if (s) s.setAttribute('viewBox', [view.x, view.y, view.w, view.h].join(' ')); }

  function draw() {
    var host = $('view'); host.innerHTML = '';
    if (!layout) return;
    geo = computeGeo();
    var L = geo.L;
    var ids = job.parts.map(function (p) { return p.id; });
    var svg = svgEl('svg', { preserveAspectRatio: 'xMidYMin meet' }, host);
    if (!view) fitView(); else applyView();
    svg.setAttribute('viewBox', [view.x, view.y, view.w, view.h].join(' '));
    var defs = svgEl('defs', {}, svg);
    var m = svgEl('marker', { id: 'arr', markerWidth: 4, markerHeight: 4, refX: 2, refY: 2, orient: 'auto' }, defs);
    svgEl('path', { d: 'M0,0L4,2L0,4Z', fill: '#8b4513' }, m);
    var labels = [];
    var mx = svgEl('marker', { id: 'arrX', markerWidth: 4, markerHeight: 4, refX: 2, refY: 2, orient: 'auto' }, defs);
    svgEl('path', { d: 'M0,0L4,2L0,4Z', fill: '#d00000' }, mx);
    var my = svgEl('marker', { id: 'arrY', markerWidth: 4, markerHeight: 4, refX: 2, refY: 2, orient: 'auto' }, defs);
    svgEl('path', { d: 'M0,0L4,2L0,4Z', fill: '#008800' }, my);
    layout.sheets.forEach(function (S, i) {
      var oy = geo.offs[i], gm = geo.gms[i], W = gm.W, B = gm.H, f = gm.frame;
      var g = svgEl('g', { transform: 'translate(0,' + (oy + B) + ') scale(1,-1)', 'data-sheet': i }, svg);
      svgEl('rect', { x: 0, y: 0, width: W, height: B, fill: gm.kind === 'abschnitt' ? '#eadcc0' : '#f3e5c8',
        stroke: i === selSheet ? '#0a6ebd' : '#333', 'stroke-width': i === selSheet ? L / 350 : L / 600, 'class': 'sheetbg', 'data-sheetbg': i }, g);
      if (f > 0) svgEl('rect', { x: f, y: f, width: W - 2 * f, height: B - 2 * f, fill: 'none', stroke: '#a07040', 'stroke-dasharray': L / 150, 'stroke-width': L / 900, 'pointer-events': 'none' }, g);
      var gy = gm.grainAxis === 'Y';
      var a = gy ? 'M' + (W * 0.03) + ',' + (B * 0.1) + 'L' + (W * 0.03) + ',' + (B * 0.3) : 'M' + (W * 0.05) + ',' + (B * 0.04) + 'L' + (W * 0.2) + ',' + (B * 0.04);
      svgEl('path', { d: a, stroke: '#8b4513', 'stroke-width': L / 250, fill: 'none', 'marker-end': 'url(#arr)', 'pointer-events': 'none', 'class': 'grain', 'data-axis': gm.grainAxis }, g);
      S.parts.forEach(function (p) {
        var col = COLORS[ids.indexOf(p.id) % COLORS.length];
        var conf = conflicts[p.key], sel = p.key === selected;
        var stroke = sel ? '#0a6ebd' : conf ? '#d00000' : p.critical ? '#d00000' : (p.small ? '#e08000' : '#222');
        var sw = sel ? L / 220 : (conf || p.small || p.critical ? L / 320 : L / 1000);
        var path = svgEl('path', { d: partD(p), fill: conf ? '#ff8080' : col, 'fill-opacity': 0.82, 'fill-rule': 'evenodd',
          stroke: stroke, 'stroke-width': sw, 'class': 'part', 'data-key': p.key, 'data-sheet': i }, g);
        var t = svgEl('title', {}, path);
        t.textContent = p.name + ' #' + (p.copy + 1) + ' – ' + fmt(p.rot, 1) + '°' + (p.flip ? ' – gewendet' : '') +
          (p.pinned ? ' – fixiert' : '') +
          ((p.fixRot !== null && p.fixRot !== undefined && !p.pinned) ? ' – Drehung fest' : '') + (conf ? ' – KONFLIKT: ' + conf + (p.forced ? ' (mit Strg übersteuert)' : '') : '') +
          ((p.features && p.features.length) ? ' – ' + featureText(p) : '');
        if (p.holes.length || (p.features && p.features.length))
          drawExtras(p, svgEl('g', { 'class': 'extras', 'data-extras': p.key }, g));
        var fr = !p.pinned && p.fixRot !== null && p.fixRot !== undefined;
        if (p.pinned || p.flip || fr) labels.push({ x: p.cx, y: oy + B - p.cy, key: p.key,
          t: (p.pinned ? '📌' : '') + (p.flip ? '⇅' : '') + (fr ? '∠' + fmt(p.fixRot, 0) + '°' : '') });
      });
      // 2.0.5: Parkzone – grau, gestrichelt, nicht platziert (kein Konflikt-Rot)
      if (S.parked && S.parked.length) {
        var pgap = parkGap(gm), pw = S.parkW || relayoutParking(S);
        svgEl('rect', { x: W + pgap * 0.5, y: 0, width: Math.max(1, pw - pgap * 0.5), height: B, fill: '#ececec', 'fill-opacity': 0.6,
          stroke: '#9a9a9a', 'stroke-dasharray': L / 150, 'stroke-width': L / 900, 'class': 'parkzone', 'data-parkzone': i, 'pointer-events': 'none' }, g);
        S.parked.forEach(function (p) {
          var sel = p.key === selected;
          var path = svgEl('path', { d: partD(p), fill: '#b9b9b9', 'fill-opacity': 0.85, 'fill-rule': 'evenodd',
            stroke: sel ? '#0a6ebd' : '#6b6b6b', 'stroke-width': sel ? L / 220 : L / 900, 'stroke-dasharray': sel ? 'none' : L / 300,
            'class': 'part parked', 'data-key': p.key, 'data-sheet': i, 'data-parked': 1 }, g);
          svgEl('title', {}, path).textContent = p.name + ' #' + (p.copy + 1) + ' – ' + fmt(p.rot, 1) + '°' + (p.flip ? ' – gewendet' : '') +
            ' – nicht platziert (' + PARK_TXT + ')';
          labels.push({ x: p.cx, y: oy + B - p.cy, key: p.key, t: 'P' + (p.flip ? '⇅' : '') +
            ((p.fixRot !== null && p.fixRot !== undefined) ? '∠' + fmt(p.fixRot, 0) + '°' : '') });
        });
        var pl = svgEl('text', { x: W + pgap * 0.5, y: oy + B + geo.fs * 1.0, 'font-size': geo.fs * 0.7, fill: '#666', 'class': 'parklabel', 'data-sheet': i }, svg);
        pl.textContent = 'Parkzone: ' + S.parked.length + ' Teil(e) nicht platziert';
      }
      drawZero(S, i, gm, g);
      var label = svgEl('text', { x: 0, y: oy - geo.fs * 0.4, 'font-size': geo.fs * 0.8, fill: '#333', 'class': 'sheetlabel', 'data-sheetbg': i }, svg);
      label.textContent = labelOf(S) + ' – ' + fmt(S.utilisation * 100, 1) + ' % – ' + S.parts.length + ' Teile' +
        ((S.parked && S.parked.length) ? ' (+' + S.parked.length + ' geparkt)' : '');
      var tb = svgEl('text', { x: W, y: oy - geo.fs * 0.4, 'font-size': geo.fs * 0.8, fill: '#0a6ebd', 'text-anchor': 'end',
        'class': 'sheetbtn', 'data-act': 'orient', 'data-sheet': i, style: 'cursor:pointer' }, svg);
      tb.textContent = '⟲ ' + (gm.orient === 'quer' ? 'quer → längs' : 'längs → quer');
      svgEl('title', {}, tb).textContent = 'Rohteil um 90° drehen – alle Teile drehen mit';
      fitLabel(label, tb, W);
    });
    labels.forEach(function (l) {
      var tx = svgEl('text', { x: l.x, y: l.y, 'font-size': geo.fs * 0.9, 'text-anchor': 'middle', 'dominant-baseline': 'middle', 'pointer-events': 'none',
        'class': 'badge', 'data-key': l.key }, svg);
      tx.textContent = l.t;
    });
    bindSvg(svg);
  }

  // Nullpunkt: Achsenkreuz (X rot, Y grün) + Kreuz an der gewählten Stelle, Wahlmarken an Ecken/Mitte
  // Beschriftung + Dreh-Schalter in die Blattbreite einpassen (schmale/quer liegende Blätter)
  function fitLabel(label, tb, W) {
    try {
      var lw = label.getComputedTextLength(), bw = tb.getComputedTextLength(), room = W - bw - W * 0.02;
      if (!(lw > 0) || lw <= room) return;
      if (room < W * 0.35) {               // sehr schmal: Schalter in eine eigene Zeile darüber
        tb.setAttribute('y', parseFloat(label.getAttribute('y')) - parseFloat(label.getAttribute('font-size')) * 1.15);
        room = W;
      }
      var f = Math.max(0.55, room / lw);
      label.setAttribute('font-size', parseFloat(label.getAttribute('font-size')) * f);
      if (lw * f > room) { label.setAttribute('textLength', room); label.setAttribute('lengthAdjust', 'spacingAndGlyphs'); }
    } catch (e) { /* ohne Layout (Tests) */ }
  }
  function drawZero(S, i, gm, g) {
    var z = E.zeroPoint(gm), L = geo.L, a = Math.max(40, Math.min(gm.W, gm.H) * 0.12), r = L / 160;
    var zg = svgEl('g', { 'class': 'zero', 'data-zero': z.key, 'data-sheet': i, 'pointer-events': 'none' }, g);
    svgEl('path', { d: 'M' + z.x + ',' + z.y + 'L' + (z.x + a) + ',' + z.y, stroke: '#d00000', 'stroke-width': L / 450, 'marker-end': 'url(#arrX)', 'class': 'zx' }, zg);
    svgEl('path', { d: 'M' + z.x + ',' + z.y + 'L' + z.x + ',' + (z.y + a), stroke: '#008800', 'stroke-width': L / 450, 'marker-end': 'url(#arrY)', 'class': 'zy' }, zg);
    svgEl('circle', { cx: z.x, cy: z.y, r: r * 1.6, fill: z.side === 'oben' ? '#ffffff' : 'none', stroke: '#000', 'stroke-width': L / 900,
      'stroke-dasharray': z.side === 'oben' ? 'none' : (L / 500) + ' ' + (L / 900) }, zg);
    svgEl('path', { d: 'M' + (z.x - r * 2.4) + ',' + z.y + 'L' + (z.x + r * 2.4) + ',' + z.y + 'M' + z.x + ',' + (z.y - r * 2.4) + 'L' + z.x + ',' + (z.y + r * 2.4),
      stroke: '#000', 'stroke-width': L / 900 }, zg);
    [['lu', 0, 0], ['ru', gm.W, 0], ['lh', 0, gm.H], ['rh', gm.W, gm.H], ['mitte', gm.W / 2, gm.H / 2]].forEach(function (c) {
      var m = svgEl('circle', { cx: c[1], cy: c[2], r: r * (c[0] === z.corner ? 1.2 : 1), fill: c[0] === z.corner ? '#0a6ebd' : '#ffffff',
        'fill-opacity': 0.85, stroke: '#0a6ebd', 'stroke-width': L / 1200, 'class': 'zmark', 'data-sheet': i, 'data-corner': c[0], style: 'cursor:pointer' }, g);
      svgEl('title', {}, m).textContent = 'Nullpunkt hierher: ' + E.ZERO_CORNER_TXT[c[0]] + ' (' + E.ZERO_SIDE_TXT[z.side] + ')';
    });
  }

  // ================================================================ Maus: Auswahl, Ziehen, Zoom, Pan
  function sheetPoint(gEl, ev) {
    var pt = gEl.ownerSVGElement.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    return pt.matrixTransform(gEl.getScreenCTM().inverse());
  }
  function rootPoint(svg, ev) {
    var pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  }
  // welches Rohteil liegt unter dem Zeiger? (SVG-Wurzelkoordinaten, Platte + Parkzone rechts)
  function sheetHitFromEvent(ev) {
    if (!ev || !geo || !layout) return null;
    var svg = $('view') && $('view').querySelector('svg');
    if (!svg || !svg.createSVGPoint) return null;
    var r;
    try { r = rootPoint(svg, ev); } catch (e) { return null; }
    var found = null;
    for (var i = 0; i < layout.sheets.length; i++) {
      var oy = geo.offs[i], gm = geo.gms[i], W = gm.W, H = gm.H, S = layout.sheets[i];
      var extra = Math.max(S.parkW || 0, parkGap(gm));
      if (r.y >= oy && r.y <= oy + H && r.x >= 0 && r.x <= W + extra)
        found = { si: i, parked: r.x > W, localX: r.x, localY: (oy + H) - r.y };
    }
    return found;
  }
  function bindSvg(svg) {
    svg.addEventListener('pointerdown', function (ev) {
      var tgt = ev.target, key = tgt.getAttribute && tgt.getAttribute('data-key');
      var corner = tgt.getAttribute && tgt.getAttribute('data-corner');
      if (corner) {                          // Nullpunkt-Marke: Ecke/Mitte wählen (Seite bleibt)
        var zi = parseInt(tgt.getAttribute('data-sheet'), 10), zs = layout.sheets[zi];
        actions.setZero(zi, corner + '_' + ((zs.def.zero || 'lu_unten').split('_')[1] || 'unten'));
        ev.preventDefault(); return;
      }
      if (tgt.getAttribute && tgt.getAttribute('data-act') === 'orient') {
        actions.orient(parseInt(tgt.getAttribute('data-sheet'), 10)); ev.preventDefault(); return;
      }
      if (ev.button === 2) {                 // Rechtsklick: Teil wählen, kein Ziehen (Menü kommt über contextmenu)
        if (key) { selected = key; updatePanel(); highlight(tgt.classList && tgt.classList.contains('part') ? tgt : (tgt.parentNode && tgt.parentNode.querySelector && tgt.parentNode.querySelector('path.part[data-key="' + key + '"]')) || tgt); }
        return;
      }
      if (ev.button !== 0) return;
      hideCtx();
      try { svg.setPointerCapture(ev.pointerId); } catch (e) { /* synthetische Events */ }
      if (key) {
        if (selected !== key) { selected = key; updatePanel(); }
        var f = findPart(key), g = tgt.parentNode;
        if (g.getAttribute('data-extras')) g = g.parentNode;          // Klick auf eine Bearbeitung
        var main = g.querySelector('path.part[data-key="' + key + '"]') || tgt;
        var p0 = sheetPoint(g, ev);
        drag = { mode: 'part', key: key, g: g, start: p0, x0: f.part.x, y0: f.part.y, moved: false, path: main,
                 extras: g.querySelector('g[data-extras="' + key + '"]') };
        highlight(main);
      } else {
        var sb = tgt.getAttribute && tgt.getAttribute('data-sheetbg');
        drag = { mode: 'pan', start: rootPoint(svg, ev), v0: { x: view.x, y: view.y }, moved: false,
                 sheet: (sb !== null && sb !== undefined && sb !== '') ? parseInt(sb, 10) : null };
      }
      ev.preventDefault();
    });
    svg.addEventListener('pointermove', function (ev) {
      if (!drag) return;
      if (drag.mode === 'part') {
        var p = sheetPoint(drag.g, ev), dx = p.x - drag.start.x, dy = p.y - drag.start.y;
        if (!drag.moved && Math.hypot(dx, dy) < geo.L / 400) return;
        if (!drag.moved) { hideCtx(); manualEdit(); startZones(drag); }
        drag.moved = true; drag.lastEv = ev;
        drag.want = drag.lastWant = { x: drag.x0 + dx, y: drag.y0 + dy, ctrl: !!(ev.ctrlKey || ev.metaKey) };
        scheduleMove();
      } else {
        var r = rootPoint(ev.currentTarget, ev);
        // Pan: viewBox so verschieben, dass der Startpunkt unter der Maus bleibt
        view.x -= r.x - drag.start.x; view.y -= r.y - drag.start.y;
        drag.moved = true; applyView();
      }
    });
    function end(ev) {
      if (!drag) return;
      if (drag.mode === 'part' && drag.want) processMove();       // letzte Bewegung nicht verlieren
      var d = drag; drag = null;
      if (d.mode === 'part') {
        var f = findPart(d.key);
        var evHit = ev || d.lastEv;
        var hit = (d.moved && evHit) ? sheetHitFromEvent(evHit) : null;
        if (d.moved && f && hit && hit.si !== f.si) {
          selected = d.key;
          var svg0 = $('view').querySelector('svg');
          var tgtG = svg0 && svg0.querySelector('g[data-sheet="' + hit.si + '"]');
          var pt = (tgtG && evHit) ? sheetPoint(tgtG, evHit) : { x: hit.localX, y: hit.localY };
          actions.toSheet(layout.sheets[hit.si].index, {
            x: pt.x - (d.start.x - d.x0), y: pt.y - (d.start.y - d.y0),
            forced: !!d.forcedNow, park: !!hit.parked
          });
          return;
        }
        var gmE = d.moved ? geomOf(f.sheet) : null, fp = f.part;
        if (d.moved && !(fp.cx >= 0 && fp.cx <= gmE.W && fp.cy >= 0 && fp.cy <= gmE.H)) {
          // 2.0.5: neben die Platte gezogen (oder geparktes Teil ohne Platz) -> parken
          var wasParked = f.parked;
          parkPart(f); f.part.fixRot = fp.fixRot;
          setOv(f.part, f.sheet.index);
          afterChange();
          setStatus((wasParked ? 'Teil bleibt geparkt' : 'Teil neben die Platte gelegt') + ' – nicht platziert; „Neu nesten“ ordnet es ein (oder auf die Platte ziehen).', true);
          if (f.si !== selSheet) { selSheet = f.si; updateSheetPanel(); }
        } else if (d.moved) {
          var unparked = false;
          if (f.parked) { unparkPart(f, fp.x, fp.y); unparked = true; }
          f.part.pinned = true;
          f.part.forced = !!(d.forcedNow);
          setOv(f.part, f.sheet.index);
          afterChange();
          if (conflicts[d.key] && f.part.forced) setStatus('Mit Strg bewusst in die Sperrzone gelegt – Teil bleibt rot; „Übernehmen“ warnt. „Abstände korrigieren“ schiebt es frei.', true);
          else if (conflicts[d.key]) setStatus('Teil verschoben und fixiert – liegt noch in einer Sperrzone (kein freier Platz).', true);
          else setStatus((unparked ? 'Geparktes Teil auf die Platte gelegt und fixiert' : 'Teil verschoben und fixiert') + (d.slid ? ' (an der Sperrzone ausgerichtet, Abstand ' + fmt(layout.sheet.spacing, 1) + ' mm).' : '.'));
          if (f.si !== selSheet) { selSheet = f.si; updateSheetPanel(); }
        } else { draw(); }
      } else if (!d.moved) {
        if (d.sheet !== null && d.sheet !== undefined && d.sheet !== selSheet) { selSheet = d.sheet; updateSheetPanel(); }
        actions.deselect();
      }
    }
    svg.addEventListener('pointerup', end);
    svg.addEventListener('pointercancel', end);
    svg.addEventListener('wheel', function (ev) {
      ev.preventDefault();
      var r = rootPoint(svg, ev), k = ev.deltaY > 0 ? 1.15 : 1 / 1.15;
      view.x = r.x - (r.x - view.x) * k; view.y = r.y - (r.y - view.y) * k; view.w *= k; view.h *= k;
      applyView();
    }, { passive: false });
    svg.addEventListener('dblclick', function (ev) {
      var key = ev.target.getAttribute && ev.target.getAttribute('data-key');
      if (key) zoomTo(key); else fitView();
    });
    svg.addEventListener('contextmenu', function (ev) {
      var key = ev.target.getAttribute && ev.target.getAttribute('data-key');
      if (!key) return;                       // leere Fläche: kein Menü (Browser-Menü bleibt)
      ev.preventDefault(); ev.stopPropagation();
      if (drag && drag.mode === 'part' && drag.moved) return;
      if (selected !== key) { selected = key; updatePanel(); }
      showCtxMenu(ev);
    });
  }
  // ---------------------------------------------------------------- Ziehen mit Sperrzonen
  var rafId = null;
  var raf = window.requestAnimationFrame ? function (fn) { return window.requestAnimationFrame(fn); } : function (fn) { return setTimeout(fn, 16); };
  function scheduleMove() {
    if (rafId !== null) return;
    rafId = raf(function () { rafId = null; if (drag && drag.want) processMove(); });
  }
  function startZones(d) {
    var f = findPart(d.key), P = placer(f.sheet);
    d.ses = P ? P.session(f.sheet.parts, poseOf(f.part)) : null;
    d.color = COLORS[job.parts.map(function (x) { return x.id; }).indexOf(f.part.id) % COLORS.length];
    // dünne Linie: Sperrzonen der Nachbarn (Kontur + Teileabstand) und Randlinie (Besäumung + ½ Abstand)
    var zg = svgEl('g', { 'class': 'zones', 'pointer-events': 'none' }, d.g), sw = geo.L / 1500;
    if (P) {
      f.sheet.parts.forEach(function (q) {
        if (q.key === d.key) return;
        var rings = zoneOf(q);
        if (rings.length) svgEl('path', { d: rings.map(ringD).join(''), fill: 'none', stroke: '#c03030', 'stroke-width': sw,
          'stroke-dasharray': (geo.L / 300) + ' ' + (geo.L / 500), 'class': 'zone', 'data-zone': q.key }, zg);
      });
      svgEl('path', { d: ringD(P.edgeLine()), fill: 'none', stroke: '#c03030', 'stroke-width': sw,
        'stroke-dasharray': (geo.L / 300) + ' ' + (geo.L / 500), 'class': 'zone edge' }, zg);
    }
    d.zones = zg;
    // gezogenes Teil über den Zonen zeichnen
    d.g.appendChild(d.path); if (d.extras) d.g.appendChild(d.extras);
  }
  function processMove() {
    var d = drag; if (!d || !d.want) return;
    var w = d.want; d.want = null;
    var f = findPart(d.key), q = f.part, x = w.x, y = w.y, touch = false, txt = '', warnTxt = false;
    // 2.0.5: Schwerpunkt außerhalb der Platte -> frei ziehen, Loslassen parkt das Teil (nicht platziert)
    var gmo = geomOf(f.sheet), w0 = rebuild(q, q.rot, q.flip, x, y);
    d.outside = !(w0.cx >= 0 && w0.cx <= gmo.W && w0.cy >= 0 && w0.cy <= gmo.H);
    var hover = d.lastEv ? sheetHitFromEvent(d.lastEv) : null;
    if (hover && hover.si !== f.si) {
      d.outside = false; d.forcedNow = false; d.slid = false;
      replacePart(f, w0);
      d.path.setAttribute('d', partD(w0));
      if (d.extras) drawExtras(w0, d.extras);
      if (hover.parked) {
        d.path.setAttribute('fill', '#b9b9b9');
        if (d.path.getAttribute('class') !== 'part parked') d.path.setAttribute('class', 'part parked');
        setStatus('Loslassen = auf ' + sheetName(layout.sheets[hover.si]) + ' parken (nicht platziert).', true);
      } else {
        d.path.setAttribute('fill', d.color);
        if (d.path.getAttribute('class') !== 'part') d.path.setAttribute('class', 'part');
        setStatus('Loslassen = auf ' + sheetName(layout.sheets[hover.si]) + ' verschieben.');
      }
      return;
    }
    if (d.outside) {
      d.forcedNow = false; d.slid = false;
      replacePart(f, w0);
      d.path.setAttribute('d', partD(w0));
      if (d.extras) drawExtras(w0, d.extras);
      d.path.setAttribute('fill', '#b9b9b9');
      if (d.path.getAttribute('class') !== 'part parked') d.path.setAttribute('class', 'part parked');
      setStatus('Neben der Platte – Loslassen = parken (nicht platziert, „Neu nesten“ ordnet es ein).', true);
      return;
    }
    if (w.ctrl || !d.ses) {
      d.forcedNow = true;
    } else {
      var r = d.ses.project(x, y);
      d.forcedNow = false;
      if (!r.ok) { x = q.x; y = q.y; touch = true; warnTxt = true;
        txt = 'Kein freier Platz für dieses Teil auf diesem Rohteil – Strg halten zum freien Verschieben.'; }
      else if (!r.free) { x = r.x; y = r.y; touch = true; d.slid = true;
        txt = 'An der Sperrzone: gleitet entlang (Abstand ' + fmt(layout.sheet.spacing, 1) + ' mm, Rand ' + fmt(geomOf(f.sheet).frame, 1) + ' mm) – Strg = frei'; }
      else { d.slid = false; txt = 'Verschieben … (wird fixiert) – Strg halten, um die Abstandssperre zu übersteuern'; }
    }
    var np = rebuild(q, q.rot, q.flip, x, y);
    np.forced = d.forcedNow;
    replacePart(f, np);
    d.path.setAttribute('d', partD(np));
    if (d.extras) drawExtras(np, d.extras);
    if (d.forcedNow) {
      var c = E.layoutConflicts(sheetRecOf(f.sheet), f.sheet.parts, d.key);   // schnelle Prüfung nur für dieses Teil
      touch = !!c[d.key];
      txt = c[d.key] ? 'Strg: Sperre übersteuert – ' + (c[d.key] === 'Rand' ? 'Randbesäumung' : 'Teileabstand') + ' verletzt (Teil bleibt rot)' :
        'Strg: frei verschieben (keine Verletzung)';
      warnTxt = touch;
    }
    d.path.setAttribute('fill', touch ? '#ff8080' : d.color);
    var cls = 'part' + (touch ? ' touch' : '');
    if (d.path.getAttribute('class') !== cls) d.path.setAttribute('class', cls);
    setStatus(txt, warnTxt);
  }
  document.addEventListener('keydown', ctrlChange); document.addEventListener('keyup', ctrlChange);
  function ctrlChange(ev) {       // Strg während des Ziehens drücken/loslassen wirkt sofort
    if (ev.key !== 'Control' || !drag || drag.mode !== 'part' || !drag.moved) return;
    var f = findPart(drag.key);
    drag.want = drag.lastWant || { x: f.part.x, y: f.part.y };
    drag.want.ctrl = ev.type === 'keydown';
    scheduleMove();
  }
  function hideCtx() { if (ctxEl) ctxEl.hidden = true; }
  function ensureCtx() {
    if (ctxEl) return ctxEl;
    ctxEl = $('partCtx') || document.createElement('div');
    if (!ctxEl.id) ctxEl.id = 'partCtx';
    ctxEl.className = 'ctxmenu';
    ctxEl.setAttribute('role', 'menu');
    ctxEl.hidden = true;
    if (!ctxEl.parentNode) document.body.appendChild(ctxEl);
    ctxEl.addEventListener('contextmenu', function (e) { e.preventDefault(); });
    ctxEl.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
    ctxEl.addEventListener('mousedown', function (e) { e.stopPropagation(); });
    document.addEventListener('pointerdown', function (e) {
      if (ctxEl && !ctxEl.hidden && !ctxEl.contains(e.target)) hideCtx();
    });
    return ctxEl;
  }
  function ctxItem(label, hk, fn, disabled) {
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'ctxitem'; b.disabled = !!disabled;
    var s = document.createElement('span'); s.textContent = label; b.appendChild(s);
    if (hk) { var h = document.createElement('span'); h.className = 'hk'; h.textContent = hk; b.appendChild(h); }
    b.onclick = function () { hideCtx(); if (fn) fn(); };
    return b;
  }
  function ctxSep() { var d = document.createElement('div'); d.className = 'ctxsep'; return d; }
  function showCtxMenu(ev) {
    var f = selected && findPart(selected); if (!f) return;
    var el = ensureCtx(), p = f.part;
    el.innerHTML = '';
    el.appendChild(ctxItem('Drehen +', 'R', function () { actions.rotate(1); }));
    el.appendChild(ctxItem('Drehen −', '⇧R', function () { actions.rotate(-1); }));
    var row = document.createElement('div'); row.className = 'ctxangle';
    var lab = document.createElement('span'); lab.textContent = 'Winkel'; row.appendChild(lab);
    var inp = document.createElement('input'); inp.type = 'number'; inp.step = '1'; inp.id = 'ctxAngle';
    inp.value = Math.round(p.rot * 10) / 10; row.appendChild(inp);
    var deg = document.createElement('span'); deg.textContent = '°'; row.appendChild(deg);
    var ok = document.createElement('button'); ok.type = 'button'; ok.textContent = 'OK';
    function applyAng() { var a = parseFloat(inp.value); hideCtx(); actions.setAngle(a); }
    ok.onclick = applyAng;
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); applyAng(); }
      e.stopPropagation();
    });
    row.appendChild(ok); el.appendChild(row);
    el.appendChild(ctxItem('Wenden', 'F', function () { actions.flip(); }));
    el.appendChild(ctxItem(p.pinned ? 'Fixierung lösen' : 'Fixieren', 'P', function () { actions.pin(); }, !!f.parked));
    el.appendChild(ctxSep());
    var wrap = document.createElement('div'); wrap.className = 'ctxsub';
    var subBtn = ctxItem('Auf Rohteil', '▸', null, false);
    subBtn.className = 'ctxitem ctxsubbtn';
    subBtn.onclick = function (e) { e.preventDefault(); };
    wrap.appendChild(subBtn);
    var sub = document.createElement('div'); sub.className = 'ctxsubmenu';
    layout.sheets.forEach(function (S) {
      var extra = (S.index === f.sheet.index ? ' (aktuell)' : '') + (S.def.kind === 'abschnitt' ? ' (Abschnitt)' : '') +
        (S.def.orient === 'quer' ? ' quer' : '');
      var sb = ctxItem(sheetName(S) + extra, '', function () { actions.toSheet(S.index); }, S.index === f.sheet.index && !f.parked);
      sub.appendChild(sb);
    });
    if (layout.sheets.length < Math.max(job.maxSheets || 1, layout.sheets.length + 1))
      sub.appendChild(ctxItem('neues Rohteil', '', function () { actions.toSheet('new'); }));
    wrap.appendChild(sub); el.appendChild(wrap);
    el.appendChild(ctxSep());
    el.appendChild(ctxItem((p.grainMode === 'laengs' ? '✓ ' : '') + 'Faser: längs', '', function () { actions.grainMode('laengs'); }));
    el.appendChild(ctxItem((p.grainMode === 'quer' ? '✓ ' : '') + 'Faser: quer', '', function () { actions.grainMode('quer'); }));
    el.appendChild(ctxItem((p.grainMode === 'frei' ? '✓ ' : '') + 'Faser: frei', '', function () { actions.grainMode('frei'); }));
    el.appendChild(ctxItem((p.ignoreGrain ? '✓ ' : '') + 'Faser ignorieren', '', function () { actions.ignoreGrain(!p.ignoreGrain); }));
    if (p.fixRot !== null && p.fixRot !== undefined)
      el.appendChild(ctxItem('Drehung freigeben', '', function () { actions.releaseRot(); }));
    el.appendChild(ctxSep());
    el.appendChild(ctxItem('Ausschließen', 'Entf', function () { actions.exclude(); }));
    el.style.left = '0px'; el.style.top = '0px'; el.hidden = false;
    var w = el.offsetWidth || 220, h = el.offsetHeight || 280;
    var vx = window.innerWidth || 800, vy = window.innerHeight || 600;
    var x = ev.clientX, y = ev.clientY;
    el.style.left = Math.max(4, Math.min(x, vx - w - 4)) + 'px';
    el.style.top = Math.max(4, Math.min(y, vy - h - 4)) + 'px';
    if (x + w + 200 > vx) wrap.classList.add('open-left');
  }
  function highlight(path) {
    Array.prototype.forEach.call($('view').querySelectorAll('path.part'), function (p) {
      var on = p === path;
      p.setAttribute('stroke', on ? '#0a6ebd' : p.getAttribute('stroke') === '#0a6ebd' ? '#222' : p.getAttribute('stroke'));
      if (on) p.setAttribute('stroke-width', geo.L / 220);
    });
  }
  function zoomTo(key) {
    var f = findPart(key); if (!f) return;
    var xs = f.part.outer.map(function (p) { return p[0]; }), ys = f.part.outer.map(function (p) { return p[1]; });
    var oy = geo.offs[f.si], B = geo.gms[f.si].H;
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), y0 = oy + B - Math.max.apply(null, ys), y1 = oy + B - Math.min.apply(null, ys);
    var m = Math.max(x1 - x0, y1 - y0) * 0.6 + 20;
    view = { x: x0 - m, y: y0 - m, w: x1 - x0 + 2 * m, h: y1 - y0 + 2 * m }; applyView();
  }

  // ================================================================ Panels
  function updatePanel() {
    var f = selected && findPart(selected);
    $('partPanel').hidden = !f; $('ppNone').hidden = !!f;
    $('ppWarn').textContent = '';
    if (f) {
      var p = f.part, t = typeOf(p);
      $('ppTitle').textContent = p.name + ' #' + (p.copy + 1);
      $('ppInfo').textContent = fmt(p.area / 100, 0) + ' cm² · Drehung ' + fmt(p.rot, 1) + '° · Teilfaser ' + fmt(t.grainAngle, 0) +
        '° · Abstand Mitte ' + fmt(p.dist, 0) + ' mm' + (p.critical ? ' · unter Mindestfläche Vakuum!' : '');
      $('ppStep').textContent = '± ' + fmt(stepOf(p), 0) + '°';
      if (document.activeElement !== $('ppAngle')) $('ppAngle').value = Math.round(p.rot * 10) / 10;
      $('ppIgnore').checked = !!p.ignoreGrain;
      $('ppGrain').value = p.grainMode;
      $('ppFlipInfo').textContent = p.flip ? 'gewendet – Ablagefläche unten' : 'Ablagefläche oben';
      var fc = featureCounts(p);
      $('ppFeat').innerHTML = '';
      [['top', 'oben'], ['bottom', 'unten']].forEach(function (sd, i) {
        var sp = document.createElement('span');
        sp.className = 'feat-' + sd[0];
        sp.textContent = (i ? ', unten: ' : 'Bearbeitung oben: ') + sideText(fc[sd[0]]);
        sp.title = (p.features || []).filter(function (f) { return f.side === sd[0]; })
          .map(function (f) { return f.kind + ' ' + fmt(f.depth, 1) + ' mm tief'; }).join('\n');
        $('ppFeat').appendChild(sp);
      });
      $('ppFeat').setAttribute('data-text', featureText(p));
      $('ppPin').textContent = p.pinned ? '📌 Fixierung lösen' : '📌 Fixieren';
      $('ppPin').className = p.pinned ? 'on' : '';
      $('ppPin').disabled = !!f.parked;
      var sel = $('ppSheet'); sel.innerHTML = '';
      layout.sheets.forEach(function (S) {
        var o = document.createElement('option'); o.value = S.index; o.textContent = sheetName(S) + (S.def.kind === 'abschnitt' ? ' (Abschnitt)' : '') + (S.def.orient === 'quer' ? ' quer' : '');
        if (S.index === f.sheet.index) o.selected = true; sel.appendChild(o);
      });
      if (layout.sheets.length < Math.max(job.maxSheets || 1, layout.sheets.length + 1)) {
        var on = document.createElement('option'); on.value = 'new'; on.textContent = 'neues Rohteil'; sel.appendChild(on);
      }
      var w = [];
      if (f.parked) w.push('Geparkt – nicht platziert: auf die Platte ziehen oder „Neu nesten“');
      if (conflicts[p.key]) w.push('Kollision: ' + (conflicts[p.key] === 'Rand' ? 'außerhalb Rand/Besäumung' : 'Teileabstand unterschritten'));
      if (!grainOk(p)) w.push('Faser ' + GRAIN_TXT[p.grainMode] + ' verletzt');
      var fixed = p.fixRot !== null && p.fixRot !== undefined;
      $('ppFixRow').hidden = !fixed;
      $('ppFixRot').textContent = fixed ? 'Drehung fest: ' + fmt(p.fixRot, 1) + '°' : '';
      $('ppWarn').textContent = w.join(' · ');
    }
    var ex = Object.keys(ov).filter(function (k) { return ov[k].excluded; });
    $('exclPanel').hidden = !ex.length;
    var list = $('exclList'); list.innerHTML = '';
    ex.forEach(function (k) {
      var d = document.createElement('div'), s = document.createElement('span'), b = document.createElement('button');
      s.textContent = ov[k].name ? ov[k].name + ' #' + (parseInt(k.split('#').pop(), 10) + 1) : k;
      b.textContent = 'aufnehmen'; b.onclick = function () { actions.include(k); };
      d.appendChild(s); d.appendChild(b); list.appendChild(d);
    });
  }
  // ---------------------------------------------------------------- Rohteil-Liste (1.10.0)
  var KIND_TXT = { standard: 'Standard', abschnitt: 'Abschnitt' };
  function updateSheetPanel() {
    var box = $('sheetList'); if (!box) return;
    box.innerHTML = '';
    $('shEdit').hidden = !layout; $('shAdd').disabled = !layout; $('sheetNone').hidden = !!layout;
    if (!layout) return;
    layout.sheets.forEach(function (S, i) {
      var gm = geomOf(S), d = document.createElement('div');
      d.className = 'sheetrow' + (i === selSheet ? ' on' : '');
      d.setAttribute('data-si', i);
      d.textContent = (S.no ? String(S.no).padStart(2, '0') : '–') + ' · ' + deDim(gm) + ' · ' + (gm.orient === 'quer' ? 'quer' : 'längs') +
        ' · ' + KIND_TXT[gm.kind] + ' · ' + S.parts.length + ' T.' + ((S.parked && S.parked.length) ? ' +' + S.parked.length + ' P' : '');
      d.title = labelOf(S);
      d.onclick = function () { actions.selectSheet(i); };
      box.appendChild(d);
    });
    var S = layout.sheets[selSheet];
    $('shEdit').hidden = !S;
    if (!S) return;
    var gm = geomOf(S);
    $('shTitle').textContent = labelOf(S);
    $('shOrient').textContent = '⟲ ' + (gm.orient === 'quer' ? 'quer → längs' : 'längs → quer');
    $('shRenest').disabled = running || !(S.parts.length || (S.parked && S.parked.length));
    $('shUp').disabled = selSheet === 0; $('shDown').disabled = selSheet >= layout.sheets.length - 1;
    $('shDel').hidden = !!(S.parts.length || (S.parked && S.parked.length));
    var zs = $('shZero');
    if (!zs.options.length) E.ZERO_KEYS.forEach(function (k) { opt(zs, k, E.zeroText(k), false); });
    zs.value = gm.zero;
    $('shKind').value = gm.kind;
    $('shAb').hidden = gm.kind !== 'abschnitt';
    if (document.activeElement && document.activeElement.parentNode && document.activeElement.parentNode.parentNode === $('shAb')) return;
    $('shL').value = gm.length; $('shB').value = gm.width; $('shF').value = gm.frame; $('shT').value = gm.thickness;
    $('shG').value = gm.grain; $('shM').value = gm.kind === 'abschnitt' ? (S.def.material || '') : '';
  }
  function deDim(gm) { return E.deNum(gm.W) + ' × ' + E.deNum(gm.H); }
  function readSheetForm() {
    return { kind: $('shKind').value, length: $('shL').value, width: $('shB').value, frame: $('shF').value,
             thickness: $('shT').value, grain: $('shG').value, material: $('shM').value };
  }
  function summary(r) {
    r = r || layout;
    var s = $('summary'); s.innerHTML = '';
    if (!r) return;
    var p = document.createElement('div');
    var nUsed = r.sheets.filter(function (S) { return S.parts.length; }).length;
    var nAb = r.sheets.filter(function (S) { return S.parts.length && S.def && S.def.kind === 'abschnitt'; }).length;
    var nQ = r.sheets.filter(function (S) { return S.parts.length && S.def && S.def.orient === 'quer'; }).length;
    p.textContent = 'Ausnutzung gesamt: ' + fmt(r.utilisation * 100, 1) + ' % auf ' + nUsed + ' Rohteil(en)' +
      (nAb ? ', davon ' + nAb + ' Abschnitt(e)' : '') + (nQ ? ', ' + nQ + ' quer' : '') + ' – Standard ' +
      fmt(r.sheet.length) + ' × ' + fmt(r.sheet.width) + ' mm (' + (job.material || 'ohne Material') + ', ' + fmt(job.sheet.thickness || 0, 1) + ' mm)';
    s.appendChild(p);
    var nPl = r.sheets.reduce(function (a, S) { return a + S.parts.length; }, 0);
    var nUn = (r.unplaced || []).reduce(function (a, u) { return a + u.count; }, 0);
    var nEx = Object.keys(ov).filter(function (k) { return ov[k].excluded; }).length;
    var nPk = nParked(r);
    var cnt = document.createElement('div'); cnt.id = 'counts';
    cnt.className = (nUn || nPk) ? 'warn' : 'ok';
    cnt.textContent = (nPl + nUn + nPk) + ' Teile in Auftrag, ' + nPl + ' platziert, ' + (nUn + nPk) + ' nicht platziert' +
      (nPk ? ' (' + nPk + ' geparkt)' : '') +
      (nEx ? ', ' + nEx + ' ausgeschlossen' : '') + ' · ' + nUsed + ' von max. ' + Math.max(job.maxSheets || 1, r.sheets.length) + ' Rohteil(en)';
    s.appendChild(cnt);
    function warn(t) { var w = document.createElement('div'); w.className = 'warn'; w.textContent = t; s.appendChild(w); }
    if (nPk) warn(nPk + ' Teil(e) geparkt (grau, rechts neben dem Rohteil) – „Neu nesten“ ordnet sie ein, oder auf die Platte ziehen.');
    if (r.unplaced && r.unplaced.length) warn('Nicht platziert: ' + r.unplaced.map(function (u) { return u.count + '× ' + u.name; }).join(', '));
    var nc = Object.keys(conflicts).length;
    if (nc) warn(nc + ' Teil(e) mit Kollision/Abstand/Rand-Verletzung (rot) – „Abstände korrigieren“ schiebt sie minimal auseinander.');
    var nf = forcedKeys().length;
    if (nf) warn(nf + ' davon bewusst mit Strg in die Sperrzone gelegt.');
    if ($('btnFix')) $('btnFix').disabled = !nc || running;
    var crit = 0; r.sheets.forEach(function (S) { S.parts.forEach(function (q) { if (q.critical) crit++; }); });
    if (crit) warn(crit + ' Teil(e) unter der Mindestfläche für Vakuum – Haltestege/Tabs oder Onion-Skin einplanen!');
  }

  // ================================================================ An Fusion
  function slimLayout() {
    var slim = JSON.parse(JSON.stringify(layout));
    var un = {}, nPk = 0;
    (slim.unplaced || []).forEach(function (u) { un[u.id] = u; });
    slim.sheets.forEach(function (S, i) {
      S.parts.forEach(function (p) { delete p.outer; delete p.holes; delete p.features; });
      S.label = labelOf(layout.sheets[i]);     // Kontrolle: Fusion beschriftet mit derselben Regel
      // 2.0.5: geparkte Teile werden nicht übernommen -> als „nicht platziert“ melden (Fusion fragt nach)
      (S.parked || []).forEach(function (p) {
        var u = un[p.id] || (un[p.id] = { id: p.id, name: p.name, count: 0, keys: [] });
        if (!u.keys) u.keys = [];
        u.count++; u.keys.push(p.key); nPk++;
      });
      delete S.parked; delete S.parkW;
    });
    slim.unplaced = Object.keys(un).map(function (k) { return un[k]; });
    slim.parked = nPk;
    slim.sheetDefs = sheetDefs();
    slim.defaultZero = job.defaultZero || 'lu_unten';
    return slim;
  }
  var lastApplyAt = 0;
  function applyResult() {
    if (!layout) return;
    var now = Date.now();
    if (now - lastApplyAt < 3000) return;     // Doppelklick / doppeltes Ereignis abfangen
    lastApplyAt = now;
    if (running) stopNesting();
    syncPinnedFromLayout();
    var slim = slimLayout();
    slim.jobId = job.jobId; slim.settings = quickSettings(); slim.overrides = ov;
    slim.sheet = slim.sheet || {}; slim.sheet.thickness = job.sheet.thickness; slim.material = job.material || '';
    slim.conflicts = Object.keys(conflicts).length;
    var fk = forcedKeys();
    slim.forced = fk.length; slim.forcedNames = fk.map(function (p) { return p.name + ' #' + (p.copy + 1); });
    if (slim.conflicts) setStatus(slim.conflicts + ' Teil(e) verletzen Abstand/Rand' + (fk.length ? ' (' + fk.length + ' mit Strg übersteuert)' : '') + ' – Fusion fragt nach.', true);
    slim.applyId = job.jobId + '-' + now + '-' + Math.floor(Math.random() * 1e6);
    if (!slim.conflicts) setStatus('Übernehme Ergebnis in Fusion …');
    if (window.LazyShell) slim.applyOpts = window.LazyShell.applyOpts();
    sendToFusion('apply', slim);       // Rückfragen (Konflikte, nicht platziert) stellt Fusion
  }
  function backLayout() {        // 2.0.0: Stand für „Auswahl übernehmen & nesten“ (früher „Zurück“)
    if (running) stopNesting();
    if (job && !job.draft) readQuick();
    syncPinnedFromLayout();
    var lay = layout ? { sheet: { length: layout.sheet.length, width: layout.sheet.width },
      sheets: layout.sheets.map(function (S) { return S.parts.map(function (q) {
        return { key: q.key, id: q.id, x: q.x, y: q.y, rot: q.rot, flip: !!q.flip, pinned: !!q.pinned }; }); }),
      defs: sheetDefs() } : null;
    return { jobId: job && !job.draft ? job.jobId : '', overrides: ov, layout: lay };
  }

  window.fusionJavaScriptHandler = {
    handle: function (action, data) {
      try {
        if (action === 'job') loadJob(JSON.parse(data));
        else if (action === 'reset') {
          var rm = null; try { rm = (JSON.parse(data || '{}') || {}).message || null; } catch (e) { rm = null; }
          resetAll(rm); if (window.LazyShell) window.LazyShell.handle(action, data);
        }
        else if (action === 'status') setStatus(data);
        else if (action === 'stop') stopNesting();
        else if (window.LazyShell) return window.LazyShell.handle(action, data);
        else return 'Unbekannte Aktion: ' + action;
      } catch (e) { setStatus('Fehler: ' + e.message, true); }
      return 'OK';
    }
  };
  function handleReply(rep) {
    if (!rep) return;
    var j = null;
    try { j = JSON.parse(rep); } catch (e) { return; /* keine Daten */ }
    if (j && j.parts) loadJob(j);
    else if (j && j.version && window.LazyShell) window.LazyShell.onSession(j);
  }
  function announce(tries) {
    if (window.adsk && window.adsk.fusionSendData) {
      var res = sendToFusion('ready', {});
      if (res && typeof res.then === 'function') res.then(handleReply); else handleReply(res);
    } else if (tries < 100) {
      setTimeout(function () { announce(tries + 1); }, 100);
    } else setStatus('Keine Verbindung zu Fusion.', true);
  }

  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && ctxEl && !ctxEl.hidden) { hideCtx(); ev.preventDefault(); return; }
    var tag = (ev.target && ev.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if (ctxEl && !ctxEl.hidden) hideCtx();
    if (ev.key === 'Escape') { actions.deselect(); return; }
    if (!selected) return;
    if (ev.key === 'r' || ev.key === 'R') actions.rotate(ev.shiftKey ? -1 : 1);
    else if (ev.key === 'f' || ev.key === 'F') actions.flip();
    else if (ev.key === 'p' || ev.key === 'P') actions.pin();
    else if (ev.key === 'Delete') actions.exclude();
    else return;
    ev.preventDefault();
  });

  document.addEventListener('DOMContentLoaded', function () {
    $('btnStart').onclick = function () { if (readQuick()) startNesting(); };
    $('btnRecalc').onclick = $('btnStart').onclick;
    $('btnStop').onclick = function () { stopNesting(); };
    $('btnApply').onclick = applyResult;
    $('btnCancel').onclick = function () { if (running) stopNesting(); sendToFusion('cancel', {}); };
    $('btnFit').onclick = function () { if (geo) fitView(); };
    $('btnFix').onclick = function () { actions.fixSpacing(); };
    $('qV').oninput = function () { $('qVv').textContent = $('qV').value; };
    $('ppRotM').onclick = function () { actions.rotate(-1); };
    $('ppRotP').onclick = function () { actions.rotate(1); };
    $('ppAngleSet').onclick = function () { actions.setAngle(parseFloat($('ppAngle').value)); };
    $('ppIgnore').onchange = function () { actions.ignoreGrain($('ppIgnore').checked); };
    $('ppGrain').onchange = function () { actions.grainMode($('ppGrain').value); };
    $('ppFlip').onclick = function () { actions.flip(); };
    $('ppPin').onclick = function () { actions.pin(); };
    $('ppFixRel').onclick = function () { actions.releaseRot(); };
    $('fvTop').onclick = function () { setFeatView('top', true); };
    $('fvAll').onclick = function () { setFeatView('all', true); };
    $('qMat').onchange = stockUi.material; $('qFmt').onchange = stockUi.format; $('qThk').onchange = stockUi.thickness;
    $('qStockSave').onclick = stockUi.save;
    $('ppSheet').onchange = function () { actions.toSheet($('ppSheet').value); };
    $('ppExclude').onclick = function () { actions.exclude(); };
    $('shOrient').onclick = function () { actions.orient(selSheet); };
    $('shRenest').onclick = function () { actions.renestSheet(selSheet); };
    $('shZero').onchange = function () { actions.setZero(selSheet, $('shZero').value); };
    $('shKind').onchange = function () { var e = actions.setSheetDef(selSheet, readSheetForm()); if (e) setStatus(e, true); };
    $('shApply').onclick = function () { var e = actions.setSheetDef(selSheet, readSheetForm()); if (e) setStatus(e, true); };
    $('shUp').onclick = function () { actions.moveSheet(selSheet, -1); };
    $('shDown').onclick = function () { actions.moveSheet(selSheet, 1); };
    $('shAdd').onclick = function () { actions.addSheet('abschnitt'); };
    $('shDel').onclick = function () { actions.removeSheet(selSheet); };
    updateButtons();
    announce(0);
  });
  // Test-/Diagnose-Schnittstelle
  window.ContourNestApp = {
    setDraft: setDraft, reset: resetAll, applyLive: applyLive, backLayout: backLayout, readQuick: readQuick, startNesting: startNesting,
    send: sendToFusion, setStatus: setStatus, fitView: function () { if (geo) fitView(); }, redraw: function () { if (layout) draw(); },
    quickSettings: function () { return job ? quickSettings() : null; },
    loadJob: loadJob, actions: actions, nester: function () { return nester; },
    layout: function () { return layout; }, overrides: function () { return ov; },
    conflicts: function () { return conflicts; }, selected: function () { return selected; },
    isRunning: function () { return running; }, view: function () { return view; }, placer: placer,
    result: function () { return layout; },
    setFeatView: setFeatView, featView: featView, stock: stockUi, job: function () { return job; },
    selSheet: function () { return selSheet; }, geomOf: geomOf, labelOf: labelOf, sheetDefs: sheetDefs,
    slim: function () { return slimLayout(); },
    parked: function () { var a = []; if (layout) layout.sheets.forEach(function (S) { (S.parked || []).forEach(function (p) { a.push(p); }); }); return a; },
    nParked: function () { return nParked(); }, hasJob: function () { return !!(job && !job.draft); }
  };
}());
