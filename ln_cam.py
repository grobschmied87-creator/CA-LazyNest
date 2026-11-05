# -*- coding: utf-8 -*-
"""ContourNest – CAM-Setups (Fertigung) je Rohteil über die Fusion-CAM-API (ohne Manufacturing Extension).

Je Rohteil ein Fräs-Setup „ContourNest Rohteil 01“ …:
* Modelle = die auf dem Rohteil platzierten Teile (Occurrences) – der Rohteil-Körper ist KEIN Modell.
* Rohteil (Stock): „Plattenkörper“ = SolidStock aus dem Rohteil-Körper (Standard, robust: Lage = Platte)
  oder „Feste Box“ = FixedBoxStock L×B×D (Lage relativ zu den Modellen, siehe README).
* WCS: Ausrichtung = Modell (X = Plattenlänge, Y = Breite, Z nach oben), Nullpunkt = Punkt der
  Rohteil-Box (Standard „bottom 1“ = links vorne unten = links unten, Plattenunterseite).
* Verknüpfung Setup <-> Rohteil über das Attribut ContourNest/cam_link (Projekt-ID + Rohteil-Nr.).

Parameter-Namen/Werte (Quellen siehe README „Fertigung“):
  wcs_orientation_mode = 'modelOrientation'      (Autodesk-Beispiel „Set Vise Origin As Setup WCS Origin“)
  wcs_origin_mode      = 'stockPoint'            (Forum/UI „Stock box point“ – über getChoices() geprüft)
  wcs_origin_boxPoint  = 'bottom 1' … 'top center' (SetupWCSPoint im Beispiel „Manufacturing Workflow“)
  job_stockFixedX/Y/Z  (Beispiel „Wood Routing Workflow“), stockMode/stockSolids (API-Referenz Setup)
  job_programName / job_programComment (API-Forum „API access to NCPrograms“)
Jede Zuweisung wird zurückgelesen und protokolliert; Fehler beim CAM brechen das Übernehmen nie ab.
"""
import json

import adsk.core

try:
    import adsk.cam
except Exception:                     # sehr alte Fusion-Version / Teststubs ohne CAM
    adsk.cam = None

try:
    from . import ln_log as _L
    from . import ln_geom as G
except ImportError:                   # Test/Direktimport
    import ln_log as _L
    import ln_geom as G


def _cp(msg, *a):
    """Sicherheitsmodus: Kontrollpunkt (fsync) VOR jedem riskanten CAM-Aufruf – stürzt Fusion ab, nennt die
    letzte Protokollzeile den Aufruf."""
    try:
        _L.checkpoint(msg, *a)
    except Exception:
        pass

GROUP = 'ContourNest'
LINK_ATTR = 'cam_link'
PENDING_ATTR = 'cam_pending'          # Setup wartet auf neue Geometrie (Modelle vor dem Löschen entfernt)
FS_PREFIX = 'FS '                     # Bearbeitungen von Finalschnitt – dürfen gelöscht werden (neu erzeugbar)
NAME_FMT = 'ContourNest Rohteil %02d'
CAM_WS = 'CAMEnvironment'
DESIGN_WS = 'FusionSolidEnvironment'

# (Schlüssel, Anzeige, SetupWCSPoint-Wert) – X = Plattenlänge, Y = Plattenbreite, „vorne“ = Ymin
ORIGINS = (
    ('lu_unten', 'Links unten, Plattenunterseite', 'bottom 1'),
    ('lu_oben', 'Links unten, Plattenoberseite', 'top 1'),
    ('ru_unten', 'Rechts unten, Plattenunterseite', 'bottom 2'),
    ('lh_unten', 'Links hinten, Plattenunterseite', 'bottom 3'),
    ('rh_unten', 'Rechts hinten, Plattenunterseite', 'bottom 4'),
    ('mitte_unten', 'Mitte, Plattenunterseite', 'bottom center'),
    ('mitte_oben', 'Mitte, Plattenoberseite', 'top center'),
    # 1.10.0: alle Ecken auch oben (Nullpunkt je Rohteil in der Palette wählbar)
    ('ru_oben', 'Rechts unten, Plattenoberseite', 'top 2'),
    ('lh_oben', 'Links hinten, Plattenoberseite', 'top 3'),
    ('rh_oben', 'Rechts hinten, Plattenoberseite', 'top 4'),
)
STOCKS = (('solid', 'Plattenkörper (aus Körper)'), ('box', 'Feste Box (Plattenmaß)'))
DEFAULTS = {'cam': True, 'cam_origin': 'lu_unten', 'cam_stock': 'solid', 'cam_prog': 0}


def origin_point(key):
    return dict((k, v) for k, _l, v in ORIGINS).get(key, 'bottom 1')


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _items(coll):
    """Fusion-Collection (count/item) oder Python-Liste -> Liste."""
    if coll is None:
        return []
    if isinstance(coll, (list, tuple)):
        return list(coll)
    n = _safe(lambda: coll.count, 0)
    if isinstance(n, int):
        return [coll.item(i) for i in range(n)]
    return list(coll)


def _coll(objs):
    try:
        return adsk.core.ObjectCollection.createWithArray(list(objs))
    except Exception:
        c = adsk.core.ObjectCollection.create()
        for o in objs:
            c.add(o)
        return c


# ---------------------------------------------------------------- CAM-Produkt
def get_cam(app, ui, log, create=True):
    """CAM-Produkt des aktiven Dokuments. Fehlt es (FERTIGUNG noch nie geöffnet), wird der Arbeitsbereich
    kurz aktiviert (legt das Produkt an) – der vorherige Arbeitsbereich wird danach wiederhergestellt
    (restore_workspace). Rückgabe (cam, vorheriger_Arbeitsbereich_oder_None)."""
    doc = _safe(lambda: app.activeDocument)
    if doc is None:
        log.append('CAM: kein aktives Dokument')
        return None, None

    def lookup():
        p = _safe(lambda: doc.products.itemByProductType('CAMProductType'))
        if p is None:
            return None
        c = _safe(lambda: adsk.cam.CAM.cast(p)) if adsk.cam else None
        return c or p

    cam = lookup()
    log.append('CAM: itemByProductType(CAMProductType) -> %s' % ('vorhanden' if cam else 'None'))
    if cam is not None or not create:
        return cam, None
    prev = _safe(lambda: ui.activeWorkspace.id)
    ws = _safe(lambda: ui.workspaces.itemById(CAM_WS))
    if ws is None:
        log.append('CAM: Arbeitsbereich %s nicht gefunden' % CAM_WS)
        return None, None
    ok = _safe(lambda: ws.activate(), False)
    cam = lookup()
    log.append('CAM: Arbeitsbereich FERTIGUNG aktiviert (%s, vorher %s) -> CAM-Produkt %s'
               % (ok, prev, 'angelegt' if cam else 'weiterhin None'))
    return cam, (prev or DESIGN_WS)


def restore_workspace(ui, ws_id, log):
    if not ws_id:
        return
    ws = _safe(lambda: ui.workspaces.itemById(ws_id))
    ok = _safe(lambda: ws.activate(), False) if ws else False
    log.append('CAM: zurück zum Arbeitsbereich %s (%s)' % (ws_id, ok))


# ---------------------------------------------------------------- Parameter
def _unq(v):
    """Fusion 2705 liefert Auswahlwerte MIT Hochkommas ("'modelOrientation'", cam_*.json 12:50) -> ohne."""
    if isinstance(v, str) and len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1]
    return v


def _choices(p):
    r = _safe(lambda: p.value.getChoices())
    if isinstance(r, (list, tuple)) and len(r) == 3 and r[0]:
        return [_unq(n) for n in (r[1] or [])], [_unq(v) for v in (r[2] or [])]
    return [], []


def _read(p):
    v = _safe(lambda: p.value.value)
    if v is None or not isinstance(v, (str, int, float, bool)):
        v = _safe(lambda: p.expression)
    if isinstance(v, str) and len(v) >= 2 and v[0] == v[-1] == "'":
        v = v[1:-1]
    return v


def set_choice(params, name, want, keys=(), rec=None):
    """Auswahl-Parameter setzen: Wert über getChoices() prüfen (sonst über Teilstrings 'keys' suchen),
    per expression "'wert'" setzen, zurücklesen, sonst value.value versuchen. Gibt den gesetzten Wert zurück."""
    rec = rec if rec is not None else {}
    p = _safe(lambda: params.itemByName(name))
    if p is None:
        rec[name] = {'ok': False, 'error': 'Parameter fehlt'}
        return None
    names, values = _choices(p)
    val = want
    if values and want not in values:
        cand = [v for v, n in zip(values, names + [''] * len(values))
                if any(k.lower() in (str(v) + ' ' + str(n)).lower() for k in keys)]
        val = cand[0] if cand else want
    r = {'want': want, 'value': val, 'choices': values[:40]}
    try:
        p.expression = "'%s'" % val
    except Exception as e:
        r['expr_error'] = str(e)
    if _read(p) != val:
        try:
            p.value.value = val
        except Exception as e:
            r['value_error'] = str(e)
    r['read_back'] = _read(p)
    r['ok'] = r['read_back'] == val
    rec[name] = r
    return val if r['ok'] else None


def set_string(params, name, text, rec):
    p = _safe(lambda: params.itemByName(name))
    if p is None:
        rec[name] = {'ok': False, 'error': 'Parameter fehlt'}
        return False
    try:
        p.value.value = text
    except Exception:
        try:
            p.expression = "'%s'" % text.replace("'", '')
        except Exception as e:
            rec[name] = {'ok': False, 'error': str(e)}
            return False
    rec[name] = {'ok': _read(p) == text, 'value': text, 'read_back': _read(p)}
    return rec[name]['ok']


def set_length(params, name, mm, rec):
    p = _safe(lambda: params.itemByName(name))
    if p is None:
        rec[name] = {'ok': False, 'error': 'Parameter fehlt'}
        return False
    try:
        p.expression = '%g mm' % mm
        rec[name] = {'ok': True, 'value': '%g mm' % mm, 'read_back': _safe(lambda: p.expression)}
        return True
    except Exception as e:
        rec[name] = {'ok': False, 'error': str(e)}
        return False


# ---------------------------------------------------------------- Design -> CAM-Kontext
def _children(occ):
    return _items(_safe(lambda: occ.childOccurrences))


def map_occurrence(cam, occ):
    """Design-Occurrence -> gleichnamige Occurrence unter cam.designRootOccurrence (Pfad über fullPathName).
    Liefert (obj, 'cam'|'design')."""
    root = _safe(lambda: cam.designRootOccurrence)
    path = _safe(lambda: occ.fullPathName)
    if root is None or not isinstance(path, str):
        return occ, 'design'
    cur = root
    for nm in path.split('+'):
        nxt = next((c for c in _children(cur) if _safe(lambda: c.name) == nm), None)
        if nxt is None:
            return occ, 'design'
        cur = nxt
    return cur, 'cam'


def stock_body(occ, name='Rohteil'):
    bodies = _items(_safe(lambda: occ.bRepBodies))
    return next((b for b in bodies if _safe(lambda: b.name) == name), None)


def part_occurrences(sheet_occ):
    """Teile-Occurrences eines Rohteils im Wurzel-Kontext (Proxys)."""
    ch = _safe(lambda: sheet_occ.childOccurrences)
    if isinstance(ch, (list, tuple)) or (ch is not None and isinstance(_safe(lambda: ch.count), int)):
        return _items(ch)
    out = []
    for c in _items(_safe(lambda: sheet_occ.component.occurrences)):
        p = _safe(lambda: c.createForAssemblyContext(sheet_occ))
        out.append(p if p is not None else c)
    return out


def sheet_infos(design, project_id=None):
    """Rohteile (Kinder der Wurzel mit Attribut 'sheet') -> Liste dicts, sortiert nach Rohteil-Nr."""
    out = []
    for occ in _items(_safe(lambda: design.rootComponent.occurrences)):
        a = _safe(lambda: occ.attributes.itemByName(GROUP, 'sheet'))
        if not a:
            continue
        d = _safe(lambda: json.loads(a.value), {}) or {}
        pid = d.get('project_id') or ''
        if project_id is not None and pid != project_id:
            continue
        out.append({'occ': occ, 'no': int(d.get('index') or 0), 'project_id': pid,
                    'project_sheet': int(d.get('project_sheet') or d.get('index') or 0), 'attr': d,
                    'parts': part_occurrences(occ)})
    out.sort(key=lambda s: (s['project_id'], s['project_sheet']))
    return out


# ---------------------------------------------------------------- Setups
def setup_link(setup):
    a = _safe(lambda: setup.attributes.itemByName(GROUP, LINK_ATTR))
    return _safe(lambda: json.loads(a.value)) if a else None


def linked_setups(cam):
    out = []
    for s in _items(_safe(lambda: cam.setups)):
        ln = setup_link(s)
        if ln:
            out.append((s, ln))
    return out


def op_count(setup):
    n = _safe(lambda: setup.allOperations.count)
    if not isinstance(n, int):
        n = _safe(lambda: setup.operations.count, 0)
    return n if isinstance(n, int) else 0


def sheet_zero(sheet, opts):
    """Nullpunkt-Schlüssel des Rohteils (1.10.0: je Rohteil im Attribut), sonst Dialog-Vorgabe."""
    z = (sheet.get('attr') or {}).get('zero')
    return z if z in G.ZERO_KEYS else (opts.get('cam_origin') or 'lu_unten')


def machine_dims(a):
    """Maß im Maschinensystem (X, Y, Z mm) – quer liegende Platten: Länge/Breite getauscht."""
    L_, W_ = float(a.get('length_mm', 0) or 0), float(a.get('width_mm', 0) or 0)
    X = a.get('machine_length_mm')
    Y = a.get('machine_width_mm')
    if X is None or Y is None:
        X, Y = (W_, L_) if a.get('orient') == 'quer' else (L_, W_)
    return float(X), float(Y), float(a.get('thickness_mm', 0) or 0)


def comment_text(sheet, version):
    a = sheet['attr']
    X, Y, Z = machine_dims(a)
    return ('CA LazyNest v%s – Rohteil %02d – %s – %g × %g × %g mm (%s) – Faser %s – %s – Nullpunkt %s – Projekt %s' % (
        version, sheet['no'], a.get('material') or 'ohne Material', X, Y, Z,
        'quer' if a.get('orient') == 'quer' else 'längs', a.get('grain_machine') or a.get('grain', 'X'),
        'Abschnitt' if a.get('kind') == 'abschnitt' else 'Standard', G.zero_text(a.get('zero')), sheet['project_id'] or '-'))


def configure(cam, setup, sheet, opts, version, log, rec):
    """Name, Modelle, Rohteil, WCS, Programm, Kommentar, Verknüpfung auf ein (neues oder vorhandenes) Setup."""
    name = NAME_FMT % sheet['no']
    if _safe(lambda: setup.name) != name:     # gleichen Namen erneut setzen -> Fusion hängt „1“ an („…011“)
        _safe(lambda: setattr(setup, 'name', name))
    rec['name'] = _safe(lambda: setup.name)
    params = _safe(lambda: setup.parameters)
    # ---- Rohteil
    mode = opts.get('cam_stock', 'solid')
    body = None
    if mode == 'solid':
        so_cam, _how = map_occurrence(cam, sheet['occ'])
        body = stock_body(so_cam) or stock_body(sheet['occ'])
        if body is None:
            log.append('%s: kein Rohteil-Körper – feste Box verwendet' % name)
            mode = 'box'
    if mode == 'solid' and _same_stock(setup, body):
        rec['stock'] = 'solid'
        rec['stock_unchanged'] = True
    elif mode == 'solid':
        try:
            _cp('%s: stockSolids zuweisen', name)
            setup.stockMode = adsk.cam.SetupStockModes.SolidStock
            setup.stockSolids = _coll([body])
            rec['stock'] = 'solid'
        except Exception as e:
            log.append('%s: Rohteil aus Körper fehlgeschlagen (%s) – feste Box' % (name, e))
            mode = 'box'
    if mode == 'box':
        a = sheet['attr']
        try:
            setup.stockMode = adsk.cam.SetupStockModes.FixedBoxStock
        except Exception as e:
            set_choice(params, 'job_stockMode', 'fixedbox', ('fixed', 'box'), rec)
            rec['stockMode_error'] = str(e)
        X, Y, Z = machine_dims(a)            # Maschinensystem (quer: getauscht)
        set_length(params, 'job_stockFixedX', X, rec)
        set_length(params, 'job_stockFixedY', Y, rec)
        set_length(params, 'job_stockFixedZ', Z, rec)
        # Lage der Box: Bezug „linke/vordere/untere Modellkante“ + Versatz bis zur Plattenkante (UNGEPRÜFT)
        off = sheet.get('model_offset_mm')
        if off:
            for ax, keys in (('X', ('left', 'links')), ('Y', ('front', 'vorn')), ('Z', ('bottom', 'unten'))):
                set_choice(params, 'job_stockFixed%sMode' % ax, 'offset from %s' % keys[0], keys, rec)
                set_length(params, 'job_stockFixed%sOffset' % ax, off[ax], rec)
        rec['stock'] = 'box'
    # ---- WCS
    set_choice(params, 'wcs_orientation_mode', 'modelOrientation', ('model',), rec)
    set_choice(params, 'wcs_origin_mode', 'stockPoint', ('stock',), rec)
    zk = sheet_zero(sheet, opts)
    set_choice(params, 'wcs_origin_boxPoint', G.zero_box_point(zk), (), rec)
    rec['zero'] = zk
    # ---- Programm / Kommentar
    cm = comment_text(sheet, version)
    prog = int(opts.get('cam_prog') or 0)
    if prog > 0:
        set_string(params, 'job_programName', str(prog + sheet['project_sheet'] - 1), rec)
    set_string(params, 'job_programComment', cm, rec)
    _safe(lambda: setattr(setup, 'notes', cm))
    link = {'project_id': sheet['project_id'], 'project_sheet': sheet['project_sheet'], 'sheet_no': sheet['no'],
            'sheet_name': _safe(lambda: sheet['occ'].name), 'version': version}
    try:
        setup.attributes.add(GROUP, LINK_ATTR, json.dumps(link))
    except Exception as e:
        log.append('%s: Verknüpfungs-Attribut fehlgeschlagen: %s' % (name, e))
    rec['comment'] = cm
    bad = [k for k, v in rec.items() if isinstance(v, dict) and v.get('ok') is False]
    if bad:
        log.append('%s: Parameter nicht gesetzt/bestätigt: %s' % (name, ', '.join(bad)))
    return rec


def _path(o):
    p = _safe(lambda: o.fullPathName)
    return p if isinstance(p, str) else ''


def _body_key(b):
    ctx = _safe(lambda: b.assemblyContext)
    return (_path(ctx) if ctx is not None else '', _safe(lambda: b.name))


def _same_stock(setup, body):
    """Ist genau dieser Körper bereits als Rohteil zugewiesen? (dann nichts anfassen)"""
    try:
        if setup.stockMode != adsk.cam.SetupStockModes.SolidStock:
            return False
        cur = _items(setup.stockSolids)
        return len(cur) == 1 and _body_key(cur[0]) == _body_key(body)
    except Exception:
        return False


def model_paths(setup):
    return [_path(m) for m in _items(_safe(lambda: setup.models))]


def _under(path, prefix):
    return bool(prefix) and (path == prefix or path.startswith(prefix + '+'))


def operations(setup):
    ops = _items(_safe(lambda: setup.allOperations))
    return ops if ops else _items(_safe(lambda: setup.operations))


def is_pending(setup):
    a = _safe(lambda: setup.attributes.itemByName(GROUP, PENDING_ATTR))
    return bool(a and _safe(lambda: a.value))


def _set_pending(setup, value):
    _safe(lambda: setup.attributes.add(GROUP, PENDING_ATTR, json.dumps(value) if value else ''))


def dependents(cam, project_id, dels):
    """CAM-Setups, die auf zu löschende Geometrie verweisen: verknüpft (cam_link: Projekt + Rohteil-Nr.) ODER
    Modelle unterhalb eines zu löschenden Pfads. dels = ln_fusion.plan_deletions(); dels['all'] = alle Rohteile.
    Rückgabe [{'setup', 'name', 'ops', 'reason', 'link'}]."""
    out = []
    if cam is None or not dels or not (dels.get('count') or dels.get('all')):
        return out
    paths = list(dels.get('paths') or [])
    nos = set(dels.get('sheet_nos') or [])
    for s in _items(_safe(lambda: cam.setups)):
        ln = setup_link(s)
        reason = None
        if ln and (dels.get('all') or (ln.get('project_id') == project_id
                                       and int(ln.get('project_sheet', -1)) in nos)):
            reason = 'verknüpft'
        if reason is None and (paths or dels.get('idents')):
            targets = list(dels.get('idents') or []) + [{'path': p} for p in paths]
            cur, _ok = read_models(s)
            if any(same_ident(i, t) for _m, i in cur for t in targets):
                reason = 'Modelle'
        if reason:
            out.append({'setup': s, 'name': _safe(lambda: s.name, '?'), 'ops': op_count(s), 'reason': reason,
                        'link': ln})
    return out


def _clear_op_geometry(op, rec):
    """Geometrie-Auswahl einer Bearbeitung leeren (Konturen/Taschen …) – bestmöglich, nie fatal."""
    n = 0
    for p in _items(_safe(lambda: op.parameters)):
        val = _safe(lambda: p.value)
        if val is None or not hasattr(val, 'getCurveSelections'):
            continue
        try:
            sel = val.getCurveSelections()
            if _safe(lambda: sel.count, 0):
                _cp('Bearbeitung %s: Auswahl %s leeren', _safe(lambda: op.name), _safe(lambda: p.name))
                sel.clear()
                val.applyCurveSelections(sel)
                n += 1
        except Exception as e:
            rec.setdefault('errors', []).append('%s/%s: %s' % (_safe(lambda: op.name), _safe(lambda: p.name), e))
    return n


def _ident(o):
    """2.0.1: Identität einer Occurrence/eines Setup-Modells – entityToken, Komponenten-ID + Name, Pfad.
    Abgleich NICHT nur über Pfad-Texte (CAM-Kopien der Occurrences haben eigene Pfade)."""
    return {'token': _safe(lambda: o.entityToken) or '', 'comp': _safe(lambda: o.component.id) or '',
            'name': _safe(lambda: o.name) or '', 'path': _path(o)}


def same_ident(a, b):
    if a.get('token') and a.get('token') == b.get('token'):
        return True
    if a.get('comp') and a.get('comp') == b.get('comp') and a.get('name') and a.get('name') == b.get('name'):
        return True
    pa, pb = a.get('path') or '', b.get('path') or ''
    return bool(pa and pb) and (pa == pb or pa.startswith(pb + '+'))


def read_models(setup):
    """Modelle eines Setups sicher lesen -> (liste [(obj, ident)], sicher). sicher=False, wenn die Sammlung
    oder ein Modellbezug nicht lesbar/ungültig ist (z. B. gelöschte Occurrence) – dann NICHT zuweisen."""
    try:
        coll = setup.models
    except Exception:
        return [], False
    n = _safe(lambda: coll.count, None) if coll is not None else 0
    if not isinstance(n, int):
        return [], False
    out, ok = [], True
    for i in range(n):
        try:
            m = coll.item(i)
            if m is None or _safe(lambda: m.isValid, True) is False:
                ok = False
                continue
            out.append((m, _ident(m)))
        except Exception:
            ok = False
    return out, ok


def pending_info(setup):
    a = _safe(lambda: setup.attributes.itemByName(GROUP, PENDING_ATTR))
    v = _safe(lambda: a.value) if a else None
    if not v:
        return None
    try:
        d = json.loads(v)
        return d if isinstance(d, dict) else {'choice': str(d)}
    except Exception:
        return {'choice': str(v)}


def _empty_coll():
    try:
        return adsk.core.ObjectCollection.create()
    except Exception:
        return None


def prepare_for_delete(cam, deps, choice, dels, log):
    """VOR dem Löschen von Geometrie (eigene Transaktion, außerhalb des Übernehmen-Befehls).
    2.0.1 (Absturz KMD-PC-03 12:33:57, setup.models auf gelöschte Occurrences):
    * Setup ohne Bearbeitungen, das auf ersetzte Geometrie verweist -> Setup LÖSCHEN (solange alle Bezüge noch
      gültig sind); sync legt es nach dem Übernehmen mit create_setup neu an.
    * Setup mit Bearbeitungen: choice 'delete_ops' löscht alle Bearbeitungen; 'reassign' löscht nur
      Finalschnitt-Bearbeitungen („FS …“) und leert bei den übrigen die Geometrie-Auswahl. Danach werden die
      Modelle VOLLSTÄNDIG geleert (Rückfall: nur betroffene per Identität entfernen), Rohteil-Körper gelöst und
      das Setup als „wartet auf Geometrie“ markiert (sync weist nach dem Übernehmen neu zu).
    * Ist ein Modellbezug nicht sicher lesbar, wird nichts zugewiesen: Markierung unsafe -> Hinweis „in FERTIGUNG
      neu zuweisen“.
    Protokoll enthält Modell- und Löschpfade (camprep-JSON)."""
    rep = {'choice': choice, 'setups': [], 'delete_paths': list(dels.get('paths') or []),
           'delete_idents': list(dels.get('idents') or [])}
    paths = list(dels.get('paths') or [])
    targets = list(dels.get('idents') or []) + [{'path': p} for p in paths]
    gone = set(p for p in paths if '+' not in p)                 # ganz entfallende Rohteile
    stock_sheets = set(dels.get('stock_sheets') or []) | gone
    for d in deps:
        s = d['setup']
        nm = d.get('name') or _safe(lambda: s.name, '?')
        r = {'name': nm, 'reason': d.get('reason'), 'ops': op_count(s), 'ops_deleted': 0, 'ops_cleared': 0,
             'models_removed': 0}
        rep['setups'].append(r)
        cur, safe = read_models(s)
        r['models_before'] = [i for _m, i in cur]
        r['models_safe'] = safe
        if r['ops'] == 0:
            try:
                _cp('Setup %s: keine Bearbeitungen -> löschen, nach dem Übernehmen neu anlegen', nm)
                s.deleteMe()
                r['deleted_for_recreate'] = True
                log.append('%s: ohne Bearbeitungen – gelöscht, wird nach dem Übernehmen neu angelegt' % nm)
                continue
            except Exception as e:
                r['delete_error'] = str(e)
                log.append('%s: Löschen fehlgeschlagen (%s) – Modelle werden geleert' % (nm, e))
        for op in operations(s):
            onm = _safe(lambda: op.name, '') or ''
            if choice == 'delete_ops' or onm.startswith(FS_PREFIX):
                try:
                    _cp('Setup %s: Bearbeitung %s löschen', nm, onm)
                    op.deleteMe()
                    r['ops_deleted'] += 1
                except Exception as e:
                    r.setdefault('errors', []).append('%s: %s' % (onm, e))
            else:
                if _clear_op_geometry(op, r):
                    r['ops_cleared'] += 1
        cleared = False
        if safe and cur:
            emp = _empty_coll()
            if emp is not None:
                try:
                    _cp('Setup %s: Modelle %d -> 0 (vollständig leeren)', nm, len(cur))
                    s.models = emp
                    cleared = True
                    r['models_removed'] = len(cur)
                except Exception as e:
                    r['clear_error'] = str(e)
            if not cleared:
                keep = [m for m, i in cur if not any(same_ident(i, t) for t in targets)]
                if len(keep) != len(cur):
                    try:
                        _cp('Setup %s: Modelle %d -> %d (Identität)', nm, len(cur), len(keep))
                        s.models = _coll(keep)
                        r['models_removed'] = len(cur) - len(keep)
                        cleared = not keep
                    except Exception as e:
                        r['models_error'] = str(e)
                        log.append('%s: Modelle konnten nicht entfernt werden (%s)' % (nm, e))
        elif not safe:
            log.append('%s: Modellbezüge nicht sicher lesbar – werden nicht angefasst; nach dem Übernehmen bitte in '
                       'FERTIGUNG neu zuweisen' % nm)
        ln = d.get('link') or {}
        bodies = _items(_safe(lambda: s.stockSolids))
        if ln.get('sheet_name') in stock_sheets or any(_body_key(b)[0] in stock_sheets for b in bodies):
            try:
                _cp('Setup %s: Rohteil-Körper lösen', nm)
                s.stockMode = adsk.cam.SetupStockModes.FixedBoxStock
                r['stock'] = 'feste Box (bis zur Neuzuweisung)'
            except Exception as e:
                r['stock_error'] = str(e)
        r['models_cleared'] = cleared
        _set_pending(s, {'choice': choice, 'cleared': cleared, 'unsafe': not safe})
    return rep


def _models(cam, sheet, rec):
    mapped = [map_occurrence(cam, o) for o in sheet['parts']]
    rec['models'] = len(mapped)
    rec['models_context'] = sorted(set(h for _o, h in mapped))
    return [o for o, _h in mapped]


def _model_offset(sheet):
    """Abstand Modell-Hüllquader -> Plattenkante (mm), für die feste Box."""
    try:
        so = sheet['occ']
        t = so.transform2.translation
        a = sheet.get('attr') or {}
        ox = t.x - (float(a.get('width_mm') or 0) * 0.1 if a.get('orient') == 'quer' else 0.0)   # Maschinen-Ursprung
        mins = [None, None, None]
        for o in sheet['parts']:
            bb = o.boundingBox
            p = bb.minPoint
            for k, v in enumerate((p.x, p.y, p.z)):
                mins[k] = v if mins[k] is None else min(mins[k], v)
        if None in mins:
            return None
        return {'X': (mins[0] - ox) * 10.0, 'Y': (mins[1] - t.y) * 10.0, 'Z': (mins[2] - t.z) * 10.0}
    except Exception:
        return None


def create_setup(cam, sheet, opts, version, log):
    rec = {'sheet': sheet['no'], 'action': 'erstellt'}
    inp = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
    _safe(lambda: setattr(inp, 'name', NAME_FMT % sheet['no']))
    inp.models = _models(cam, sheet, rec)
    setup = cam.setups.add(inp)
    sheet.setdefault('model_offset_mm', _model_offset(sheet))
    configure(cam, setup, sheet, opts, version, log, rec)
    return setup, rec


def update_setup(cam, setup, sheet, opts, version, log):
    """Vorhandenes Setup an Ort und Stelle aktualisieren. Modelle nur zuweisen, wenn sie sich wirklich geändert
    haben UND alle vorhandenen Modellbezüge sicher lesbar sind (2.0.1: nie auf ungültige Bezüge zuweisen –
    nativer Absturz in ModelSelectionRequest). Sonst: Setup bleibt „wartet auf Geometrie“, Hinweis an den Anwender."""
    rec = {'sheet': sheet['no'], 'action': 'aktualisiert'}
    want = _models(cam, sheet, rec)
    pinfo = pending_info(setup)
    cur, safe = read_models(setup)
    rec['models_before'] = [i['path'] for _m, i in cur]
    rec['models_safe'] = safe
    if pinfo and pinfo.get('unsafe'):
        safe = False
    if not safe:
        rec['models_changed'] = False
        rec['action'] = 'Modelle nicht zugewiesen'
        rec['manual'] = True
        _set_pending(setup, dict(pinfo or {}, unsafe=True))
        log.append('%s: Modellbezüge nicht sicher lesbar – Modelle NICHT zugewiesen. Bitte in FERTIGUNG: Setup '
                   'bearbeiten → Modell → die Teile von %s wählen.' % (_safe(lambda: setup.name, '?'), sheet['name']))
    elif pinfo or sorted(_path(o) for o in want) != sorted(i['path'] for _m, i in cur):
        _cp('Setup %s: models = %d Teil(e)', _safe(lambda: setup.name), len(want))
        setup.models = _coll(want)
        _cp('Setup %s: models zugewiesen', _safe(lambda: setup.name))
        rec['models_changed'] = True
    else:
        rec['models_changed'] = False
        rec['action'] = 'geprüft'
    if pinfo and safe:
        _set_pending(setup, None)
        rec['reassigned'] = True
    sheet.setdefault('model_offset_mm', _model_offset(sheet))
    configure(cam, setup, sheet, opts, version, log, rec)
    return rec


def sync(app, ui, design, project_id, opts, version, log, diag=None):
    """Setups für alle Rohteile des Projekts anlegen/aktualisieren; verwaiste Setups aufräumen.
    Liefert dict mit Zählern und einer deutschen Ergebniszeile ('line'). Wirft nie."""
    diag = diag if diag is not None else {}
    res = {'created': 0, 'updated': 0, 'recreated': 0, 'deleted': 0, 'kept': 0, 'errors': 0, 'warnings': [],
           'line': ''}
    prev_ws = None
    try:
        if adsk.cam is None:
            res['line'] = 'CAM-Setup: CAM-API nicht verfügbar.'
            return res
        sheets = sheet_infos(design, project_id)
        if not sheets:
            res['line'] = 'CAM-Setup: keine Rohteile gefunden.'
            return res
        _cp('CAM: get_cam (ggf. Arbeitsbereich FERTIGUNG aktivieren)')
        cam, prev_ws = get_cam(app, ui, log)
        if cam is None:
            res['line'] = ('CAM-Setup: nicht erstellt – kein CAM-Produkt (Arbeitsbereich FERTIGUNG einmal öffnen '
                           'und „Nesting bearbeiten“ → Übernehmen wiederholen).')
            return res
        all_keys = set((s['project_id'], s['project_sheet']) for s in sheet_infos(design))
        existing = linked_setups(cam)
        used = set()
        diag['setups'] = []
        for sh in sheets:
            hit = next(((s, ln) for s, ln in existing if ln.get('project_id') == sh['project_id']
                        and int(ln.get('project_sheet', -1)) == sh['project_sheet']), None)
            try:
                if hit:
                    # 1.8.1: vorhandene Setups NIE löschen/neu anlegen (Absturz 12:48/12:53), nur aktualisieren
                    used.add(id(hit[0]))
                    rec = update_setup(cam, hit[0], sh, opts, version, log)
                    res['updated'] += 1
                    if rec.get('manual'):
                        res['warnings'].append('%s: Modelle bitte in FERTIGUNG neu zuweisen (Bezüge nicht sicher '
                                               'lesbar – zur Sicherheit nicht automatisch)' % (NAME_FMT % sh['no']))
                    n_ops = op_count(hit[0])
                    if n_ops:
                        res['warnings'].append('%s hat %d Bearbeitung(en) – Geometrie geändert, Werkzeugwege bitte '
                                               'neu berechnen (nicht automatisch)%s' % (
                                                   NAME_FMT % sh['no'], n_ops,
                                                   '; Geometrie-Auswahl neu zuweisen' if rec.get('reassigned') else ''))
                else:
                    _cp('Setup für Rohteil %02d anlegen', sh['no'])
                    _s, rec = create_setup(cam, sh, opts, version, log)
                    res['created'] += 1
                diag['setups'].append(rec)
            except Exception as e:
                res['errors'] += 1
                log.append('CAM-Setup %s: Fehler %s' % (NAME_FMT % sh['no'], e))
        for s, ln in existing:                      # verwaiste Setups (Rohteil existiert nicht mehr)
            if id(s) in used or not _safe(lambda: s.isValid, True):
                continue
            if (ln.get('project_id'), int(ln.get('project_sheet', -1))) in all_keys:
                continue
            if op_count(s) > 0:
                res['kept'] += 1
                res['warnings'].append('%s gehört zu keinem Rohteil mehr, hat aber Bearbeitungen – nicht gelöscht'
                                       % _safe(lambda: s.name, '?'))
            else:
                _cp('verwaistes Setup %s löschen', _safe(lambda: s.name, '?'))
                if _safe(lambda: s.deleteMe(), False) is not False:
                    res['deleted'] += 1
        n = res['created'] + res['updated']
        parts = ['%d erstellt' % res['created']]
        if res['updated']:
            parts.append('%d aktualisiert' % res['updated'])
        if res['recreated']:
            parts.append('%d ersetzt' % res['recreated'])
        if res['deleted']:
            parts.append('%d verwaiste gelöscht' % res['deleted'])
        if res['errors']:
            parts.append('%d Fehler' % res['errors'])
        o = dict((k, lbl) for k, lbl, _v in ORIGINS).get(opts.get('cam_origin', 'lu_unten'))
        st = dict(STOCKS).get(opts.get('cam_stock', 'solid'))
        res['line'] = ('CAM-Setup: %d Setup(s) für %d Rohteil(e) – %s; Nullpunkt %s; Rohteil %s.'
                       % (n, len(sheets), ', '.join(parts), o, st))
        if res['warnings']:
            res['line'] += '\n  ' + '\n  '.join(res['warnings'])
        return res
    except Exception as e:
        res['errors'] += 1
        res['line'] = 'CAM-Setup: fehlgeschlagen (%s) – Nesting ist trotzdem übernommen.' % e
        log.append('CAM: Ausnahme %r' % e)
        return res
    finally:
        restore_workspace(ui, prev_ws, log)
        diag['result'] = {k: v for k, v in res.items() if k != 'line'}


def delete_linked(cam, log, project_ids=None):
    """Verknüpfte Setups löschen (alle oder nur der Projekte). Rückgabe Anzahl."""
    n = 0
    for s, ln in linked_setups(cam):
        if project_ids is not None and ln.get('project_id') not in project_ids:
            continue
        try:
            _cp('Setup %s löschen', _safe(lambda: s.name, '?'))
            s.deleteMe()
            n += 1
        except Exception as e:
            log.append('Setup löschen: %s' % e)
    return n
