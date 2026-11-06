# -*- coding: utf-8 -*-
"""CA LazyNest 2.0.7 – True-Shape-Nesting (Konturschachtelung) für Autodesk Fusion ohne Manufacturing Extension.
Nachfolger von ContourNest 1.10.0 („Nesting für Macher und faule Typen“).

EIN Fenster: die HTML-Palette (palette/index.html) ist das Hauptfenster mit den Registern Auswahl, Platte,
Nesting, Vorschau und Ergebnis. „Teile wählen“ startet einen schlanken Fusion-Auswahlbefehl und kehrt in die
Palette zurück; erst „Auswahl übernehmen & nesten“ baut den Auftrag (interner Befehl, darf die Konstruktion
ändern: Parameter, Komponente aus Körper, Baugruppen-Kopien). Das Ergebnis wird über den internen Befehl
„Nesting übernehmen“ als Rohteil-Komponenten mit Teile-Occurrences aufgebaut.

Kompatibilität: Attributgruppe „ContourNest“, Projektformat „ContourNest-Projekt“ und Setup-Namen
„ContourNest Rohteil NN“ bleiben unverändert – Finalschnitt liest LazyNest-Projekte ohne Änderung.
"""
import json
import os
import platform
import sys
import time
import uuid

import adsk.core
import adsk.fusion

try:
    from . import ln_geom as G
    from . import ln_fusion as F
    from . import ln_log as L
    from . import ln_project as P
    from . import ln_stock as STK
    from . import ln_cam as CAM
    from . import ln_catools as T
    from . import ln_assembly as A
    from . import ln_timing as TM
    from . import ln_cleanup as CL
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ln_geom as G          # noqa: E402
    import ln_fusion as F        # noqa: E402
    import ln_log as L           # noqa: E402
    import ln_project as P       # noqa: E402
    import ln_stock as STK       # noqa: E402
    import ln_cam as CAM         # noqa: E402
    import ln_catools as T       # noqa: E402
    import ln_assembly as A      # noqa: E402
    import ln_timing as TM       # noqa: E402
    import ln_cleanup as CL      # noqa: E402



def _load_core(alias):
    """Gemeinsamen Kern (ca_core, identisch in LazyNest/LazySetup) unter EIGENEM Modulnamen laden – beide
    Add-Ins laufen im selben Fusion-Python; ein gemeinsamer Name würde die Module des anderen überschreiben."""
    import importlib.util
    if alias in sys.modules:
        return sys.modules[alias]
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ca_core')
    spec = importlib.util.spec_from_file_location(alias, os.path.join(d, '__init__.py'), submodule_search_locations=[d])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


CORE = _load_core('lazynest_ca_core')
ca_i18n = CORE.ca_i18n
TH = CORE.ca_theme
REFS = CORE.ca_refs
F.CC = CORE.ca_compute            # 2.0.3: gemeinsamer computeAll-Stand mit LazySetup
DIAG = CORE.ca_diag
PRODUCT = 'lazynest'

VERSION = '2.0.13'                 # EINZIGE Versionsquelle (Manifest, Tooltips, Palette, Protokoll, Projektdateien)
BUILD_DATE = '2026-11-06'
APP_NAME = 'CA LazyNest'
L.VERSION = VERSION
P.VERSION = VERSION

_app = None
_ui = None
handlers = []                    # alle Event-Handler global halten (sonst sammelt Python sie ein)
_state = {
    'part_settings': {},         # key -> {'mode','qty','enabled', 'manual_qty', 'project_qty'}
    'sel': {},                   # Auswahlliste key -> Eintrag (siehe _sel)
    'sel_order': [],
    'ctx': None,                 # {'job':..., 'parts': {id: {...}}} für Übernahme
    'result': None,
    'opts': {},
    'overrides': {},             # Fixierungen/Wenden/Ausschlüsse je Stück aus der Palette
    'assembly': False,           # Baugruppenmodus
}

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, 'resources')
PALETTE_ID = 'LazyNest_Palette'
PAL_W, PAL_H = 520, 820
CMD_CREATE = 'LazyNest_Create'
CMD_REPEAT = 'LazyNest_Repeat'
CMD_DELETE = 'LazyNest_Delete'
CMD_APPLY = 'LazyNest_Apply'
CMD_DIAG = 'LazyNest_Diagnose'
CMD_EDIT = 'LazyNest_Edit'
CMD_IMPORT = 'LazyNest_Import'
CMD_PICK = 'LazyNest_Pick'               # schlanker Auswahlbefehl („Teile wählen“)
CMD_PREP = 'LazyNest_Prepare'            # intern: Auftrag bauen (Parameter, Kopien, Komponente aus Körper)
CMD_REFRESH = 'LazyNest_RefreshCopies'   # intern: Baugruppen-Kopien aktualisieren
EV_APPLY = 'LazyNest_ApplyEvent'        # CustomEvents: Befehle NICHT direkt aus dem HTML-Ereignis starten
EV_VERIFY = 'LazyNest_VerifyEvent'      # Nachprüfung NACH Ende des Übernehmen-Befehls
EV_SWITCH = 'LazyNest_SwitchEvent'      # Aufruf aus FERTIGUNG -> KONSTRUKTION aktivieren, Befehl neu starten
EV_PICK = 'LazyNest_PickEvent'
EV_NEST = 'LazyNest_NestEvent'
EV_REFRESH = 'LazyNest_RefreshEvent'
EV_CAM = 'LazyNest_CamEvent'              # 2.0.1: CAM-Setups in EIGENEM Schritt nach der Nachprüfung
EV_DELETE = 'LazyNest_DeleteEvent'        # 2.0.4: „Nesting löschen“ (Rückfrage + CAM außerhalb eines Befehls)
EV_CLEANUP = 'LazyNest_CleanupEvent'      # 2.0.4: Waisen nach manuellem Löschen aufräumen
CMD_DELETE_NEST = 'LazyNest_DeleteNesting'  # intern: Geometrie/Attribute eines Nestings löschen
CMD_CLEANUP = 'LazyNest_Cleanup'          # intern: Waisen (ohne CAM) löschen
_custom_events = {}
PANELS = ('SolidModifyPanel',)
ADDINS_PANEL = 'SolidScriptsAddinsPanel'      # Diagnose/Import + Rückfall, falls „CA Tools“ nicht anlegbar
WS = 'FusionSolidEnvironment'
CAM_WS = 'CAMEnvironment'
CAM_ADDINS_PANEL = 'CAMScriptsAddinsPanel'    # Rückfall in FERTIGUNG
CAM_CMDS = ((CMD_CREATE, True), (CMD_EDIT, False))   # in FERTIGUNG > FRÄSEN > CA Tools (True = großes Symbol)


def _design():
    return adsk.fusion.Design.cast(_app.activeProduct)


def _design_or_switch(cid):
    """1.8.2: Konstruktion holen; aus FERTIGUNG (CA Tools) aufgerufen -> KONSTRUKTION aktivieren und den Befehl
    danach neu starten (über CustomEvent, nie im laufenden commandCreated). None = nicht fortfahren."""
    design = _design()
    if design:
        return design
    has_design = L.safe(lambda: _app.activeDocument.products.itemByProductType('DesignProductType'))
    if has_design is not None and EV_SWITCH in _custom_events:
        _state['switch_cmd'] = cid
        L.info('%s aus anderem Arbeitsbereich – wechsle zu KONSTRUKTION und starte neu', cid)
        _fire(EV_SWITCH)
        return None
    return None


def _msg(text, title=APP_NAME):
    if _ui:
        _ui.messageBox(text, title)


def _err(prefix='Fehler'):
    tb = L.exc(prefix)
    _msg('%s:\n%s\n\nProtokoll: %s' % (prefix, tb, L.LOG_FILE))


def conflict_question(res):
    """1.9.0: Rückfrage beim Übernehmen mit Abstands-/Randverletzungen (inkl. mit Strg übersteuerter Teile)."""
    n = int(res.get('conflicts') or 0)
    txt = '%d Teil(e) kollidieren oder verletzen Abstand/Rand (rot in der Vorschau).' % n
    forced = int(res.get('forced') or 0)
    if forced:
        names = list(res.get('forcedNames') or [])
        more = len(names) - 5
        txt += '\n\nDavon %d bewusst mit Strg in die Sperrzone gelegt: %s%s' % (
            forced, ', '.join(names[:5]), (' … (+%d)' % more) if more > 0 else '')
    txt += ('\n\nTipp: „Abstände korrigieren“ in der Vorschau schiebt die Teile minimal auseinander.'
            '\n\nTrotzdem übernehmen?')
    return txt


def _ask(text, title=APP_NAME):
    r = _ui.messageBox(text, title, adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                       adsk.core.MessageBoxIconTypes.QuestionIconType)
    return r == adsk.core.DialogResults.DialogYes


def _ask3(text, title=APP_NAME):
    """Ja/Nein/Abbrechen -> 'yes' | 'no' | 'cancel'."""
    r = _ui.messageBox(text, title, adsk.core.MessageBoxButtonTypes.YesNoCancelButtonType,
                       adsk.core.MessageBoxIconTypes.WarningIconType)
    if r == adsk.core.DialogResults.DialogYes:
        return 'yes'
    if r == adsk.core.DialogResults.DialogNo:
        return 'no'
    return 'cancel'


def _formatted_box(inputs, iid, name, text, rows):
    if hasattr(inputs, 'addFormattedTextBoxCommandInput'):
        return inputs.addFormattedTextBoxCommandInput(iid, name, text, rows)
    return inputs.addTextBoxCommandInput(iid, name, text, rows, True)


# ====================================================================== Auswahl (2.0.0: Palette statt Dialog)
def _cam_opts(design):
    """CAM-Optionen aus den gespeicherten Einstellungen (für Bearbeiten/Wiederholen älterer Projekte)."""
    st = F.read_settings_attr(design) if design is not None else {}
    return {k: st.get(k, v) for k, v in CAM.DEFAULTS.items()}


def _settings_to_params(s):
    return {
        'nest_laenge': s['length'], 'nest_breite': s['width'], 'nest_dicke': s['thickness'],
        'nest_rand': s['frame'], 'nest_abstand': s['spacing'], 'nest_rohteil_abstand': s['gap'],
        'nest_dicken_tol': s['tol'], 'nest_drehschritt': s['rot_step'],
        'nest_faser_y': 1.0 if s['grain'] == 'Y' else 0.0, 'nest_max_rohteile': s['max_sheets'],
        'nest_rechenzeit': s['time_limit'], 'nest_generationen': s['generations'],
        'nest_population': s['population'], 'nest_vakuum_gewicht': s['vac_weight'],
        'nest_vakuum_flaeche': s['vac_area_cm2'],
    }


def _part_warnings(res, s):
    """Liste kurzer Warnungen (deutsch) für eine Zeile der Auswahlliste."""
    w = []
    if res.get('error'):
        return ['✖ ' + res['error']]
    if res.get('convert_reason'):
        if s.get('convert'):
            w.append('→ wird in Komponente umgewandelt: ' + res['convert_reason'])
        elif not A.convert_blocks_part(res, False):
            extra = _t('warn.multibody_include',
                       'gewählter Körper wird berücksichtigt. Optional: Häkchen „Körper ohne eigene Komponente '
                       'umwandeln“, um nur diesen Körper auszulagern (ändert die Konstruktion).')
            w.append('⚠ ' + res['convert_reason'] + ((' – ' + extra) if extra else ''))
        else:
            w.append('✖ ' + res['convert_reason'])
    if 'thickness' in res and abs(res['thickness'] - s['thickness']) > s['tol']:
        w.append('⚠ Dicke %s mm' % G.de(res['thickness'], 1))
    if s['material'] and res.get('material'):
        a, b = s['material'].lower(), res['material'].lower()
        if a not in b and b not in a:
            w.append('⚠ Material „%s“' % res['material'])
    if 'outer' in res:
        mode = _state['part_settings'].get(res['key'], {}).get('mode', 'laengs')
        rots = G.allowed_rotations(mode, res['grain_angle'], s['grain'], s['rot_step'])
        if not G.part_fits_sheet(res['outer'], s, rots):
            w.append('✖ passt nicht auf das Rohteil (mit Faservorgabe)')
        if res['area'] < s['vac_area_cm2'] * 100.0:
            w.append('⚠ kleiner als Mindestfläche Vakuum')
    li = res.get('link_info') or {}
    if li.get('out_of_date'):
        w.append('⚠ verknüpfte Quelle „%s“ hat eine neuere Version (v%s → v%s)' % (
            li.get('document') or li.get('component'), li.get('version'), li.get('latest_version')))
    return w


def _part_usable(res, s):
    if res.get('error') or 'outer' not in res:
        return False
    if A.convert_blocks_part(res, bool((s or {}).get('convert'))):
        return False
    return True


def _instance_id(ent):
    """Eindeutige Instanz einer Auswahl: Occurrence-Pfad bzw. Kontext-Pfad des Körper-Proxys.
    Occurrence „Kocherbox:1+Deckel:1“ und ihr Körper zählen damit als EIN Stück."""
    occ = adsk.fusion.Occurrence.cast(ent)
    if occ:
        p = L.safe(lambda: occ.fullPathName, default='')
        if p and isinstance(p, str):
            return 'O:' + p
    ctx = L.safe(lambda: ent.assemblyContext, default=None)
    if ctx is not None and not isinstance(ctx, str):
        p = L.safe(lambda: ctx.fullPathName, default='')
        if p and isinstance(p, str):
            return 'O:' + p
    tok = L.safe(lambda: ent.entityToken, default='')
    if tok and isinstance(tok, str):
        return 'T:' + tok
    return 'E:%d' % id(ent)


def _geom_sig(body):
    """2.0.5: Geometrie-Fingerabdruck (Volumen, Fläche, Hüllquader) – geänderte Kontur/Stärke -> neue Analyse
    statt veralteter Zwischenspeicher (entityToken bleibt bei Geometrieänderung oft gleich)."""
    def r(v):
        try:
            return round(float(v), 5)
        except Exception:
            return None
    bb = L.safe(lambda: body.boundingBox, default=None)
    pts = ()
    if bb is not None and not isinstance(bb, str):
        pts = tuple(r(L.safe(f)) for f in (lambda: bb.minPoint.x, lambda: bb.minPoint.y, lambda: bb.minPoint.z,
                                             lambda: bb.maxPoint.x, lambda: bb.maxPoint.y, lambda: bb.maxPoint.z))
    return (r(L.safe(lambda: body.volume)), r(L.safe(lambda: body.area))) + pts


def _analyse_cached(ent, design, assembly=False):
    """resolve + analyze; die teure Analyse je Komponente/Körper nur einmal pro Sitzung."""
    res = F.resolve_entity(ent, design, assembly=assembly)
    if res['body'] is None or res['error']:
        return res, False
    tok = L.safe(lambda: res['body'].entityToken, default='')
    ck = (res.get('key'), tok if isinstance(tok, str) else '', _geom_sig(res['body']))
    cache = _state.setdefault('ana_cache', {})
    if ck in cache:
        hit = dict(cache[ck])
        hit.update(comp=res['comp'], body=res['body'], key=res['key'], convert_reason=res['convert_reason'],
                   linked=res.get('linked'), link_info=res.get('link_info'),
                   needs_convert=res.get('needs_convert'), multibody=res.get('multibody'))
        return hit, True
    res = F.analyze_part(res, 0.1)
    cache[ck] = {k: v for k, v in res.items() if k not in ('entity', 'src', 'count', 'instances')}
    return res, False


def _source_info(ent):
    """Diagnose: was genau wurde gewählt (Proxy/nativ, Pfad, Instanz-Matrix, verknüpft?, Elternpfad)."""
    d = {'type': L.safe(lambda: ent.objectType), 'name': L.safe(lambda: ent.name)}
    ctx = L.safe(lambda: ent.assemblyContext, default=False)
    if ctx and not isinstance(ctx, str):
        d['assemblyContext'] = L.safe(lambda: ctx.fullPathName)
        d['context_transform2'] = L.safe(lambda: L.mat(ctx.transform2))
    occ = adsk.fusion.Occurrence.cast(ent)
    if occ:
        d['fullPathName'] = L.safe(lambda: occ.fullPathName)
        d['transform2'] = L.safe(lambda: L.mat(occ.transform2))
        d['isReferencedComponent'] = L.safe(lambda: occ.isReferencedComponent)
    # 2.0.0 (f): verknüpft? + vollständiger Elternpfad
    lk = A.linked_parent(ent)
    d['in_referenced_component'] = lk is not None
    d['referenced_occurrence'] = A._g(lambda: lk.fullPathName, '') if lk is not None else ''
    d['link'] = A.link_info(lk) if lk is not None else None
    d['parent_path'] = A.parent_names(ent)
    d['parents_referenced'] = [bool(A._g(lambda: o.isReferencedComponent, False)) for o in reversed(A.chain(ent))]
    nat = L.safe(lambda: ent.nativeObject, default=False)
    d['is_proxy'] = bool(nat) and not isinstance(nat, str)
    comp = L.safe(lambda: (nat or ent).parentComponent, default=False)
    if comp and not isinstance(comp, str):
        d['component'] = L.safe(lambda: comp.name)
        d['component_bodies'] = L.safe(lambda: [b.name for b in comp.bRepBodies])
        d['component_sub_occurrences'] = L.safe(lambda: comp.occurrences.count)
        d['component_instances_in_design'] = L.safe(
            lambda: _design().rootComponent.allOccurrencesByComponent(comp).count)
    return d


def _sel():
    """Auswahlliste: key -> Eintrag {'res', 'instances', 'count', 'src', 'kind': teil|kopie|projekt, ...}."""
    return _state.setdefault('sel', {})


def _sel_order():
    return _state.setdefault('sel_order', [])


def _put_entry(key, entry):
    _state['sess_doc'] = _doc_key()            # 2.0.5: Auswahl gehört zum aktiven Dokument
    if key not in _sel():
        _sel_order().append(key)
    _sel()[key] = entry
    ps = _state['part_settings'].get(key) or {'mode': 'laengs', 'enabled': True}
    ps['sel_count'] = entry['count']
    if not ps.get('manual_qty'):
        ps['qty'] = max(entry['count'], int(ps.get('project_qty') or 0))
    _state['part_settings'][key] = ps



def _drop_sel_key(key):
    """Eintrag aus Auswahl und Teil-Einstellungen entfernen (Reihenfolge erhalten)."""
    if key in _sel():
        _sel().pop(key, None)
    if key in _sel_order():
        _sel_order().remove(key)
    _state['part_settings'].pop(key, None)


def _comp_body_id(comp, body):
    """Vergleichsschlüssel Komponente+Körper (für teil↔kopie-Bereinigung)."""
    ck = A.comp_key(comp) if comp is not None else ''
    bn = L.safe(lambda: body.name, default='') if body is not None else ''
    return '%s/%s' % (ck, bn)


def _entry_comp_body_id(e):
    if not isinstance(e, dict):
        return ''
    g = e.get('group') if isinstance(e.get('group'), dict) else None
    if g:
        return _comp_body_id(g.get('comp'), g.get('body'))
    res = e.get('res') or {}
    return _comp_body_id(res.get('comp'), res.get('body'))


def _put_kopie_from_group(g, n_ana_box=None):
    """Gruppe aus ln_assembly.collect / Body-Pfad als kind=kopie eintragen; Analyse einmalig."""
    res = {'comp': g['comp'], 'body': g['body'], 'key': g['key'], 'convert_reason': '', 'error': '',
           'name': g['name'], 'link_info': g.get('linked'),
           'linked': (g.get('linked') or {}).get('component')}
    try:
        res = F.analyze_part(res, 0.1)
        if n_ana_box is not None:
            n_ana_box[0] += 1
    except Exception as e:
        res['error'] = 'Analyse fehlgeschlagen: %s' % e
        L.exc('Analyse Baugruppenteil %s' % g['name'])
    _put_entry(g['key'], {'res': res, 'instances': list(g['paths']), 'count': g['count'], 'src': [],
                          'kind': 'kopie', 'group': g})
    return res


def _group_from_teil_entry(e, design):
    """Versuch, aus einem kind=teil-Eintrag eine Asm-Gruppe zu bauen (ents oder res.body/comp)."""
    res = e.get('res') or {}
    ents = list(e.get('ents') or [])
    # 1) gespeicherte Entitäten bevorzugen (Occurrence → collect, Body → Einzelgruppe)
    for ent in ents:
        occ = adsk.fusion.Occurrence.cast(ent)
        if occ:
            groups, _st = A.collect([occ])
            return groups
        body = adsk.fusion.BRepBody.cast(ent)
        nat = (L.safe(lambda: body.nativeObject) or body) if body else None
        if nat is None:
            continue
        comp = nat.parentComponent
        lk = A.linked_parent(ent)
        return [{'key': 'A:%s/%s' % (A.comp_key(comp), nat.name), 'comp': comp, 'body': nat,
                 'body_name': nat.name, 'name': (comp.name if comp != design.rootComponent else nat.name),
                 'count': max(1, int(e.get('count') or 1)), 'paths': [A.occ_path(ent)],
                 'linked': A.link_info(lk) if lk else None, 'solids': _solid_count(comp)}]
    # 2) Fallback: Körper/Komponente aus der Analyse (Linked-Fehler behält body)
    body, comp = res.get('body'), res.get('comp')
    if body is None:
        return []
    nat = L.safe(lambda: body.nativeObject) or body
    if nat is None:
        return []
    if comp is None:
        comp = L.safe(lambda: nat.parentComponent)
    if comp is None:
        return []
    lk_info = res.get('link_info')
    paths = list(e.get('instances') or []) or ['']
    name = res.get('name') or (comp.name if comp != design.rootComponent else L.safe(lambda: nat.name, '?'))
    return [{'key': 'A:%s/%s' % (A.comp_key(comp), L.safe(lambda: nat.name, 'Körper')),
             'comp': comp, 'body': nat, 'body_name': L.safe(lambda: nat.name, 'Körper'),
             'name': name, 'count': max(1, int(e.get('count') or 1)), 'paths': paths,
             'linked': lk_info if isinstance(lk_info, dict) else None, 'solids': _solid_count(comp)}]


def _solid_count(comp):
    try:
        return sum(1 for b in comp.bRepBodies if b.isSolid)
    except Exception:
        return 1


def _group_is_root(g, design):
    try:
        c = (g or {}).get('comp')
        return c is not None and design is not None and c == design.rootComponent
    except Exception:
        return False


def _teil_needs_asm(e):
    """Einzelteil-Zeile, die im Baugruppenmodus als Kopie geführt werden sollte."""
    if e.get('kind', 'teil') != 'teil':
        return False
    res = e.get('res') or {}
    err = res.get('error') or ''
    if res.get('linked') or res.get('link_info'):
        return True
    if 'verknüpft' in err or 'Unterkomponent' in err:
        return True
    return False


def _drop_teil_matching_groups(groups):
    """2.0.7: vor A:-Keys alte kind=teil mit gleicher Komponente/Körper entfernen."""
    ids = set(_comp_body_id(g.get('comp'), g.get('body')) for g in groups)
    ids.discard('/')
    gone = []
    for key in list(_sel_order()):
        e = _sel().get(key)
        if not e or e.get('kind', 'teil') != 'teil':
            continue
        if _entry_comp_body_id(e) in ids:
            gone.append(key)
    for key in gone:
        _drop_sel_key(key)
    return gone


def _set_assembly(on):
    """2.0.7: Baugruppenmodus setzen und Auswahlliste dazu synchronisieren.
    on: teil mit Linked-/Unterkomponenten-Fehler → kopie (A:…) oder entfernen.
    off: kopie bleiben; teil aus ents mit assembly=False neu analysieren."""
    on = bool(on)
    prev = bool(_state.get('assembly'))
    _state['assembly'] = on
    design = _design()
    converted, dropped, refreshed = 0, 0, 0
    notice = ''
    if on and design:
        for key in list(_sel_order()):
            e = _sel().get(key)
            if not e or not _teil_needs_asm(e):
                continue
            groups = _group_from_teil_entry(e, design)
            _drop_sel_key(key)
            if not groups:
                dropped += 1
                continue
            _drop_teil_matching_groups(groups)  # idempotent
            for g in groups:
                old = _sel().get(g['key'])
                if old is not None and isinstance(old.get('group'), dict):
                    new_paths = [p for p in g['paths'] if p not in old['group']['paths']]
                    old['group']['paths'].extend(new_paths)
                    old['group']['count'] = len(old['group']['paths'])
                    old['count'] = old['group']['count']
                    _put_entry(g['key'], old)
                else:
                    box = [0]
                    _put_kopie_from_group(g, box)
                converted += 1
        if converted or dropped:
            notice = ('Baugruppenmodus an: %d Teil(e) als Kopie übernommen' % converted) if converted else ''
            if dropped:
                notice = (notice + ('; ' + ' | ' if notice else '') +
                          '%d Teil(e) entfernt – bitte mit Baugruppenmodus neu wählen' % dropped)
            L.info('setAssembly on: converted=%d dropped=%d', converted, dropped)
    elif not on and design:
        # teil aus ents neu auflösen (Linked-Fehler wieder sichtbar); kopie unverändert
        for key in list(_sel_order()):
            e = _sel().get(key)
            if not e or e.get('kind', 'teil') != 'teil':
                continue
            ents = list(e.get('ents') or [])
            if not ents:
                continue
            try:
                res, _c = _analyse_cached(ents[0], design, assembly=False)
            except Exception as ex:
                res = dict(e.get('res') or {})
                res['error'] = 'Analyse fehlgeschlagen: %s' % ex
            e['res'] = res
            _put_entry(key, e)
            refreshed += 1
        if refreshed:
            notice = 'Baugruppenmodus aus: %d Einzelteil(e) neu geprüft' % refreshed
            L.info('setAssembly off: refreshed=%d', refreshed)
    elif on != prev:
        L.info('setAssembly %s (keine Konstruktion)', 'on' if on else 'off')
    return {'assembly': on, 'rows': _rows() if design else [], 'notice': notice,
            'converted': converted, 'dropped': dropped, 'refreshed': refreshed}

def _add_entities(ents, assembly):
    """Gewählte Entitäten in die Auswahlliste aufnehmen (additiv). Baugruppenmodus: Occurrences rekursiv
    sammeln, je (Komponente, Körper) ein Eintrag 'kopie' – die Kopie entsteht erst beim Nesten."""
    design = _design()
    logged = _state.setdefault('logged_inst', set())
    n_new, n_ana = 0, 0
    for i, ent in enumerate(ents):
        iid = _instance_id(ent)
        if iid not in logged:
            logged.add(iid)
            L.info('Auswahl %d: %s inst=%s src=%s', i, L.safe(lambda: ent.name), iid,
                   json.dumps(_source_info(ent), default=str))
        occ = adsk.fusion.Occurrence.cast(ent)
        if assembly:
            if occ:
                groups, st = A.collect([occ])
                L.info('Baugruppenmodus %s: %s', L.safe(lambda: occ.fullPathName), json.dumps(st))
            else:
                body = adsk.fusion.BRepBody.cast(ent)
                nat = (L.safe(lambda: body.nativeObject) or body) if body else None
                if nat is None:
                    continue
                comp = nat.parentComponent
                lk = A.linked_parent(ent)
                groups = [{'key': 'A:%s/%s' % (A.comp_key(comp), nat.name), 'comp': comp, 'body': nat,
                           'body_name': nat.name, 'name': comp.name if comp != design.rootComponent else nat.name,
                           'count': 1, 'paths': [A.occ_path(ent)], 'linked': A.link_info(lk) if lk else None,
                           'solids': _solid_count(comp)}]
            # 2.0.7: alte kind=teil derselben Komponente/Körper entfernen (keine Doppel-Fehlerzeilen)
            _drop_teil_matching_groups(groups)
            for g in groups:
                old = _sel().get(g['key'])
                # 2.0.4: Eintrag aus einem Projekt (Bearbeiten, kind 'projekt') hat keine Gruppe -> KeyError in
                # 2.0.3; jetzt wird er durch die frisch gewählte Kopie (aktuelle Geometrie) ersetzt.
                if old is not None and isinstance(old.get('group'), dict):
                    new_paths = [p for p in g['paths'] if p not in old['group']['paths']]
                    old['group']['paths'].extend(new_paths)
                    old['group']['count'] = len(old['group']['paths'])
                    old['count'] = old['group']['count']
                    _put_entry(g['key'], old)
                    continue
                box = [0]
                _put_kopie_from_group(g, box)
                n_ana += box[0]
                n_new += 1
            continue
        try:
            res, cached = _analyse_cached(ent, design)
            n_ana += 0 if cached else 1
        except Exception as e:
            res = {'name': L.safe(lambda: ent.name, default='?'), 'error': 'Analyse fehlgeschlagen: %s' % e,
                   'key': 'E:%s' % iid}
            L.exc('Analyse')
        key = res.get('key') or 'E:%s' % iid
        res['key'] = key
        e = _sel().get(key)
        if e is not None:
            if iid not in e['instances']:
                e['instances'].append(iid)
                e['count'] = len(e['instances'])
                e.setdefault('ents', []).append(ent)
            _put_entry(key, e)
            continue
        _put_entry(key, {'res': res, 'instances': [iid], 'count': 1, 'src': [], 'kind': 'teil', 'ents': [ent]})
        n_new += 1
    L.info('Auswahl ergänzt: %d neue Einträge, %d analysiert, Liste jetzt %d (%s)', n_new, n_ana, len(_sel_order()),
           ', '.join('%s %d' % (_sel()[k]['res'].get('name', '?'), _state['part_settings'][k]['qty'])
                     for k in _sel_order()))


def _dims(outer):
    try:
        b = G.bbox2d(outer)
        return [round(b[2] - b[0], 1), round(b[3] - b[1], 1)]
    except Exception:
        return None


def _rows(s=None):
    """Auswahlliste für die Palette."""
    design = _design()
    s = s or (_initial_settings(design) if design else None)
    out = []
    for key in _sel_order():
        e = _sel()[key]
        res = e['res']
        ps = _state['part_settings'].setdefault(key, {'mode': 'laengs', 'qty': 1, 'enabled': True})
        w = _part_warnings(res, s) if s else []
        out.append({'key': key, 'name': res.get('name', '?'), 'count': e.get('count', 1), 'qty': int(ps.get('qty', 1)),
                    'mode': ps.get('mode', 'laengs'), 'enabled': bool(ps.get('enabled', True)),
                    'projectQty': int(ps.get('project_qty') or 0), 'kind': e.get('kind', 'teil'),
                    'thickness': res.get('thickness'), 'areaCm2': round((res.get('area') or 0) / 100.0, 1),
                    'dims': _dims(res['outer']) if res.get('outer') else None,
                    'grainAngle': res.get('grain_angle'), 'linked': res.get('linked') or '',
                    'outOfDate': bool((res.get('link_info') or {}).get('out_of_date')),
                    'error': res.get('error') or '', 'warnings': w,
                    'hintConvert': bool(res.get('convert_reason')),
                    'multibody': bool(res.get('multibody')),
                    'usable': bool(s and _part_usable(res, s) and not any(x.startswith('✖ passt') for x in w))})
    return out


FLOAT_KEYS = ('length', 'width', 'thickness', 'frame', 'spacing', 'rot_step', 'gap', 'tol', 'vac_area_cm2')
INT_KEYS = ('max_sheets', 'time_limit', 'generations', 'population', 'vac_weight', 'cam_prog')
BOOL_KEYS = ('convert', 'stock_body', 'joints', 'replace', 'cam')


def _coerce_settings(design, raw):
    """Einstellungen aus der Palette (gleiche Schlüssel wie SETTING_KEYS) auf Vorgaben legen und typisieren."""
    s = _initial_settings(design)
    for k in SETTING_KEYS:
        if k not in (raw or {}):
            continue
        v = raw[k]
        try:
            if k in FLOAT_KEYS:
                s[k] = float(v)
            elif k in INT_KEYS:
                s[k] = int(round(float(v)))
            elif k in BOOL_KEYS:
                s[k] = bool(v)
            elif v is not None:
                s[k] = str(v)
        except (TypeError, ValueError):
            L.warn('Einstellung %s=%r ungültig – Vorgabe %r bleibt', k, v, s.get(k))
    s['grain'] = 'Y' if s.get('grain') == 'Y' else 'X'
    s['feat_view'] = 'all' if s.get('feat_view') == 'all' else 'top'
    s['joints'] = False
    if s.get('fix') not in [k for k, _l in F.FIX_MODES]:
        s['fix'] = 'ground'
    return s


def _prepare_nest(req):
    """„Auswahl übernehmen & nesten“ (im internen Befehl CMD_PREP, darf die Konstruktion ändern: Parameter,
    Komponente aus Körper, Baugruppen-Kopien). Baut den Job und lädt ihn in die Palette."""
    design = _design()
    if not design:
        _to_palette('notice', {'level': 'error', 'text': 'Keine Konstruktion aktiv.'})
        return
    s = _coerce_settings(design, req.get('settings') or {})
    errs = G.validate_settings(s)
    if errs:
        _to_palette('notice', {'level': 'error', 'text': ' '.join(errs), 'tab': 'Platte'})
        return
    for key, d in (req.get('parts') or {}).items():
        ps = _state['part_settings'].get(key)
        if ps is None:
            continue
        if 'qty' in d and int(d['qty']) != int(ps.get('qty', 0)):
            ps['manual_qty'] = True
            ps['qty'] = max(0, int(d['qty']))
        if d.get('mode') in ('laengs', 'quer', 'frei'):
            ps['mode'] = d['mode']
        if 'enabled' in d:
            ps['enabled'] = bool(d['enabled'])
    F.write_params(design, _settings_to_params(s))      # nur parametrisch (sonst übersprungen)
    F.write_attr(design, 'settings', {k: s[k] for k in SETTING_KEYS if k in s})
    _validate_session('Nesten')               # 2.0.5: gelöschte Rohteile/Teile -> frischer Kontext
    cur_jid = ((_state.get('ctx') or {}).get('job') or {}).get('jobId')
    req_jid = req.get('jobId') or ''
    stale = bool(req_jid) and req_jid != cur_jid      # Palette zeigt einen Auftrag, den Fusion nicht mehr kennt
    if stale:
        L.info('Nesten: Palette-Stand von Job %s verworfen (aktueller Job %s)', req_jid, cur_jid or '-')
    if req.get('layout') and _state.get('ctx') and not stale:
        # wie früher „Zurück“: Rohteil-Definitionen/Anordnung (Bearbeiten) bleiben erhalten
        _state['back_from_job'] = req_jid or (_state['ctx'].get('job') or {}).get('jobId') or '?'
        _state['back_layout'] = req.get('layout') or {}
    if req.get('overrides') is not None and not stale:
        _state['overrides'] = req.get('overrides') or {}
    elif stale:
        _state['overrides'] = {}
    parts_json, ctx_parts, skipped = [], {}, []
    pending_copies, skip_keys = [], set()
    for key in _sel_order():
        e = _sel()[key]
        res = e['res']
        ps = _state['part_settings'][key]
        if not (ps.get('enabled', True) and int(ps.get('qty', 0)) > 0):
            continue
        if e.get('kind') == 'projekt':
            jp = dict(e['jp'], qty=int(ps['qty']), grainMode=ps.get('mode', e['jp'].get('grainMode', 'laengs')))
            parts_json.append(jp)
            ctx_parts[jp['id']] = e['ctx']
            continue
        if not _part_usable(res, s):
            skipped.append({'name': res.get('name', '?'), 'reason': res.get('error') or res.get('convert_reason')
                            or 'nicht verwendbar'})
            skip_keys.add(key)
            continue
        rots = G.allowed_rotations(ps['mode'], res['grain_angle'], s['grain'], s['rot_step'])
        if not G.part_fits_sheet(res['outer'], s, rots):
            skipped.append({'name': res.get('name', '?'), 'reason': 'zu groß für das Rohteil (mit Faservorgabe)'})
            skip_keys.add(key)
            continue
        if e.get('kind') == 'kopie':
            g = e.get('group') or {}
            if A.copy_needed(g, is_root=_group_is_root(g, design)):
                pending_copies.append(key)
            else:
                L.info('Baugruppenmodus: %s als Original (keine Kopie nötig)', g.get('name') or key)
    copies_log = []
    nest_warn = []
    for k in pending_copies:
        li = (_sel()[k].get('group') or {}).get('linked') or {}
        if li.get('out_of_date'):
            nest_warn.append({'name': _sel()[k]['group'].get('name', '?'),
                              'reason': 'Quelle „%s“ ist veraltet (v%s, neueste v%s) – Kopie entspricht dem geladenen '
                                        'Stand. Verknüpfung aktualisieren, dann „Kopien aktualisieren“.' % (
                                            li.get('document') or li.get('component'), li.get('version'),
                                            li.get('latest_version'))})
    if nest_warn:
        L.warn('Baugruppenmodus: %d Kopie(n) aus veralteten Quellen: %s', len(nest_warn),
               '; '.join(w['name'] for w in nest_warn))
    if pending_copies:
        made = F.make_copies(design, [_sel()[k]['group'] for k in pending_copies], copies_log)
        seen_fb = set()
        for key, (g, comp, body, err) in zip(pending_copies, made):
            action = A.copy_fallback_action(g, comp, body, err, seen_fb)
            if action == 'copy':
                _sel()[key]['res'].update(comp=comp, body=body)
                _sel()[key]['copy_comp'] = comp
                continue
            if action == 'fallback':
                dk = A.fallback_dedupe_key(g)
                if dk:
                    seen_fb.add(dk)
                err_short = err or 'Kopie fehlgeschlagen'
                if err_short.startswith('Kopie fehlgeschlagen: '):
                    err_short = err_short[len('Kopie fehlgeschlagen: '):]
                nest_warn.append({
                    'name': g.get('name', '?'),
                    'reason': _t('warn.copy_fallback',
                                 'Kopie fehlgeschlagen ({err}) – Original wird genestet.', err=err_short)})
                L.warn('Baugruppenmodus: %s – Fallback auf Original (%s)', g.get('name'), err or err_short)
                continue
            if action == 'dup':
                skip_keys.add(key)
                L.info('Baugruppenmodus: %s übersprungen (gleiche Komponente bereits als Original)',
                       (g or {}).get('name') or key)
                continue
            skipped.append({'name': (g or {}).get('name', '?'), 'reason': err or 'Kopie fehlgeschlagen'})
            skip_keys.add(key)
    for key in _sel_order():
        e = _sel()[key]
        if e.get('kind') == 'projekt' or key in skip_keys:
            continue
        res = e['res']
        ps = _state['part_settings'][key]
        if not (ps.get('enabled', True) and int(ps.get('qty', 0)) > 0) or not _part_usable(res, s):
            continue
        comp = res.get('comp')
        if res.get('convert_reason') and s.get('convert'):
            try:
                comp, _nb = F.convert_body_to_component(res['body'])
            except Exception as ex:
                L.exc('Komponente aus Körper')
                skipped.append({'name': res.get('name', '?'), 'reason': 'Umwandeln fehlgeschlagen: %s' % ex})
                continue
        pid = key                 # stabile ID (Komponente) -> Fixierungen überleben erneutes Nesten
        parts_json.append({'id': pid, 'name': res['name'], 'outer': res['outer'], 'holes': res['holes'],
                           'grainAngle': res['grain_angle'], 'grainMode': ps['mode'], 'qty': int(ps['qty']),
                           'enabled': True, 'thickness': res['thickness'],
                           'features': res.get('features') or []})
        ctx_parts[pid] = {'comp': comp, 'frame': res['frame'], 'name': res['name'],
                          'token': comp.entityToken if comp else '',
                          'src': e.get('src'), 'diag': res.get('diag'),
                          'copy_of': (e.get('group') or {}).get('paths') if e.get('kind') == 'kopie' else None}
    _state['nest_skipped'] = skipped
    if not parts_json:
        _to_palette('notice', {'level': 'error', 'text': 'Keine verwendbaren Teile.', 'skipped': skipped,
                               'tab': 'Auswahl'})
        return
    job = G.build_job(s, parts_json, uuid.uuid4().hex[:12])
    job['seed'] = int(time.time()) % 100000
    ids = set(p['id'] for p in parts_json)
    qtys = dict((p['id'], int(p.get('qty') or 0)) for p in parts_json)

    def _ov_ok(k):          # 2.0.5: nur Overrides mit Gegenstück (Teil vorhanden, Kopie-Nr. < Menge)
        pid, _s, n = k.rpartition('#')
        if pid not in ids:
            return False
        try:
            return int(n) < qtys.get(pid, 0)
        except Exception:
            return False
    job['overrides'] = {k: v for k, v in (_state.get('overrides') or {}).items() if _ov_ok(k)}
    opts = {k: s[k] for k in ('stock_body', 'joints', 'replace', 'gap', 'fix')}
    opts.update({k: s.get(k, v) for k, v in CAM.DEFAULTS.items()})
    job['defaultZero'] = s.get('cam_origin') or 'lu_unten'
    job['skipped'] = skipped
    job['warnings'] = nest_warn
    _attach_edit_layout(job)
    _start_job(design, job, ctx_parts, opts)
    if copies_log:
        L.info('Baugruppenmodus: %s', '; '.join(copies_log))
    if skipped:
        _to_palette('notice', {'level': 'warn', 'skipped': skipped,
                               'text': '%d Teil(e) nicht berücksichtigt – siehe Ergebnis.' % len(skipped)})


def _project_entries(project, ctx_parts, job):
    """Projekt (Bearbeiten/Wiederholen) als Einträge der Auswahlliste."""
    for jp in job['parts']:
        key = jp['id']
        info = ctx_parts.get(key) or {}
        res = {'name': jp.get('name', '?'), 'outer': jp.get('outer'), 'holes': jp.get('holes', []),
               'grain_angle': jp.get('grainAngle', 0.0), 'thickness': jp.get('thickness', 0.0),
               'area': abs(G.area2d(jp['outer'])) if jp.get('outer') else 0.0, 'key': key,
               'comp': info.get('comp'), 'frame': info.get('frame')}
        if key not in _sel():
            _sel_order().append(key)
        _sel()[key] = {'res': res, 'instances': [], 'count': int(jp.get('qty', 1)), 'kind': 'projekt', 'jp': jp,
                       'ctx': info}


SETTING_KEYS = ('length', 'width', 'thickness', 'material', 'grain', 'frame', 'spacing', 'rot_step', 'max_sheets',
                'time_limit', 'generations', 'population', 'gap', 'tol', 'vac_weight', 'vac_area_cm2',
                'convert', 'stock_body', 'joints', 'replace', 'fix', 'feat_view',
                'cam', 'cam_origin', 'cam_stock', 'cam_prog')
PREFS_FILE = os.path.join(HERE, 'user_prefs.json')      # benutzerweite Einstellungen (nicht im Zip)



def _lang():
    try:
        return ca_i18n.normalize_lang(_read_prefs().get('language') or 'de')
    except Exception:
        return 'de'


def _t(key, fallback='', **kw):
    """i18n-Text mit Fallback (fehlender Schlüssel oder Formatfehler → fallback)."""
    try:
        s = ca_i18n.t(key, _lang(), **kw)
        if s and s != key:
            return s
    except Exception:
        pass
    if fallback and kw:
        try:
            return fallback.format(**kw)
        except Exception:
            return fallback
    return fallback

def _i18n_payload():
    return ca_i18n.payload(_lang())

def _read_prefs():
    try:
        with open(PREFS_FILE, 'r', encoding='utf-8') as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_prefs(**kw):
    d = _read_prefs()
    d.update(kw)
    try:
        with open(PREFS_FILE, 'w', encoding='utf-8') as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
    except Exception:
        L.exc('Einstellungen schreiben')


def _feat_view(design=None):
    """Ansicht der Bearbeitungen in der Palette: 'top' (Nur oben, Standard) | 'all' (Alle gestrichelt)."""
    v = None
    if design is not None:
        v = F.read_settings_attr(design).get('feat_view')
    if v not in ('top', 'all'):
        v = _read_prefs().get('feat_view')
    return 'all' if v == 'all' else 'top'


def _set_feat_view(design, v):
    v = 'all' if v == 'all' else 'top'
    _write_prefs(feat_view=v)
    if design is not None:
        st = F.read_settings_attr(design)
        st['feat_view'] = v
        F.write_attr(design, 'settings', st)
    return v


def _stock_lib():
    lib = _state.get('stock_lib')
    if lib is None:
        lib = STK.load(HERE)
        _state['stock_lib'] = lib
    return lib


def _initial_settings(design):
    """Vorbelegung: Standard/Benutzerparameter (nur parametrisch) < Design-Attribut < Werte aus der Palette."""
    pv = F.read_params(design)
    s = {'length': pv['nest_laenge'], 'width': pv['nest_breite'], 'thickness': pv['nest_dicke'], 'material': '',
         'grain': 'Y' if pv['nest_faser_y'] >= 0.5 else 'X', 'frame': pv['nest_rand'], 'spacing': pv['nest_abstand'],
         'rot_step': pv['nest_drehschritt'], 'max_sheets': int(pv['nest_max_rohteile']),
         'time_limit': int(pv['nest_rechenzeit']), 'generations': int(pv['nest_generationen']),
         'population': int(pv['nest_population']), 'gap': pv['nest_rohteil_abstand'], 'tol': pv['nest_dicken_tol'],
         'vac_weight': int(pv['nest_vakuum_gewicht']), 'vac_area_cm2': float(pv['nest_vakuum_flaeche']),
         'convert': False, 'stock_body': True, 'joints': False, 'replace': False, 'fix': 'ground',
         'feat_view': _feat_view()}
    s.update(CAM.DEFAULTS)
    st = F.read_settings_attr(design)
    for k in SETTING_KEYS:
        if k in st:
            s[k] = st[k]
    rs = (_state.get('restore') or {}).get('settings') or {}
    for src, dst in (('length', 'length'), ('width', 'width'), ('frame', 'frame'), ('spacing', 'spacing'),
                     ('grain', 'grain'), ('rotStep', 'rot_step'), ('vacuumWeight', 'vac_weight'),
                     ('thickness', 'thickness'), ('material', 'material'), ('featView', 'feat_view')):
        if rs.get(src) is not None:
            s[dst] = rs[src]
    return s


def _attach_edit_layout(job):
    """„Bearbeiten“ → Zurück → OK: vorhandene Anordnung (Stand der Palette) behalten; neu gewählte Stücke
    platziert die Palette in freie Flächen bzw. auf neue Rohteile (bis Max. Anzahl Rohteile)."""
    prev = _state.get('ctx') or {}
    pproj = prev.get('project') or {}
    if not _state.get('back_from_job'):
        return
    lay = _state.get('back_layout') or {}
    # 1.10.0: Rohteil-Definitionen (Abschnitte, längs/quer, Nullpunkt, Reihenfolge) überleben „Zurück“ immer
    defs = lay.get('defs')
    if defs is None:
        defs = (prev.get('job') or {}).get('sheets') or []
    job['sheets'] = [dict(d) for d in defs if isinstance(d, dict)]
    if pproj.get('mode') != 'bearbeiten':
        return
    sheets = lay.get('sheets')
    if sheets is None:
        sheets = ((prev.get('job') or {}).get('initialLayout') or {}).get('sheets') or []
        dims = (prev.get('job') or {}).get('sheet') or {}
    else:
        dims = lay.get('sheet') or {}
    if dims and (abs(float(dims.get('length', 0)) - job['sheet']['length']) > 1e-6
                 or abs(float(dims.get('width', 0)) - job['sheet']['width']) > 1e-6):
        L.info('Bearbeiten: Rohteilmaß geändert – Anordnung wird neu berechnet (fixierte Teile bleiben)')
        return
    qty = dict((p['id'], int(p['qty'])) for p in job['parts'])
    init = []
    for S in sheets:
        keep = [dict(p) for p in S if p.get('id') in qty and int(str(p.get('key', '#0')).rsplit('#', 1)[-1]) < qty[p['id']]]
        init.append(keep)
    dl = list(job['sheets'])
    while init and not init[-1] and not (len(dl) >= len(init) and dl[len(init) - 1].get('kind') == 'abschnitt'):
        init.pop()
    if not any(init):
        return
    job['initialLayout'] = {'sheets': init}
    if dl and len(dl) >= len(init):
        job['initialLayout']['defs'] = dl[:len(init)]
    job['maxSheets'] = max(int(job.get('maxSheets', 1)), len(init))
    job['project'] = {'id': pproj.get('id'), 'version': pproj.get('version')}
    n_old = sum(len(S) for S in init)
    L.info('Bearbeiten: %d Stück behalten ihre Lage, %d Stück neu (Palette platziert sie in freie Flächen)',
           n_old, sum(qty.values()) - n_old)


# ====================================================================== Palette / Job (Hauptfenster)
def _store_last_job(design, job, ctx_parts, opts):
    data = {'job': {k: v for k, v in job.items() if k != 'stock'}, 'opts': opts, 'overrides': job.get('overrides', {}),
            'parts': {pid: {'token': p['token'], 'frame': p['frame'], 'name': p['name']} for pid, p in ctx_parts.items()}}
    F.write_attr(design, 'lastJob', data)


def _start_job(design, job, ctx_parts, opts, mode='neu', project=None):
    """mode: 'neu' | 'bearbeiten' (ersetzt das Projekt) | 'wiederholen' (neues Projekt aus altem)."""
    origin = _state.pop('back_from_job', None)
    label = mode
    if origin:
        prevp = (_state.get('ctx') or {}).get('project')
        if prevp and project is None:          # erneut nesten aus „Bearbeiten“/„Wiederholen“: Projekt bleibt
            project = prevp
            mode = prevp.get('mode', mode)
        label = ('neu genestet (vorher Job %s)' % origin) if mode == 'neu' else \
            '%s (neu genestet, vorher Job %s)' % (mode, origin)
    job['version'] = VERSION
    job['featView'] = _feat_view(design)
    try:
        job['stock'] = STK.for_palette(_stock_lib())
    except Exception:
        L.exc('Rohteilbibliothek laden')
        job['stock'] = []
    _state['ctx'] = {'job': job, 'parts': ctx_parts, 'origin': label, 'project': project}
    _state['ctx_doc'] = _doc_key()
    _state['opts'] = opts
    _state['result'] = None
    L.info('Job %s gestartet (%s), %d Teiltypen%s', job.get('jobId'), _state['ctx']['origin'], len(ctx_parts),
           (', Projekt %s v%s' % (project.get('id'), project.get('version'))) if project else '')
    try:
        _store_last_job(design, job, ctx_parts, opts)
    except Exception:
        pass
    pal = _show_palette()
    if pal and _state.get('pal_ready'):
        _to_palette('rows', {'rows': _rows(), 'assembly': bool(_state.get('assembly'))})
        _to_palette('job', job)


def _to_palette(action, data):
    """Nachricht an die Palette (nie fatal). Vor 'ready' gehen Nachrichten verloren – die Palette holt den
    gesamten Stand dann mit 'ready' (Sitzung inkl. Job)."""
    pal = L.safe(lambda: _ui.palettes.itemById(PALETTE_ID))
    if not pal or isinstance(pal, str):
        return None
    try:
        txt = data if isinstance(data, str) else json.dumps(data, default=str)
        r = pal.sendInfoToHTML(action, txt)
        L.info('sendInfoToHTML(%s, %d Zeichen) -> %r', action, len(txt), r)
        return r
    except Exception:
        L.exc('sendInfoToHTML(%s)' % action)
        return None


def _license_status():
    """OSS: immer frei (kein Schlüssel/Trial)."""
    return {'line': 'Open Source – kostenlos', 'licensed': True, 'apply_allowed': True,
            'machine_id': '', 'enforce': False, 'edition': 'OSS', 'products_line': ''}



def _theme():
    t, src = TH.detect(_app, L.info)
    if _state.get('theme_src') != src:
        _state['theme_src'] = src
        L.info('Farbschema: %s (%s)', t, src)
    return t


def _session():
    """Gesamtstand für die Palette ('ready'): Version, Theme, Einstellungen, Auswahl, Job, Lizenz, Vorlieben."""
    design = _design()
    s = _initial_settings(design) if design else None
    prefs = _read_prefs()
    ctx = _state.get('ctx')
    lic = _license_status()
    return {'version': VERSION, 'buildDate': BUILD_DATE, 'theme': _theme(), 'settings': s,
            'rows': _rows(s) if design else [], 'assembly': bool(_state.get('assembly')),
            'job': ctx['job'] if ctx else None, 'stock': STK.for_palette(_stock_lib()),
            'options': {'rotSteps': G.ROT_STEPS, 'grainModes': G.GRAIN_MODES, 'fixModes': F.FIX_MODES,
                        'camOrigins': [(k, lbl) for k, lbl, _v in CAM.ORIGINS], 'camStocks': list(CAM.STOCKS)},
            'prefs': {'lastTab': prefs.get('last_tab') or 'Vorschau'}, 'license': lic, 'i18n': _i18n_payload(),
            'info': DIAG.info_payload(PRODUCT, APP_NAME, VERSION, BUILD_DATE, lic),
            'logDir': L.LOG_DIR, 'logFile': L.LOG_FILE, 'design': bool(design),
            'designType': ('parametrisch' if F.is_parametric(design) else 'direkt') if design else '',
            'hasCopies': bool(design and F.copy_components(design)), 'migration': _state.get('migration'),
            'applied': _state.get('applied_info')}


def _palette_size():
    p = _read_prefs()
    w, h = p.get('pal_w'), p.get('pal_h')
    ok = isinstance(w, int) and isinstance(h, int) and 320 <= w <= 4000 and 360 <= h <= 4000
    return (w, h) if ok else (PAL_W, PAL_H)


def _remember_size(pal=None):
    pal = pal or L.safe(lambda: _ui.palettes.itemById(PALETTE_ID))
    if not pal or isinstance(pal, str):
        return
    w, h = L.safe(lambda: pal.width), L.safe(lambda: pal.height)
    if isinstance(w, int) and isinstance(h, int) and w >= 320 and h >= 360:
        _write_prefs(pal_w=w, pal_h=h)


def _show_palette():
    """Palette EINMAL anlegen und nur einmal mit Handlern verbinden; danach wiederverwenden.
    (Löschen + Neuanlegen mit gleicher ID konnte zu doppelt verarbeiteten Palette-Nachrichten führen.)"""
    pal = _ui.palettes.itemById(PALETTE_ID)
    if pal and _state.get('pal_hooked'):
        pal.isVisible = True
        L.info('Palette wiederverwendet')
        return pal
    if pal:
        pal.deleteMe()              # fremder/alter Stand ohne unsere Handler
    _state['pal_ready'] = False
    url = os.path.join(HERE, 'palette', 'index.html').replace('\\', '/')
    w, h = _palette_size()
    pal = _ui.palettes.add(PALETTE_ID, 'CA LazyNest %s' % VERSION, url, True, True, True, w, h)
    try:
        pal.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
    except Exception:
        pass
    try:
        pal.setMinimumSize(340, 420)
    except Exception:
        pass
    h_ = PaletteHTML()
    pal.incomingFromHTML.add(h_)
    handlers.append(h_)
    hc = PaletteClosed()
    pal.closed.add(hc)
    handlers.append(hc)
    _state['pal_hooked'] = True
    L.info('Palette angelegt (%d×%d), HTML-Handler %x verbunden', w, h, id(h_))
    return pal


def _open_main(assembly=None):
    """Befehl „CA LazyNest“: Hauptfenster öffnen; aktuelle Fusion-Auswahl wird übernommen."""
    if assembly is not None:
        _state['assembly'] = bool(assembly)
    try:
        sels = _ui.activeSelections
        ents = [sels.item(i).entity for i in range(sels.count)]
        ents = [e for e in ents if adsk.fusion.Occurrence.cast(e) or adsk.fusion.BRepBody.cast(e)]
    except Exception:
        ents = []
    _validate_session('Öffnen')           # 2.0.5: anderes Dokument / gelöschte Rohteile -> vorher verwerfen
    if ents:
        _add_entities(ents, bool(_state.get('assembly')))
    _check_session_and_orphans()          # 2.0.4: nie blockierend
    _show_palette()
    if _state.get('pal_ready'):
        _to_palette('session', _session())
        if _state.get('open_notice'):
            _to_palette('notice', {'level': 'info', 'text': _state.pop('open_notice')})


def _diag_text():
    lic = _license_status()
    design = _design()
    rows = ['CA LazyNest %s (Build %s)' % (VERSION, BUILD_DATE),
            'Fusion %s, Python %s, %s' % (L.safe(lambda: _app.version), sys.version.split()[0], platform.platform()),
            'Pfad: %s' % HERE, 'Protokoll: %s' % L.LOG_FILE,
            'Farbschema: %s (%s)' % (_theme(), _state.get('theme_src')),
            'Konstruktion: %s' % (('%s, %s' % (L.safe(lambda: _app.activeDocument.name),
                                               'parametrisch' if F.is_parametric(design) else 'direkt'))
                                  if design else '–'),
            'Maschinen-ID: %s' % lic.get('machine_id'), 'Lizenz: %s' % lic.get('line'),
            lic.get('products_line', ''),
            'Auswahl: %d Einträge, Baugruppenmodus %s' % (len(_sel_order()), 'an' if _state.get('assembly') else 'aus')]
    try:
        with open(L.LOG_FILE, 'r', encoding='utf-8', errors='replace') as fh:
            tail = fh.readlines()[-40:]
        rows.append('--- letzte Protokollzeilen ---')
        rows.extend(t.rstrip('\n') for t in tail)
    except Exception:
        pass
    return '\n'.join(rows)


def _open_folder(path):
    ok = DIAG.open_folder(path)
    if not ok:
        L.warn('Ordner öffnen fehlgeschlagen: %s', path)
    return ok


class PaletteHTML(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            ha = adsk.core.HTMLEventArgs.cast(args)
            act = ha.action
            L.info('Palette -> Python: action=%s data=%d Zeichen (Handler %x)', act, len(ha.data or ''), id(self))
            data = {}
            try:
                data = json.loads(ha.data or '{}')
            except Exception:
                data = {}
            if act == 'ready':
                _state['pal_ready'] = True
                ha.returnData = json.dumps(_session(), default=str)
            elif act == 'apply':
                aid = data.get('applyId')
                ha.returnData = 'OK'
                if aid and aid in _state.setdefault('apply_ids', set()):
                    L.warn('apply %s doppelt empfangen – ignoriert', aid)
                    return
                if aid:
                    _state['apply_ids'].add(aid)
                _state['result'] = data
                ao = data.get('applyOpts') or {}
                if isinstance(_state.get('opts'), dict):     # „ersetzen“ wirkt schon in der CAM-Vorbereitung
                    for k in ('stock_body', 'replace', 'cam'):
                        if k in ao:
                            _state['opts'][k] = bool(ao[k])
                _fire(EV_APPLY)             # Befehl erst NACH Rückkehr aus dem HTML-Ereignis starten
            elif act == 'setAssembly':
                # 2.0.7: Checkbox → State + Auswahlliste sync (teil↔kopie)
                rep = _set_assembly(bool(data.get('assembly')))
                ha.returnData = json.dumps(rep, default=str)
            elif act == 'pick':
                _state['assembly'] = bool(data.get('assembly', _state.get('assembly')))
                ha.returnData = 'OK'
                _fire(EV_PICK)
            elif act == 'nest':
                if _debounce('nest', 1.0):
                    ha.returnData = 'OK'
                    return
                _state['nest_req'] = data
                _state['assembly'] = bool(data.get('assembly', _state.get('assembly')))
                ha.returnData = 'OK'
                _fire(EV_NEST)
            elif act == 'refreshCopies':
                ha.returnData = 'OK'
                if _debounce('refreshCopies', 2.0):
                    return
                _fire(EV_REFRESH)
            elif act == 'removePart':
                k = data.get('key')
                if k in _sel():
                    _sel().pop(k, None)
                    _sel_order().remove(k)
                ha.returnData = json.dumps({'rows': _rows(), 'assembly': bool(_state.get('assembly'))}, default=str)
            elif act == 'clearParts':
                # 2.0.5: Liste leeren = komplette Sitzung zurücksetzen (Auswahl, Teil-Einstellungen, Auftrag,
                # Anordnung, Fixierungen, Bearbeiten-Stand). Das übernommene Nesting bleibt in der Konstruktion
                # („Nesting löschen“ entfernt es). Mehrfachklick (bis 6×/s) wird zusammengefasst.
                if _debounce('clearParts', 0.8):
                    ha.returnData = json.dumps({'rows': _rows(), 'reset': False}, default=str)
                    return
                pid = (((_state.get('ctx') or {}).get('project')) or {}).get('id')
                if pid:
                    L.info('Liste geleert – Palette vom Projekt %s gelöst', pid)
                _reset_session('Liste geleert', notify=False)
                ha.returnData = json.dumps({'rows': [], 'reset': True, 'session': _session()}, default=str)
            elif act == 'deleteNesting':
                ha.returnData = 'OK'
                if _debounce('deleteNesting', 2.0):
                    return
                _state['del_req'] = {'project': data.get('project'), 'source': 'palette'}
                _fire(EV_DELETE)
            elif act == 'rows':
                # Einstellungen geändert -> Hinweise neu berechnen (Dicke, Passt-nicht, Material …)
                for key, d in (data.get('parts') or {}).items():
                    ps = _state['part_settings'].get(key)
                    if ps is not None:
                        if 'qty' in d and int(d['qty']) != int(ps.get('qty', 0)):
                            ps['qty'], ps['manual_qty'] = max(0, int(d['qty'])), True
                        if d.get('mode') in ('laengs', 'quer', 'frei'):
                            ps['mode'] = d['mode']
                        if 'enabled' in d:
                            ps['enabled'] = bool(d['enabled'])
                design = _design()
                s = _coerce_settings(design, data.get('settings') or {}) if design else None
                ha.returnData = json.dumps({'rows': _rows(s) if design else [], 'assembly': bool(_state.get('assembly'))}, default=str)
            elif act == 'pref':
                if 'featView' in data:
                    v = _set_feat_view(_design(), data.get('featView'))
                    L.info('Ansicht Bearbeitungen: %s (gespeichert)', v)
                if data.get('lastTab'):
                    _write_prefs(last_tab=str(data['lastTab'])[:20])
                if data.get('size'):
                    _remember_size()
                ha.returnData = 'OK'
            elif act == 'saveStock':
                d = data
                path = STK.save_user(HERE, d.get('group') or STK.CUSTOM, d.get('material') or '',
                                     float(d['length']), float(d['width']), float(d['thickness']),
                                     d.get('grain') or 'X')
                _state['stock_lib'] = None
                L.info('Rohteil-Vorlage gespeichert: %s', path)
                ha.returnData = json.dumps(STK.for_palette(_stock_lib()))
            elif act == 'language':
                _write_prefs(language=ca_i18n.normalize_lang((data or {}).get('language') or 'de'))
                ha.returnData = json.dumps({'ok': True, 'i18n': _i18n_payload()}, default=str)
            elif act == 'license':
                ha.returnData = json.dumps(_license_status())  # OSS: ignoriert Schlüssel
            elif act == 'openLogs':
                ha.returnData = json.dumps({'ok': _open_folder(L.LOG_DIR), 'path': L.LOG_DIR})
            elif act == 'diag':
                txt = _diag_text()
                p = L.dump_json('diagtext', {'text': txt})
                ha.returnData = json.dumps({'text': txt, 'file': p})
            elif act == 'cancel':
                ha.returnData = 'OK'
                _remember_size()
                pal = _ui.palettes.itemById(PALETTE_ID)
                if pal:
                    pal.isVisible = False
            else:
                ha.returnData = 'Unbekannte Aktion'
        except Exception:
            _err('Palette')


def _fire(ev_id):
    """Befehl verzögert starten: aus einem Palette-Ereignis heraus ignoriert Fusion execute() oft.

    Fusion 2705 liefert bei fireCustomEvent IMMER False, stellt das Ereignis aber trotzdem zu (Log 10:10:13:
    „-> False“, 15 ms später „CustomEvent … empfangen“). Rückfall (direkter Aufruf) daher nur, wenn das
    Ereignis gar nicht registriert ist oder der Aufruf eine Ausnahme wirft."""
    if ev_id not in _custom_events:
        L.warn('CustomEvent %s nicht registriert – direkter Aufruf', ev_id)
        _run_deferred(ev_id)
        return
    try:
        ok = _app.fireCustomEvent(ev_id, '')
        L.info('fireCustomEvent(%s) -> %s (Rückgabewert wird ignoriert)', ev_id, ok)
    except Exception:
        L.exc('fireCustomEvent(%s) – direkter Aufruf als Rückfall' % ev_id)
        _run_deferred(ev_id)


def _run_deferred(ev_id):
    if ev_id == EV_SWITCH:
        cid = _state.pop('switch_cmd', None)
        ws = _ui.workspaces.itemById(WS)
        if ws and cid:
            ws.activate()
            _execute_cmd(cid)
        return
    if ev_id == EV_PICK:
        _execute_cmd(CMD_PICK)
    elif ev_id == EV_NEST:
        _execute_cmd(CMD_PREP)
    elif ev_id == EV_REFRESH:
        _execute_cmd(CMD_REFRESH)
    elif ev_id == EV_APPLY:
        # 1.8.1: CAM-Bezüge VOR dem Übernehmen-Befehl lösen (eigene Transaktion, nie zusammen mit Geometrie)
        TM.reset()                       # 2.0.2: Zeiten je Phase für dieses Übernehmen
        with TM.phase('CAM vorbereiten'):
            ok_prep = _prepare_apply()
        if ok_prep:
            with TM.phase('Übernehmen-Befehl'):
                _execute_cmd(CMD_APPLY)
    elif ev_id == EV_DELETE:
        _delete_flow(_state.pop('del_req', None) or {})
    elif ev_id == EV_CLEANUP:
        _cleanup_flow()
    elif ev_id == EV_VERIFY:
        extra = []
        try:
            with TM.phase('Nachprüfung'):
                extra.append(_post_apply_verify(report=True))
        except Exception:
            L.exc('Nachprüfung')
        pend = _state.pop('pending_summary', None)
        if pend:
            # 2.0.1: CAM in einem eigenen, späteren Schritt (eigenes Ereignis) – nie im Nachprüfungs-Handler
            _state['cam_pending'] = {'pend': pend, 'extra': extra}
            _fire(EV_CAM)
        elif extra and extra[0]:
            _msg(extra[0])
    elif ev_id == EV_CAM:
        cp = _state.pop('cam_pending', None)
        if cp:
            pend, extra = cp['pend'], list(cp['extra'])
            try:
                extra.append(_cam_after_apply(pend))
            except Exception:
                L.exc('CAM-Setups')
                extra.append('CAM-Setup: fehlgeschlagen (siehe Protokoll) – Nesting ist trotzdem übernommen.')
            L.info('Zeiten (ms): %s', TM.report())
            t = pend['text']
            add = '\n'.join(e for e in extra if e)
            if add:
                t = t.replace('\n\nDiagnose-Datei:', '\n' + add + '\n\nDiagnose-Datei:') if '\n\nDiagnose-Datei:' in t \
                    else t + '\n' + add
            info = dict(_state.get('applied_info') or {})
            info['text'] = t
            _state['applied_info'] = info
            _to_palette('applied', info)
            _msg(t)


# ---------------------------------------------------------------- Befehl „Teile wählen“ (schlank)
class PickCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            if not _design_or_switch(CMD_PICK):
                return
            cmd = args.command
            cmd.okButtonText = 'Zur Liste hinzufügen'
            inputs = cmd.commandInputs
            sel = inputs.addSelectionInput('sel', 'Teile', 'Körper oder Komponenten der Teile wählen')
            sel.addSelectionFilter('SolidBodies')
            sel.addSelectionFilter('Occurrences')
            sel.setSelectionLimits(1, 0)
            mode = 'Baugruppenmodus: Unterbaugruppen werden rekursiv als Kopien übernommen.' \
                if _state.get('assembly') else 'Einzelteile (je Komponente genau 1 Volumenkörper).'
            _formatted_box(inputs, 'pinfo', '', mode + ' OK kehrt zur LazyNest-Palette zurück.', 2)
            for ev, H in ((cmd.execute, PickExecute), (cmd.inputChanged, PickInputChanged)):
                h = H()
                ev.add(h)
                handlers.append(h)
            L.info('Teile wählen gestartet (Baugruppenmodus %s)', _state.get('assembly'))
        except Exception:
            _err('Teile wählen')


class PickInputChanged(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            if args.input.id == 'sel':
                sel = adsk.core.SelectionCommandInput.cast(args.input)
                _state['pick_live'] = [sel.selection(i).entity for i in range(sel.selectionCount)]
        except Exception:
            pass


class PickExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            sel = adsk.core.SelectionCommandInput.cast(args.command.commandInputs.itemById('sel'))
            n = L.safe(lambda: sel.selectionCount, default=0)
            ents = [sel.selection(i).entity for i in range(n if isinstance(n, int) else 0)]
            if not ents:                    # Log 10:43 (1.x): im execute meldete das SelectionInput 0 Einträge
                ents = list(_state.get('pick_live') or [])
            _state['pick_live'] = []
            L.info('Teile wählen: %d Entität(en)', len(ents))
            _add_entities(ents, bool(_state.get('assembly')))
            _show_palette()
            _to_palette('rows', {'rows': _rows(), 'assembly': bool(_state.get('assembly')), 'tab': 'Auswahl'})
        except Exception:
            _err('Teile übernehmen')


# ---------------------------------------------------------------- interne Befehle (ändern die Konstruktion)
class _SimpleCreated(adsk.core.CommandCreatedEventHandler):
    EXEC = None

    def notify(self, args):
        h = self.EXEC()
        args.command.execute.add(h)
        handlers.append(h)


class PrepExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            req = _state.pop('nest_req', None) or {}
            _prepare_nest(req)
        except Exception:
            tb = L.exc('Nesting vorbereiten')
            _to_palette('notice', {'level': 'error', 'text': 'Nesting konnte nicht gestartet werden – siehe Protokoll.'})
            _msg('Nesting konnte nicht gestartet werden:\n%s\n\nProtokoll: %s' % (tb, L.LOG_FILE))


class PrepCreated(_SimpleCreated):
    EXEC = PrepExecute


class RefreshExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            design = _design()
            if not design:
                return
            log = []
            rep = F.refresh_copies(design, log)
            L.info('Kopien aktualisieren: %s', json.dumps(rep, ensure_ascii=False))
            t = 'Kopien aktualisiert: %d.' % rep['updated']
            if rep['outdated']:
                t += ('\nACHTUNG: verknüpfte Quelle(n) mit neuerer Version: %s – zuerst in Fusion „Verknüpfungen '
                      'aktualisieren“, dann erneut „Kopien aktualisieren“.' % ', '.join(rep['outdated']))
            if rep['missing']:
                t += '\nQuelle nicht gefunden: %s' % ', '.join(rep['missing'])
            if rep['failed']:
                t += '\nFehlgeschlagen: %s' % '; '.join(rep['failed'])
            if rep['updated']:
                t += '\nLagen in den Rohteilen bleiben erhalten. Hat sich die Kontur geändert: „Auswahl übernehmen ' \
                     '& nesten“ bzw. Vorschau prüfen.'
            _to_palette('notice', {'level': 'warn' if (rep['outdated'] or rep['failed'] or rep['missing']) else 'ok',
                                   'text': t})
            _msg(t)
        except Exception:
            _err('Kopien aktualisieren')


class RefreshCreated(_SimpleCreated):
    EXEC = RefreshExecute


def _cam_after_apply(pend):
    """CAM-Setups je Rohteil des eben übernommenen Projekts (nie fatal). Liefert die Ergebniszeile."""
    opts = pend.get('opts') or {}
    if not opts.get('cam', True):
        return 'CAM-Setup: nicht angefordert (Option „CAM-Setup erstellen“ aus).'
    design = _design()
    if not design:
        return 'CAM-Setup: keine Konstruktion aktiv.'
    log, diag = [], {}
    # 2.0.1: veraltete Verknüpfungen -> KEIN computeAll/CAM (Hänger KMD-PC-03 12:50)
    od, _chk = REFS.outdated(design)
    if od:
        L.warn('CAM-Nachlauf übersprungen – veraltete Verknüpfungen: %s', ', '.join(i['name'] for i in od))
        return ('CAM-Setup: übersprungen – %d verknüpfte Komponente(n) veraltet (%s). Verknüpfungen aktualisieren, '
                '„Kopien aktualisieren“ und dann „Nesting bearbeiten“ → Übernehmen.' % (
                    len(od), ', '.join(i['name'] for i in od[:4])))
    # 1.8.1: CAM erst, wenn die Konstruktion nach dem Übernehmen fehlerfrei neu berechnet ist.
    # 2.0.1: nicht doppelt – hat das Übernehmen schon neu berechnet und die Nachprüfung nichts verschoben, reicht das.
    lc = F.LAST_COMPUTE
    if lc.get('ok') and lc.get('apply_seq') == F.APPLY_SEQ and not _state.get('verify_fixed'):
        L.info('CAM-Nachlauf: computeAll übersprungen (bereits nach dem Übernehmen neu berechnet, Nachprüfung ohne '
               'Korrektur)')
    else:
        try:
            L.checkpoint('CAM-Nachlauf: design.computeAll()')
            with TM.phase('CAM-Nachlauf computeAll'):
                design.computeAll()
            L.checkpoint('CAM-Nachlauf: design.computeAll() fertig')
            F.write_compute_state(design, 'LazyNest')
        except Exception as e:
            L.exc('computeAll vor CAM')
            return ('CAM-Setup: übersprungen – Neuberechnung der Konstruktion fehlgeschlagen (%s). Nesting ist '
                    'übernommen; Setups bitte nach dem Speichern über „Nesting bearbeiten“ → Übernehmen '
                    'aktualisieren.' % e)
    L.checkpoint('CAM-Nachlauf: CAM.sync Projekt %s', pend.get('project_id'))
    try:
        with TM.phase('CAM.sync (Setups)'):
            res = CAM.sync(_app, _ui, design, pend.get('project_id'), opts, VERSION, log, diag)
    except Exception as e:                  # sync wirft eigentlich nie – doppelt abgesichert
        L.exc('CAM.sync')
        res = {'line': 'CAM-Setup: fehlgeschlagen (%s) – Nesting ist trotzdem übernommen.' % e}
    diag['log'] = log
    diag['line'] = res.get('line')
    p = L.dump_json('cam', diag)
    L.info('CAM: %s (Datei %s)', res.get('line'), p)
    for ln in log:
        L.info('  %s', ln)
    # 2.0.3: Stand nach dem eigenen Nachlauf (Projekt-Attribut, Nachprüfung ohne Korrektur, CAM-Setups – alles
    # ohne Geometrieänderung) erneut ablegen. Deckt den Fall ab, dass sich die Zeitachsen-Kennung zwischen dem
    # computeAll im Übernehmen-Befehl und dem Befehlsende geändert hat (LazySetup rechnete 2.0.2 sonst 120 s neu).
    if F.LAST_COMPUTE.get('ok') and F.LAST_COMPUTE.get('apply_seq') == F.APPLY_SEQ and not _state.get('verify_fixed'):
        cur = F.CC.fingerprint(design) if F.CC is not None else None
        if cur is not None and cur != F.LAST_COMPUTE.get('fp'):
            L.info('Neuberechnungs-Stand: Kennung nach dem Nachlauf %s (nach computeAll %s) – neu abgelegt',
                   cur, F.LAST_COMPUTE.get('fp'))
        F.write_compute_state(design, 'LazyNest')
    return res.get('line') or ''


def _post_apply_verify(report=False):
    """Läuft nach dem Übernehmen-Befehl: hat Fusion (Neuberechnung/Zeitachse) Teile zurückgesetzt?"""
    design = _design()
    if not design:
        return ''
    log = []
    rep = F.verify_and_repair(design, fix=True, log=log)
    rep['log'] = log
    rep['fusion_version'] = L.safe(lambda: _app.version)
    path = L.dump_json('verify', rep)
    L.info('Nachprüfung: %d geprüft, %d abweichend, %d korrigiert', rep['checked'], rep['deviating'], rep['fixed'])
    _state['verify_fixed'] = rep['fixed']
    line = ''
    if rep['deviating']:
        line = ('Nachprüfung nach dem Übernehmen: %d Teil(e) hatten sich verschoben, %d korrigiert. Datei: %s'
                % (rep['deviating'], rep['fixed'], path))
        if not report:
            _msg(line)
    return line


def _execute_cmd(cid):
    cd = _ui.commandDefinitions.itemById(cid)
    try:
        active = _ui.activeCommand
    except Exception:
        active = '?'
    res = cd.execute() if cd else None
    L.info('execute(%s) -> %s (aktiver Befehl vorher: %s)', cid, res, active)
    return res


class DeferredEvent(adsk.core.CustomEventHandler):
    def __init__(self, ev_id):
        super().__init__()
        self.ev_id = ev_id

    def notify(self, args):
        try:
            L.info('CustomEvent %s empfangen', self.ev_id)
            _run_deferred(self.ev_id)
        except Exception:
            _err('Befehl starten (%s)' % self.ev_id)


class PaletteClosed(adsk.core.UserInterfaceGeneralEventHandler):
    def notify(self, args):
        _remember_size()            # Palette bleibt (unsichtbar) bestehen; stop() löscht sie


def _persist_palette_settings(design, ps):
    """Schnelleinstellungen der Palette in Attribut (und – nur parametrisch – Parameter) übernehmen."""
    if not ps:
        return
    st = F.read_settings_attr(design)
    for src, dst in (('length', 'length'), ('width', 'width'), ('frame', 'frame'), ('spacing', 'spacing'),
                     ('grain', 'grain'), ('rotStep', 'rot_step'), ('vacuumWeight', 'vac_weight')):
        if ps.get(src) is not None:
            st[dst] = ps[src]
    F.write_attr(design, 'settings', st)
    vals = {}
    for k, name in (('length', 'nest_laenge'), ('width', 'nest_breite'), ('frame', 'nest_rand'),
                    ('spacing', 'nest_abstand'), ('rot_step', 'nest_drehschritt'), ('vac_weight', 'nest_vakuum_gewicht')):
        if k in st:
            vals[name] = float(st[k])
    F.write_params(design, vals)


# ====================================================================== Übernehmen
class ApplyCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        h = ApplyExecute()
        args.command.execute.add(h)
        handlers.append(h)
        # keine Eingaben -> Fusion führt den Befehl ohne Dialog sofort aus


def _sheet_settings(ctx, res):
    """Rohteil-Maße wie in F.apply_result (Job + Schnelleinstellungen der Palette), mm."""
    sh = dict((ctx.get('job') or {}).get('sheet') or {})
    for k in ('length', 'width', 'thickness', 'frame', 'spacing', 'grain'):
        if k in (res.get('sheet') or {}):
            sh[k] = res['sheet'][k]
    return sh


def _all_deletions(design):
    occs = F.sheet_occurrences(design.rootComponent)
    return {'paths': [L.safe(lambda: o.name, default='') for o in occs], 'stock_sheets': [], 'sheet_nos': set(),
            'count': len(occs), 'all': True}


def _pending_deletions(design):
    """(Projekt-ID, plan_deletions) für das anstehende Übernehmen – oder (None, None)."""
    ctx, res, opts = _state.get('ctx'), _state.get('result'), _state.get('opts', {}) or {}
    if not (design and ctx and res):
        return None, None
    proj = ctx.get('project') or {}
    if proj.get('mode') == 'bearbeiten':
        plan = F.plan_edit(design, proj['id'], res, _sheet_settings(ctx, res), ctx)
        return proj['id'], F.plan_deletions(plan)
    if opts.get('replace') and F.sheet_occurrences(design.rootComponent):
        return None, _all_deletions(design)
    return None, None


def _cam_dependents(project_id, dels):
    """Nur lesen: CAM-Setups, die auf zu löschende Geometrie verweisen (ohne Arbeitsbereichswechsel)."""
    log = []
    cam, _ws = CAM.get_cam(_app, _ui, log, create=False)
    if cam is None:
        return None, []
    return cam, CAM.dependents(cam, project_id, dels)


def _prepare_apply():
    """Vor dem Übernehmen-Befehl (eigener Schritt, eigene Transaktion): Muss beim Übernehmen Geometrie gelöscht
    werden, auf die CAM-Setups/Bearbeitungen verweisen, wird nachgefragt und der CAM-Bezug VOR dem Löschen gelöst.
    Rückgabe True = Übernehmen ausführen, False = abgebrochen (nichts geändert)."""
    _state['cam_prepared'] = None
    try:
        design = _design()
        od, _chk = REFS.outdated(design) if design else ([], False)
        if od:
            L.warn('Übernehmen abgebrochen – veraltete Verknüpfungen: %s', json.dumps(od, ensure_ascii=False, default=str))
            aid = (_state.get('result') or {}).get('applyId')
            if aid:
                _state.setdefault('apply_ids', set()).discard(aid)
            msg = REFS.message(od, 'Übernehmen (Neuberechnung)')
            _to_palette('notice', {'level': 'error', 'text': msg.split('\n\n')[0] + ' – Verknüpfungen aktualisieren, '
                                   'dann „Kopien aktualisieren“ und erneut übernehmen.'})
            _msg(msg)
            return False
        pid, dels = _pending_deletions(design)
        if not dels or not (dels.get('count') or dels.get('all')):
            return True
        cam, deps = _cam_dependents(pid, dels)
        if not deps:
            return True
        res = _state.get('result') or {}
        n_ops = sum(d['ops'] for d in deps)
        names = ', '.join(d['name'] for d in deps[:6]) + (' …' if len(deps) > 6 else '')
        L.info('CAM-Abhängigkeiten vor dem Übernehmen: %d Setup(s), %d Operation(en): %s; zu löschen: %s',
               len(deps), n_ops, names, dels.get('paths'))
        choice = 'reassign'
        if n_ops:
            what = []
            if dels.get('all'):
                what.append('alle Rohteile werden ersetzt')
            else:
                n_parts = sum(1 for p_ in dels['paths'] if '+' in p_)
                n_sheets = sum(1 for p_ in dels['paths'] if '+' not in p_)
                if n_parts:
                    what.append('%d Teil(e) entfallen' % n_parts)
                if n_sheets:
                    what.append('%d Rohteil(e) entfallen' % n_sheets)
                if dels.get('stock_sheets'):
                    what.append('%d Plattenkörper wird neu aufgebaut (Plattenmaß geändert)' % len(dels['stock_sheets']))
            r = _ask3('%d Operation(en) verweisen auf dieses Rohteil (Setup: %s).\n'
                      'Beim Übernehmen muss Geometrie gelöscht werden: %s.\n\n'
                      'Ja = Operationen löschen\n'
                      'Nein = Setup behalten und Geometrie neu zuweisen\n'
                      '      (Finalschnitt-Operationen „FS …“ werden gelöscht, andere verlieren ihre Auswahl)\n'
                      'Abbrechen = nichts ändern' % (n_ops, names, ', '.join(what) or 'Geometrie'))
            L.info('Rückfrage CAM-Abhängigkeiten -> %s', r)
            if r == 'cancel':
                aid = res.get('applyId')
                if aid:
                    _state.setdefault('apply_ids', set()).discard(aid)   # erneutes Übernehmen erlauben
                return False
            choice = 'delete_ops' if r == 'yes' else 'reassign'
        log = []
        L.checkpoint('CAM vor dem Löschen vorbereiten (%s): %d Setup(s)', choice, len(deps))
        rep = CAM.prepare_for_delete(cam, deps, choice, dels, log)
        rep['log'] = log
        L.dump_json('camprep', rep)
        L.info('CAM vorbereitet: %s', json.dumps(rep, ensure_ascii=False, default=str)[:800])
        _state['cam_prepared'] = {'apply_id': res.get('applyId'), 'choice': choice, 'setups': len(deps),
                                  'report': rep}
        return True
    except Exception:
        L.exc('CAM-Vorbereitung vor dem Übernehmen')
        _msg('CAM-Vorbereitung fehlgeschlagen – Übernehmen abgebrochen, NICHTS gelöscht.\n\nProtokoll: %s'
             % L.LOG_FILE)
        return False


def _cam_safe_to_delete(design, project_id, dels, res):
    """Letzte Sicherung im Übernehmen-Befehl: Geometrie mit CAM-Bezug wird nur gelöscht, wenn _prepare_apply
    die Bezüge für GENAU dieses Übernehmen gelöst hat. Sonst Abbruch statt Absturz."""
    prep = _state.get('cam_prepared') or {}
    if prep and prep.get('apply_id') == res.get('applyId'):
        return True
    try:
        _cam, deps = _cam_dependents(project_id, dels)
    except Exception:
        L.exc('CAM-Abhängigkeiten prüfen')
        deps = [{'name': '?', 'ops': 0, 'reason': 'unbekannt'}]
    left = list(deps)
    if not left:
        return True
    L.warn('Übernehmen abgebrochen: %d Setup(s) verweisen noch auf zu löschende Geometrie', len(left))
    _msg('Übernehmen abgebrochen – NICHTS gelöscht:\n%d CAM-Setup(s) verweisen noch auf Geometrie, die gelöscht '
         'werden müsste (%s).\nBitte erneut „Übernehmen“ wählen und die Rückfrage beantworten.'
         % (len(left), ', '.join(d['name'] for d in left[:6])))
    aid = res.get('applyId')
    if aid:
        _state.setdefault('apply_ids', set()).discard(aid)
    return False


def _overlap_guard(ctx, res):
    """2.0.4: unabhängige Prüfung der finalen Platzierungen (Python, echte Konturen) vor dem Übernehmen.
    Die Palette prüft auch – aber behaltene Lagen/Fixierungen wurden dort bis 2.0.3 nicht geprüft (Log 17:51).
    Meldet die Palette 0 Konflikte und die Prüfung findet doch welche, wird nachgefragt."""
    try:
        t0 = time.time()
        bad = G.placement_conflicts(res, ctx.get('job') or {})
        L.info('Kollisionsprüfung der finalen Anordnung: %d Befund(e) in %.2f s%s', len(bad), time.time() - t0,
               (' – ' + '; '.join('Blatt %d %s: %s' % (b['sheet'] + 1, b['kind'], b['name']) for b in bad[:8]))
               if bad else '')
    except Exception:
        L.exc('Kollisionsprüfung der finalen Anordnung')
        return True
    ov = [b for b in bad if b['kind'] == 'Überlappung']
    if not bad or (res.get('conflicts') and not ov):
        return True          # Abstands-/Randkonflikte hat der Anwender eben schon bestätigt
    # Review P3.10: echte Überlappung (auch fixierte Teile untereinander) IMMER ausdrücklich bestätigen lassen
    txt = '\n'.join('Rohteil-Blatt %d: %s (%s)' % (b['sheet'] + 1, b['name'], b['kind']) for b in bad[:10])
    if _ask('Die Prüfung der finalen Anordnung meldet %d Problem(e)%s:\n\n%s\n\nTrotzdem übernehmen?'
            % (len(bad), (', davon %d Überlappung(en)' % len(ov)) if ov else '', txt)):
        L.info('Übernehmen trotz %d Befund(en) der Kollisionsprüfung', len(bad))
        return True
    L.info('Übernehmen wegen Kollisionsprüfung abgebrochen')
    aid = res.get('applyId')
    if aid:
        _state.setdefault('apply_ids', set()).discard(aid)
    return False


class ApplyExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            design = _design()
            ctx, res, opts = _state.get('ctx'), _state.get('result'), _state.get('opts', {})
            _state['result'] = None         # jedes Ergebnis nur EINMAL verarbeiten
            if not (design and ctx and res):
                L.warn('Übernehmen ohne (neues) Ergebnis – doppelter Aufruf? ignoriert')
                return
            if _state.get('ctx_doc') and _doc_key() and _doc_key() != _state.get('ctx_doc'):
                _msg('Der Auftrag gehört zu einem anderen Dokument – nichts übernommen. Bitte LazyNest dort öffnen.')
                return
            key = (res.get('jobId'), res.get('applyId'))
            if res.get('jobId') and res.get('jobId') in _state.setdefault('applied_jobs', set()) and \
                    (ctx.get('project') or {}).get('mode') != 'bearbeiten':
                if not _ask('Dieses Nesting wurde bereits übernommen.\n\nNoch ein weiteres Mal übernehmen '
                            '(zusätzliches Rohteil)?'):
                    L.info('Erneutes Übernehmen von %s abgelehnt', key)
                    return
            if res.get('jobId') and res['jobId'] != ctx['job'].get('jobId'):
                _msg('Das Ergebnis gehört nicht zum aktuellen Nesting-Auftrag.')
                return
            if res.get('unplaced'):
                txt = ', '.join('%d× %s' % (u['count'], u['name']) for u in res['unplaced'])
                if not _ask('Nicht platziert: %s\n\nTrotzdem übernehmen?' % txt):
                    return
            if res.get('conflicts'):
                if not _ask(conflict_question(res)):
                    L.info('Übernehmen mit %s Konflikt(en) abgelehnt (%s mit Strg übersteuert)',
                           res['conflicts'], res.get('forced') or 0)
                    return
                L.info('Übernehmen trotz %s Konflikt(en), mit Strg übersteuert: %s', res['conflicts'],
                       ', '.join(res.get('forcedNames') or []) or '-')
            if not _overlap_guard(ctx, res):
                return
            _state['overrides'] = res.get('overrides') or {}
            _persist_palette_settings(design, res.get('settings') or {})
            opts = dict(opts)
            ao = res.get('applyOpts') or {}           # 2.0.0: Übernahme-Optionen aus dem Register „Ergebnis“
            for k in ('stock_body', 'replace', 'cam'):
                if k in ao:
                    opts[k] = bool(ao[k])
            if ao.get('fix') in [k for k, _l in F.FIX_MODES]:
                opts['fix'] = ao['fix']
            for k in ('cam_origin', 'cam_stock'):
                if ao.get(k):
                    opts[k] = str(ao[k])
            if 'cam_prog' in ao:
                try:
                    opts['cam_prog'] = max(0, int(ao['cam_prog']))
                except (TypeError, ValueError):
                    pass
            if ao:
                st_ = F.read_settings_attr(design)
                st_.update({k: opts[k] for k in ('stock_body', 'replace', 'fix', 'cam', 'cam_origin', 'cam_stock',
                                                 'cam_prog') if k in opts})
                F.write_attr(design, 'settings', st_)
            proj = ctx.get('project') or {}
            prev = None
            deleted = 0
            plan = None
            if proj.get('mode') == 'bearbeiten':
                prev = L.safe(lambda: P.load(design, proj['id']), default=None)
                # 2.0.4: KEIN x_off mehr auf das erste alte Rohteil (neue Rohteile lagen darauf, Log 17:51) –
                # apply_result legt neue Rohteile hinter alle vorhandenen (ln_geom.next_sheet_x)
                # 1.8.1: an Ort und Stelle aktualisieren – Rohteile + Teile-Occurrences bleiben erhalten
                plan = F.plan_edit(design, proj['id'], res, _sheet_settings(ctx, res), ctx)
                dels = F.plan_deletions(plan)
                if dels['count'] and not _cam_safe_to_delete(design, proj['id'], dels, res):
                    return
                opts['plan'] = plan
                deleted = len(plan['remove_sheets'])
                L.info('Bearbeiten an Ort und Stelle: Projekt %s – %d Rohteil(e) behalten, %d Teil(e) behalten '
                       '(%d unverändert, %d verschoben), %d ersetzt (mit Verlauf: Lage geändert), %d neu, '
                       '%d entfernt, %d Rohteil(e) entfernt, %d Plattenkörper neu',
                       proj['id'], sum(1 for e in plan['sheets'] if e.get('occ') is not None), plan['kept'],
                       plan['unchanged'], plan['moved'], plan['replaced'], plan['added'] - plan['replaced'],
                       plan['removed'] - plan['replaced'], deleted, plan['resized'])
            elif opts.get('replace') and F.sheet_occurrences(design.rootComponent):
                dels = _all_deletions(design)
                if not _cam_safe_to_delete(design, None, dels, res):
                    return
                F.delete_nesting(design)
            pid = proj['id'] if (proj.get('mode') == 'bearbeiten') else P.new_id()
            opts['project_id'] = pid
            opts['project_version'] = (prev.get('version', 0) + 1) if prev else 1
            log = []
            diag = {'fusion_version': L.safe(lambda: _app.version), 'designType':
                    'parametrisch' if F.is_parametric(design) else 'direkt',
                    'opts': {k: v for k, v in opts.items() if k != 'plan'}, 'job_id': ctx['job'].get('jobId'),
                    'palette_result': {'sheet': res.get('sheet'), 'settings': res.get('settings'),
                                       'conflicts': res.get('conflicts'), 'overrides': res.get('overrides'),
                                       'sheets': [[{k: p.get(k) for k in ('key', 'id', 'x', 'y', 'rot', 'flip', 'pinned')}
                                                   for p in S2['parts']] for S2 in res.get('sheets', [])]},
                    'job_parts': [{k: jp.get(k) for k in ('id', 'name', 'qty', 'grainMode', 'grainAngle', 'thickness',
                                                         'outer')} for jp in ctx['job'].get('parts', [])]}
            path = ''
            diag['apply_id'] = res.get('applyId')
            diag['job_origin'] = ctx.get('origin')
            diag['project'] = {'id': pid, 'version': opts['project_version'], 'mode': proj.get('mode', 'neu'),
                               'deleted_sheets': deleted}
            if plan:
                diag['edit_plan'] = {'kept': plan['kept'], 'added': plan['added'], 'removed': plan['removed'],
                                     'unchanged': plan['unchanged'], 'moved': plan['moved'],
                                     'replaced': plan['replaced'], 'parametric': plan['parametric'],
                                     'removed_sheets': deleted, 'resized': plan['resized'],
                                     'cam_prepared': (_state.get('cam_prepared') or {}).get('report')}
            ppath = ''
            try:
                with TM.phase('Übernehmen (Geometrie inkl. Prüfung)'):
                    stats = F.apply_result(design, ctx, res, opts, log, diag)
                _state['applied_jobs'].add(res.get('jobId'))
                try:
                    project = P.build_project(design, ctx, res, opts, project_id=pid, prev=prev, app=_app)
                    project['version'] = opts['project_version']
                    if proj.get('mode') == 'wiederholen':
                        project['derived_from'] = {'id': proj.get('id'), 'version': proj.get('version')}
                    ppath = P.save(design, project)
                    diag['project']['file'] = ppath
                    L.info('Projekt %s v%d gespeichert: Attribut + %s', pid, project['version'],
                           ppath or '(Datei fehlgeschlagen)')
                    # 2.0.4: Palette bleibt mit DIESEM Projekt verbunden – erneutes Nesten/Übernehmen ändert das
                    # Nesting an Ort und Stelle statt ein zweites (doppelte Teile) anzulegen.
                    ctx['project'] = {'id': pid, 'version': opts['project_version'], 'mode': 'bearbeiten'}
                    ctx['job']['project'] = {'id': pid, 'version': opts['project_version']}
                    _state['ctx_doc'] = _doc_key()
                except Exception as e:
                    L.exc('Projekt speichern')
                    log.append('Projekt konnte nicht gespeichert werden: %s' % e)
            finally:
                diag['log'] = log
                path = L.dump_json('apply', diag)
            opts.pop('plan', None)       # 2.0.0: Palette bleibt offen und zeigt das Register „Ergebnis“
            if plan:
                t = ('%d Rohteil(e) mit %d Teilen aktualisiert, Ausnutzung %s %%.' %
                     (stats['sheets'], stats['parts'], G.de(res.get('utilisation', 0) * 100, 1)))
                t += ('\nBearbeiten an Ort und Stelle: %d Teil(e) behalten (%d verschoben), %d neu eingefügt '
                      '(davon %d ersetzt, da Lage geändert – Konstruktion mit Verlauf), %d entfernt%s%s.'
                      % (stats.get('kept', 0), stats.get('kept_moved', 0), stats.get('added', 0), plan['replaced'],
                         stats.get('removed_parts', 0),
                                            ', %d Rohteil(e) entfernt' % stats['removed_sheets']
                                            if stats.get('removed_sheets') else '',
                                            ', %d Plattenkörper neu (Maß geändert)' % stats['stock_rebuilt']
                                            if stats.get('stock_rebuilt') else ''))
            else:
                t = ('%d Rohteil(e) mit %d Teilen erstellt, Ausnutzung %s %%.' %
                     (stats['sheets'], stats['parts'], G.de(res.get('utilisation', 0) * 100, 1)))
            if stats.get('turned'):
                t += '\n%d Rohteil(e) längs/quer gedreht (Teile mitgedreht, nichts gelöscht).' % stats['turned']
            if stats.get('abschnitte'):
                t += '\n%d Abschnitt(e) mit eigenem Format.' % stats['abschnitte']
            skipped = list(stats.get('skipped') or [])
            if stats['missing']:
                t += '\n%d Teil(e) übersprungen:' % stats['missing']
                for sk in skipped[:8]:
                    t += '\n  • %s – %s' % (sk.get('name'), sk.get('reason'))
                if len(skipped) > 8:
                    t += '\n  … (+%d, siehe Register „Ergebnis“)' % (len(skipped) - 8)
            if stats['moved']:
                t += '\nLageprüfung: %d Abweichung(en) erkannt, %d korrigiert.' % (stats['moved'], stats['corrected'])
            else:
                t += '\nLageprüfung (Matrix): alle Teile exakt wie in der Vorschau.'
            if stats.get('captured'):
                t += '\nPositionen in der Zeitachse erfasst (Snapshot): %d Teil(e).' % stats['captured']
            if stats.get('fixed_mode', 'none') != 'none':
                t += '\nFixiert (%s): %d von %d Teil(en)%s.' % (
                    dict(F.FIX_MODES).get(stats['fixed_mode'], stats['fixed_mode']), stats.get('fixed_parts', 0),
                    stats['parts'], '' if not stats.get('moved_by_fixing') else
                    ', %d dabei verschoben und korrigiert' % stats['moved_by_fixing'])
            if stats.get('native_bad'):
                t += ('\nACHTUNG: %d Teil(e) ließen sich nicht mit korrekter nativer Lage einfügen – '
                      'nur überschrieben, nicht festgesetzt.' % stats['native_bad'])
            t += '\nNeuberechnung (computeAll): %s.' % (
                'alle Teile unverändert' if not stats.get('moved_by_compute') else
                '%d Teil(e) verschoben – bitte Protokoll senden' % stats['moved_by_compute'])
            t += '\nProjekt %s v%d gespeichert%s.' % (pid, opts['project_version'],
                                                      ' (alte Version ersetzt)' if proj.get('mode') == 'bearbeiten'
                                                      else '')
            if stats['bbox_mismatch']:
                t += '\nHüllquader-Prüfung: %d Teil(e) weichen ab – bitte Diagnose-Datei senden.' % stats['bbox_mismatch']
            if log:
                t += '\n\n' + '\n'.join(log)
            if path:
                t += '\n\nDiagnose-Datei: %s' % path
            L.info('Übernehmen fertig: %s', json.dumps(stats))
            # Zusammenfassung erst NACH Abschluss des Befehls: Nachprüfung + CAM-Setups (Arbeitsbereichswechsel
            # ist innerhalb eines laufenden Befehls nicht ratsam) -> eine gemeinsame Meldung
            _state['applied_info'] = {'jobId': res.get('jobId'), 'project': pid, 'version': opts['project_version'],
                                      'sheets': stats['sheets'], 'parts': stats['parts'],
                                      'utilisation': res.get('utilisation', 0), 'skipped': skipped,
                                      'nestSkipped': _state.get('nest_skipped') or [], 'diagFile': path,
                                      'logFile': L.LOG_FILE, 'text': t}
            _to_palette('applied', _state['applied_info'])
            _state['pending_summary'] = {'text': t, 'project_id': pid, 'opts': opts, 'diag_path': path}
            _fire(EV_VERIFY)
        except Exception:
            _err('Übernehmen fehlgeschlagen')


# ====================================================================== Diagnose
class DiagCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        h = DiagExecute()
        args.command.execute.add(h)
        handlers.append(h)


class DiagExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            design = _design()
            if not design:
                _msg('Keine Konstruktion aktiv.')
                return
            d = F.diagnose(design)
            d['fusion_version'] = L.safe(lambda: _app.version)
            d['document'] = L.safe(lambda: _app.activeDocument.name)
            a = L.safe(lambda: design.attributes.itemByName(G.GROUP, 'lastJob'), default=False)
            if a and not isinstance(a, str):
                d['lastJob'] = L.safe(lambda: json.loads(a.value))
            path = L.dump_json('diagnose', d)
            sm = d['summary']
            _msg('Diagnose (nichts geändert):\n%d Rohteil(e), %d Teile, davon %d mit Lageabweichung.\n\n'
                 'Datei: %s\nProtokoll: %s' % (sm['sheets'], sm['parts'], sm['parts_deviating'], path, L.LOG_FILE))
        except Exception:
            _err('Diagnose fehlgeschlagen')


# ====================================================================== Wiederholen / Löschen
class RepeatCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        h = RepeatExecute()
        args.command.execute.add(h)
        handlers.append(h)


def _selected_project(design):
    """Projekt-ID des in Fusion gewählten Rohteils (oder eines Teils darin), sonst ''."""
    try:
        sels = _ui.activeSelections
        for i in range(sels.count):
            e = sels.item(i).entity
            occ = adsk.fusion.Occurrence.cast(e)
            for o in (occ, L.safe(lambda: occ.assemblyContext, default=None) if occ else None):
                if o is not None:
                    pid = F.sheet_project(o)
                    if pid:
                        return pid
    except Exception:
        pass
    return ''


def _original_occurrence(design, comp):
    """Eine Instanz der Komponente außerhalb der Rohteile (und nicht in „LazyNest Kopien“ bevorzugt)."""
    try:
        occs = design.rootComponent.allOccurrencesByComponent(comp)
        for i in range(occs.count):
            o = occs.item(i)
            top = o
            while L.safe(lambda: top.assemblyContext, default=None) is not None:
                top = top.assemblyContext
            if not L.safe(lambda: top.attributes.itemByName(G.GROUP, 'sheet'), default=None):
                return o
    except Exception:
        pass
    return None


def _open_project(design, project, mode, keep_layout):
    """Projekt in die Palette laden. mode: 'bearbeiten' | 'wiederholen'."""
    ctx_parts, rep = P.resolve(design, project)
    L.info('Projekt %s v%s laden (%s): gefunden %s, fehlt %s', project['project_id'], project.get('version'), mode,
           json.dumps(rep['found']), rep['missing'])
    txt = P.report_text(rep)
    if not ctx_parts:
        _msg('Keine Teile des Projekts in dieser Konstruktion gefunden.\n\n' + txt)
        return False
    if rep['missing'] and not _ask(txt + '\n\nOhne diese Teile fortfahren?'):
        return False
    job = P.job_from_project(project, ctx_parts, uuid.uuid4().hex[:12], keep_layout=keep_layout)
    job['seed'] = int(time.time()) % 100000
    _state['overrides'] = job.get('overrides') or {}
    # für „Zurück zu den Einstellungen“: je Teil eine Original-Instanz (nicht im Rohteil) + Stückzahl/Faser
    ents = []
    for jp in job['parts']:
        o = _original_occurrence(design, ctx_parts[jp['id']]['comp'])
        if o is not None:
            ents.append(o)
        _state['part_settings'][jp['id']] = {'mode': jp.get('grainMode', 'laengs'), 'enabled': True,
                                             'qty': int(jp.get('qty', 1)), 'manual_qty': False,
                                             'project_qty': int(jp.get('qty', 1))}
    _state['orig_occs'] = ents
    _sel().clear()
    del _sel_order()[:]
    _project_entries(project, ctx_parts, job)
    opts = dict(project.get('opts') or {})
    opts.setdefault('fix', 'ground')
    for k, v in _cam_opts(design).items():
        opts.setdefault(k, v)
    _start_job(design, job, ctx_parts, opts, mode=mode,
               project={'id': project['project_id'], 'version': project.get('version', 1), 'mode': mode})
    if txt and not rep['missing']:
        _msg(txt)
    return True


class RepeatExecute(adsk.core.CommandEventHandler):
    """Nesting wiederholen: gespeichertes Projekt (gewähltes Rohteil, sonst das neueste) neu rechnen.
    Fixierte Stücke bleiben, das Ergebnis wird als NEUES Projekt übernommen."""
    def notify(self, args):
        try:
            design = _design()
            if not design:
                return
            pid = _selected_project(design)
            projs = P.all_projects(design)
            project = (P.load(design, pid) if pid else None) or (projs[0] if projs else None)
            if not project:
                _msg('In dieser Konstruktion ist kein Nesting-Projekt gespeichert.\n'
                     'Tipp: „Nesting-Projekt importieren“ (Zusatzmodule) lädt eine gesicherte Projektdatei.')
                return
            _open_project(design, project, 'wiederholen', keep_layout=False)
        except Exception:
            _err('Wiederholen fehlgeschlagen')


# ====================================================================== Bearbeiten / Importieren
class EditCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            design = _design_or_switch(CMD_EDIT)
            if not design and _state.get('switch_cmd') == CMD_EDIT:
                return
            projs = P.all_projects(design) if design else []
            cmd = args.command
            if not projs:
                _msg('In dieser Konstruktion ist kein Nesting-Projekt gespeichert.\n'
                     'Tipp: „Nesting-Projekt importieren“ (Zusatzmodule) lädt eine gesicherte Projektdatei.')
                return
            cmd.okButtonText = 'Bearbeiten'
            want = _selected_project(design) or projs[0]['project_id']
            _state['edit_list'] = [(P.label(p), p['project_id']) for p in projs]
            dd = cmd.commandInputs.addDropDownCommandInput('proj', 'Nesting-Projekt',
                                                           adsk.core.DropDownStyles.TextListDropDownStyle)
            for lbl, pid in _state['edit_list']:
                dd.listItems.add(lbl, pid == want)
            cmd.commandInputs.addBoolValueInput('keep', 'Vorhandene Anordnung laden (sonst neu rechnen)', True, '', True)
            _formatted_box(cmd.commandInputs, 'einfo', '', 'Die Anordnung wird in der Palette geöffnet. „Übernehmen“ '
                           'ersetzt die Rohteile dieses Projekts (neue Version).', 2)
            h = EditExecute()
            cmd.execute.add(h)
            handlers.append(h)
        except Exception:
            _err('Bearbeiten')


class EditExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            design = _design()
            inputs = args.command.commandInputs
            it = adsk.core.DropDownCommandInput.cast(inputs.itemById('proj')).selectedItem
            pid = dict(_state.get('edit_list') or []).get(it.name if it else '', '')
            if not pid and _state.get('edit_list'):
                pid = _state['edit_list'][0][1]
            keep = adsk.core.BoolValueCommandInput.cast(inputs.itemById('keep')).value
            project = P.load(design, pid) if pid else None
            if not project:
                _msg('Projekt nicht gefunden.')
                return
            _open_project(design, project, 'bearbeiten', keep_layout=bool(keep))
        except Exception:
            _err('Bearbeiten fehlgeschlagen')


class ImportCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        h = ImportExecute()
        args.command.execute.add(h)
        handlers.append(h)


class ImportExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            design = _design()
            if not design:
                return
            fd = _ui.createFileDialog()
            fd.title = 'Nesting-Projekt importieren'
            fd.filter = 'LazyNest-/ContourNest-Projekt (*.json);;Alle Dateien (*.*)'
            fd.isMultiSelectEnabled = False
            if os.path.isdir(P.PROJECT_DIR):
                fd.initialDirectory = P.PROJECT_DIR
            if fd.showOpen() != adsk.core.DialogResults.DialogOK:
                return
            try:
                project = P.read_file(fd.filename)
            except Exception as e:
                _msg('Datei kann nicht gelesen werden:\n%s' % e)
                return
            P.save(design, project, write_file=False)
            L.info('Projekt %s v%s importiert aus %s', project['project_id'], project.get('version'), fd.filename)
            _open_project(design, project, 'bearbeiten', keep_layout=True)
        except Exception:
            _err('Import fehlgeschlagen')


# ====================================================================== 2.0.4: Nesting löschen / Waisen aufräumen
def _doc_key():
    """Kennung des aktiven Dokuments (Sitzung gehört zu GENAU einem Dokument)."""
    doc = L.safe(lambda: _app.activeDocument)
    if doc is None:
        return ''
    for f in (lambda: doc.creationId, lambda: doc.dataFile.id, lambda: doc.name):
        v = L.safe(f)
        if isinstance(v, str) and v:
            return v
    return ''


def _protect_idents():
    """Komponenten der laufenden Sitzung (Auftrag, Auswahl, frische Kopien) – Aufräumen fasst sie nie an."""
    out = set()
    for info in ((_state.get('ctx') or {}).get('parts') or {}).values():
        c = info.get('comp') if isinstance(info, dict) else None
        if c is not None:
            out.add(CL.ident(c))
    for key, e in _sel().items():
        out.add(key)
        for c in (e.get('copy_comp'), (e.get('res') or {}).get('comp')):
            if c is not None:
                out.add(CL.ident(c))
    return out


def _reset_session(reason, notify=True):
    """Palette/Sitzung leeren: danach öffnet LazyNest leer (kein alter Auftrag, keine alte Anordnung).
    2.0.5: einheitlich für LazyNest löschen, Dokument wechseln/schließen und „Liste leeren“. notify=False: nur
    den Zustand leeren (Aufrufer antwortet selbst, z. B. im HTML-Ereignis)."""
    for k in ('ctx', 'result', 'applied_info', 'back_from_job', 'back_layout', 'cam_prepared', 'pending_summary',
              'cam_pending', 'ctx_doc', 'nest_skipped', 'sess_doc', 'nest_req'):
        _state[k] = None
    _state['overrides'] = {}
    _state['part_settings'] = {}
    _sel().clear()
    del _sel_order()[:]
    _state['logged_inst'] = set()
    _state['applied_jobs'] = set()
    _state['apply_ids'] = set()
    L.info('Sitzung zurückgesetzt (%s)', reason)
    if notify and _state.get('pal_ready'):
        _to_palette('reset', {'reason': reason, 'message': _RESET_TXT.get(reason.split(':')[0])})
        _to_palette('session', _session())


_RESET_TXT = {'Nesting gelöscht': 'Nesting gelöscht – bereit für ein neues Nesting.',
              'anderes Dokument aktiviert': 'Anderes Dokument aktiv – LazyNest-Sitzung zurückgesetzt.',
              'Dokument geschlossen': 'Dokument geschlossen – LazyNest-Sitzung zurückgesetzt.',
              'Liste geleert': 'Liste geleert – LazyNest-Sitzung zurückgesetzt.'}


def _debounce(key, sec):
    """2.0.5: True = derselbe Palette-Befehl kam innerhalb von sec Sekunden schon einmal (ignorieren)."""
    now = time.time()
    last = _state.setdefault('debounce', {})
    if now - last.get(key, 0) < sec:
        L.info('%s doppelt (%.2f s) – ignoriert', key, now - last.get(key, 0))
        return True
    last[key] = now
    return False


def _drop_context(reason):
    """2.0.5: Auftrag/Anordnung/Fixierungen verwerfen, Auswahlliste behalten (Teile gibt es noch)."""
    for k in ('ctx', 'result', 'applied_info', 'back_from_job', 'back_layout', 'ctx_doc', 'nest_req'):
        _state[k] = None
    _state['overrides'] = {}
    _state['apply_ids'] = set()
    L.info('Auftrag verworfen (%s) – Auswahl bleibt', reason)
    if _state.get('pal_ready'):
        _to_palette('reset', {'reason': reason, 'message': 'Rohteile/Teile wurden in Fusion gelöscht – Auftrag '
                                                             'verworfen. Neu nesten.'})
        _to_palette('session', _session())


def _entry_valid(e):
    """Auswahl-Eintrag noch gültig? Nur Einzelteile ('teil'): Komponente gelöscht bzw. Körper gelöscht (ohne
    Umwandlung „Komponente aus Körper“). Baugruppen-Kopien ('kopie') baut das Nesten aus den Quellpfaden neu,
    Projekt-Einträge tragen keine Fusion-Objekte."""
    if e.get('kind', 'teil') != 'teil':
        return True
    res = e.get('res') or {}
    comp, body = res.get('comp'), res.get('body')
    if comp is not None and not CL.is_valid(comp):
        return False
    if body is not None and not res.get('convert_reason') and not CL.is_valid(body):
        return False
    return True


def _validate_session(reason):
    """2.0.5: Sitzung gegen die Konstruktion prüfen (Öffnen/Nesten). Anderes Dokument -> zurücksetzen;
    ungültige Auswahl-Einträge entfernen; Auftrag mit gelöschten Teilen oder (ganz/teilweise) manuell gelöschten
    Rohteilen -> frischer Kontext. Wirft nie. Rückgabe 'ok' | 'reset' | 'dropped'."""
    try:
        dk = _doc_key()
        sd = _state.get('ctx_doc') or _state.get('sess_doc')
        if dk and sd and sd != dk and (_state.get('ctx') or _sel()):
            _reset_session('anderes Dokument aktiviert')
            return 'reset'
        gone = [k for k, e in list(_sel().items()) if not _entry_valid(e)]
        for k in gone:
            _sel().pop(k, None)
            if k in _sel_order():
                _sel_order().remove(k)
            _state['part_settings'].pop(k, None)
        if gone:
            L.info('%s: %d Auswahl-Eintrag/Einträge nicht mehr gültig – entfernt', reason, len(gone))
        ctx = _state.get('ctx')
        if not ctx:
            return 'ok'
        bad = [pid for pid, info in (ctx.get('parts') or {}).items()
               if isinstance(info, dict) and info.get('comp') is not None and not CL.is_valid(info['comp'])]
        if bad:
            _drop_context('%s: %d Teil(e) des Auftrags gelöscht' % (reason, len(bad)))
            return 'dropped'
        pid = (ctx.get('project') or {}).get('id')
        design = _design()
        if pid and design:
            inv = CL.inventory(design, None)
            n_have = sum(1 for x in inv['sheets'] if x['project_id'] == pid)
            proj = (inv['projects'].get(pid) or {}).get('data') or {}
            n_exp = len(proj.get('sheets') or [])
            if n_have == 0:
                _reset_session('Rohteile von Projekt %s gelöscht' % pid)
                return 'reset'
            if n_exp and n_have < n_exp:
                _drop_context('%s: %d von %d Rohteil(en) von Projekt %s gelöscht' % (reason, n_exp - n_have, n_exp,
                                                                                      pid))
                return 'dropped'
        return 'ok'
    except Exception:
        L.exc('Sitzung prüfen (übersprungen)')
        return 'ok'


def _readonly(design):
    """Fremde/schreibgeschützte Konstruktion (Review P2.5): dann nie schreiben."""
    doc = L.safe(lambda: design.parentDocument) or L.safe(lambda: _app.activeDocument)
    for f in (lambda: doc.isReadOnly, lambda: doc.dataFile.isReadOnly, lambda: design.rootComponent.isReferencedComponent):
        if L.safe(f) is True:
            return True
    return False


def _cam_read():
    cam, _ws = CAM.get_cam(_app, _ui, [], create=False)    # nur lesen, kein Arbeitsbereichswechsel
    return cam


def _targets(inv):
    """Löschbare Nestings: [(project_id|'', Beschriftung, include_unassigned)]."""
    out = []
    by = {}
    for s in inv['sheets']:
        by.setdefault(s['project_id'], []).append(s)
    for pid in sorted(set(list(by.keys()) + [k for k, v in inv['projects'].items() if v.get('data')])):
        if not pid:
            continue
        pr = (inv['projects'].get(pid) or {}).get('data') or {}
        n = len(by.get(pid, []))
        out.append((pid, 'Projekt %s v%s – %d Rohteil(e)%s' % (pid, pr.get('version', '?'), n,
                                                              (', ' + pr.get('saved', '')[5:16]) if pr else ''), False))
    if by.get(''):
        out.append(('', 'unbekannt: Rohteile ohne Projekt-ID (ältere Version) – %d' % len(by['']), True))
    return out


def _current_project(inv):
    """Aktuelles Nesting der Palette: Auftrag -> zuletzt übernommen -> das einzige der Konstruktion."""
    have = set(s['project_id'] for s in inv['sheets']) | set(k for k, v in inv['projects'].items() if v.get('data'))
    for pid in (((_state.get('ctx') or {}).get('project') or {}).get('id'),
                (_state.get('applied_info') or {}).get('project'),
                ((_state.get('ctx') or {}).get('job') or {}).get('project', {}).get('id')
                if isinstance(((_state.get('ctx') or {}).get('job') or {}).get('project'), dict) else None):
        if pid and pid in have:
            return pid, None
    tg = _targets(inv)
    if len(tg) == 1:
        return tg[0][0], tg[0][2]
    return None, tg


def _delete_flow(req):
    """Schritt 1 (CustomEvent, kein Befehl aktiv): Ziel bestimmen, nachfragen, CAM-Setups löschen bzw. lösen.
    Schritt 2: interner Befehl CMD_DELETE_NEST (Rohteile, Kopien, Attribute, Marker), danach leere Palette."""
    busy = _state.get('busy_delete')
    if busy and time.time() - busy < 300:
        L.warn('Nesting löschen läuft bereits – doppelter Aufruf ignoriert')
        return
    _state['busy_delete'] = time.time()
    ok_started = False
    try:
        design = _design()
        if not design:
            _msg('Keine Konstruktion aktiv.')
            return
        if _readonly(design):
            _msg('Die Konstruktion ist schreibgeschützt/fremd – es wird nichts gelöscht.')
            return
        cam = L.safe(_cam_read)
        inv = CL.inventory(design, cam)
        pid, unassigned = req.get('project'), bool(req.get('unassigned'))
        if pid is None and not unassigned:
            pid, extra = _current_project(inv)
            if pid is None:
                if not extra:
                    _to_palette('notice', {'level': 'info', 'text': 'Kein Nesting in dieser Konstruktion – nichts zu '
                                                                   'löschen.'})
                    _reset_session('Nesting löschen: nichts vorhanden')
                    return
                _msg('Diese Konstruktion enthält %d Nestings:\n%s\n\nWelches gelöscht werden soll, bitte mit '
                     '„LazyNest löschen“ (Werkzeugleiste CA Tools) wählen.' % (len(extra), '\n'.join(
                         '• ' + t[1] for t in extra)))
                return
            unassigned = bool(extra)          # einziges Ziel = Rohteile ohne Projekt
        cur = set([((_state.get('ctx') or {}).get('project') or {}).get('id'),
                   (_state.get('applied_info') or {}).get('project')]) - set([None])
        is_cur = (pid in cur) or not cur
        protect = set() if is_cur else _protect_idents()     # eigenes Nesting: Sitzung wird ohnehin geleert
        plan = CL.plan_delete(inv, pid or '', protect, include_unassigned=unassigned)
        if CL.plan_empty(plan):
            _reset_session('Nesting löschen: nichts gefunden')
            return
        what = CL.describe(plan)
        if not _ask('Nesting %s löschen?\n\n%s\n\nDie Original-Teile bleiben unverändert. Projektdateien werden nach '
                    'projects\\geloescht verschoben.' % (pid or '(ohne Projekt)', '\n'.join('• ' + w for w in what))):
            L.info('Nesting löschen abgebrochen (%s)', pid)
            return
        keep_hist = False
        if pid and plan['sheets'] and CL.has_history(plan):
            # 2.0.5: Ja -> alles (wie 2.0.4); Nein -> nur Rohteile/CAM-Setups + Sitzung, Kopien/Historie bleiben
            keep_hist = not _ask('Auch LazyNest Kopien und Projekt-Historie entfernen?\n\n'
                                 'Ja: Baugruppen-Kopien, Projektdaten in der Konstruktion und Projektdatei werden '
                                 'ebenfalls entfernt (Datei nach projects\\geloescht).\n'
                                 'Nein: nur die Rohteile (und ihre CAM-Setups) löschen und LazyNest leeren – Kopien '
                                 'und Projekt-Historie bleiben erhalten.')
            if keep_hist:
                plan = CL.keep_history(plan)
                L.info('Nesting löschen %s: Kopien/Historie bleiben', pid)
        log = []
        n_setups = 0
        if cam is not None:
            n_setups = CL.delete_setups(plan, log, cam)       # nur Setups mit cam_link dieses Nestings
            if n_setups < 0:
                _msg('Nichts gelöscht: %s' % '; '.join(log))
                return
            # Setups OHNE Verknüpfung (vom Anwender): nur Modelle/LazySetup-Bearbeitungen dieser Rohteile lösen
            nu = CL.release_user_setups(cam, [s['name'] for s in plan['sheets']], log, make_coll=CAM._coll)
            if nu:
                log.append('%d eigene(s) CAM-Setup(s) behalten – nur Modelle aus den gelöschten Rohteilen entfernt.'
                           % nu)
        cam = None
        L.info('Nesting löschen %s: %d CAM-Setup(s) gelöscht %s', pid, n_setups, log)
        _state['del_job'] = {'project': pid or '', 'unassigned': unassigned, 'setups': n_setups, 'log': log,
                             'protect': protect, 'reset': is_cur, 'doc': _doc_key(), 'keep_history': keep_hist}
        ok_started = True
        _execute_cmd(CMD_DELETE_NEST)
    except Exception:
        _err('Nesting löschen fehlgeschlagen')
    finally:
        if not ok_started:
            _state['busy_delete'] = None


class DeleteNestExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            job = _state.pop('del_job', None)
            design = _design()
            if not (job and design):
                return
            if job.get('doc') and _doc_key() != job['doc']:
                _state['busy_delete'] = None
                _msg('Dokument wurde gewechselt – Nesting löschen abgebrochen (CAM-Setups wurden bereits entfernt). '
                     'Im ursprünglichen Dokument erneut „Nesting löschen“ wählen.')
                return
            log = list(job.get('log') or [])
            inv = CL.inventory(design, None)
            plan = CL.plan_delete(inv, job['project'], job.get('protect'), include_unassigned=job['unassigned'])
            if job.get('keep_history'):
                plan = CL.keep_history(plan)
            rep = CL.delete_rest(design, plan, log, F._delete_sheet_occ)
            done = CL.plan_done(design, plan)
            # Projektdatei erst NACH vollständigem Erfolg beider Schritte verschieben (Review P1.3/P1.4)
            moved = CL.move_project_files(P.PROJECT_DIR, plan['files_for'], log) if done else []
            if not done:
                log.append('Nicht alles ließ sich löschen – „Nesting löschen“ einfach erneut ausführen '
                           '(setzt dort fort, Projektdatei bleibt bis dahin).')
            L.info('Nesting %s gelöscht: %s, %d Datei(en) verschoben, %s', job['project'], json.dumps(rep), len(moved),
                   log)
            t = ('Nesting %s gelöscht: %d Rohteil(e), %d CAM-Setup(s), %d Kopie(n), %d Projektdatei(en) nach '
                 'projects\\geloescht verschoben.' % (job['project'] or '(ohne Projekt)', rep['sheets'], job['setups'],
                                                      rep['copies'], len(moved)))
            if job.get('keep_history'):
                t = ('Nesting %s gelöscht: %d Rohteil(e), %d CAM-Setup(s). LazyNest Kopien und Projekt-Historie '
                     'bleiben erhalten.' % (job['project'], rep['sheets'], job['setups']))
            _state['del_done'] = {'text': t, 'log': log, 'reset': job.get('reset', True)}
            _fire(EV_CLEANUP)          # Meldung + leere Palette nach Ende des Befehls; Rest-Waisen gleich mit
        except Exception:
            _err('Nesting löschen fehlgeschlagen')


class DocActivated(adsk.core.DocumentEventHandler):
    """Review P4.12: Palette gehört zu Dokument + Nesting; anderes Dokument aktiv -> Sitzung verwerfen.
    2.0.5: auch eine reine Auswahlliste (noch kein Auftrag) gehört zu ihrem Dokument."""
    def notify(self, args):
        try:
            dk = _doc_key()
            sd = _state.get('ctx_doc') or _state.get('sess_doc')
            if (_state.get('ctx') or _sel()) and sd and dk and dk != sd:
                _reset_session('anderes Dokument aktiviert')
        except Exception:
            L.exc('documentActivated')


def _open_doc_keys():
    out = set()
    for d in CL.items(L.safe(lambda: _app.documents)):
        for f in (lambda: d.creationId, lambda: d.dataFile.id, lambda: d.name):
            v = L.safe(f)
            if isinstance(v, str) and v:
                out.add(v)
                break
    return out


class DocClosed(adsk.core.DocumentEventHandler):
    """2.0.5: Dokument der Sitzung geschlossen -> Sitzung verwerfen (nicht erst beim nächsten Öffnen)."""
    def notify(self, args):
        try:
            sd = _state.get('ctx_doc') or _state.get('sess_doc')
            if not sd or not (_state.get('ctx') or _sel()):
                return
            if sd not in _open_doc_keys():
                _reset_session('Dokument geschlossen')
        except Exception:
            L.exc('documentClosed')


class DeleteNestCreated(_SimpleCreated):
    EXEC = DeleteNestExecute


def _check_session_and_orphans():
    """Beim Öffnen: Sitzung eines anderen Dokuments / gelöschten Nestings verwerfen; Waisen erkennen (nur lesen)
    und Aufräumen als eigenen Schritt anstoßen. Wirft nie – neues Nesting wird nie blockiert."""
    try:
        design = _design()
        if not design:
            return
        dk = _doc_key()
        ctx = _state.get('ctx')
        if ctx and _state.get('ctx_doc') and dk and _state.get('ctx_doc') != dk:
            _reset_session('anderes Dokument')
            ctx = None
        inv = CL.inventory(design, L.safe(_cam_read))
        if ctx and (ctx.get('project') or {}).get('id'):
            pid = ctx['project']['id']
            if not any(s['project_id'] == pid for s in inv['sheets']):
                L.info('Projekt %s der Sitzung hat keine Rohteile mehr – Sitzung zurückgesetzt', pid)
                _reset_session('Rohteile von Projekt %s gelöscht' % pid)
        # nach Rückgängig (Strg+Z) eines Löschens: Projekt-Attribut wieder da -> Datei zurückholen (nur Dateisystem)
        live = [k for k, v in inv['projects'].items() if v.get('data')]
        back = CL.restore_project_files(P.PROJECT_DIR, live, [])
        if back:
            L.info('Projektdatei(en) wiederhergestellt (Löschen rückgängig gemacht?): %s', back)
            _state['open_notice'] = ('Gelöschtes Nesting wurde wiederhergestellt (Rückgängig) – Projektdatei '
                                     'zurückgeholt. CAM-Setups stellt Rückgängig NICHT wieder her: bei Bedarf erneut '
                                     '„Übernehmen“.')
        orph = CL.find_orphans(inv, _protect_idents())
        if CL.plan_empty(orph):
            return
        sig = json.dumps(orph['reasons'])
        if sig == _state.get('cleanup_sig'):
            return                   # schon gefragt/abgelehnt – nicht erneut in dieser Sitzung
        _state['cleanup_sig'] = sig
        L.info('Waisen erkannt (nur gelesen): %s', '; '.join(orph['reasons']))
        if _readonly(design):
            L.info('Konstruktion schreibgeschützt/fremd – nur Hinweis, nichts geändert')
            return
        _state['cleanup_req'] = True
        _fire(EV_CLEANUP)            # Rückfrage NACH dem Öffnen (eigenes Ereignis)
    except Exception:
        L.exc('Waisen-Prüfung (übersprungen)')


def _cleanup_flow():
    """CustomEvent: Waisen aufräumen (CAM zuerst, außerhalb eines Befehls) bzw. Abschluss von „Nesting löschen“."""
    done = _state.pop('del_done', None)
    asked = _state.pop('cleanup_req', None)
    _state['busy_delete'] = None
    try:
        design = _design()
        if design and (done or asked) and not _readonly(design):
            cam = L.safe(_cam_read)
            inv = CL.inventory(design, cam)
            orph = CL.find_orphans(inv, _protect_idents())
            # Review P2.5: NIE still beim Öffnen löschen – Befunde zeigen und bestätigen lassen
            if not CL.plan_empty(orph) and _ask(
                    'Reste gelöschter Nestings gefunden:\n\n%s\n\nJetzt aufräumen? (Vorhandene Rohteile und '
                    'Original-Teile bleiben unverändert.)' % '\n'.join('• ' + r for r in orph['reasons'][:15])):
                log = []
                n = CL.delete_setups(orph, log, cam) if cam is not None else 0
                cam = None
                if n >= 0:
                    _state['cleanup_job'] = {'setups': n, 'log': log, 'reasons': orph['reasons']}
                    _execute_cmd(CMD_CLEANUP)
                else:
                    _msg('Nicht aufgeräumt: %s' % '; '.join(log))
            elif not CL.plan_empty(orph):
                L.info('Aufräumen abgelehnt: %s', '; '.join(orph['reasons']))
    except Exception:
        L.exc('Waisen aufräumen')
    if done:
        if done.get('reset', True):
            _reset_session('Nesting gelöscht')
        _to_palette('notice', {'level': 'info', 'tab': 'Auswahl', 'text': done['text']})
        _msg(done['text'] + (('\n\n' + '\n'.join(done['log'])) if done.get('log') else ''))


class CleanupExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            job = _state.pop('cleanup_job', None)
            design = _design()
            if not (job and design):
                return
            log = list(job.get('log') or [])
            inv = CL.inventory(design, None)
            orph = CL.find_orphans(inv, _protect_idents())
            rep = CL.delete_rest(design, orph, log)
            moved = CL.move_project_files(P.PROJECT_DIR, orph['files_for'], log)
            L.info('Waisen aufgeräumt: %s; %d Setup(s), %s, %d Datei(en) verschoben %s', '; '.join(job['reasons']),
                   job['setups'], json.dumps(rep), len(moved), log)
            n = job['setups'] + rep['copies'] + len(orph['files_for'])
            if n:
                _to_palette('notice', {'level': 'info', 'tab': 'Auswahl',
                                       'text': 'Aufgeräumt (Rohteile wurden manuell gelöscht): %s.' % ', '.join(
                                           x for x in (
                                               ('%d Nesting-Projekt(e)' % len(orph['files_for'])) if orph['files_for'] else '',
                                               ('%d CAM-Setup(s)' % job['setups']) if job['setups'] else '',
                                               ('%d Kopie(n)' % rep['copies']) if rep['copies'] else '') if x)})
        except Exception:
            L.exc('Waisen aufräumen')


class CleanupCreated(_SimpleCreated):
    EXEC = CleanupExecute


class DeleteCreated(adsk.core.CommandCreatedEventHandler):
    """„LazyNest löschen“ (Werkzeugleiste): bei mehreren Nestings Auswahl, sonst direkt. Gleicher Ablauf wie der
    Knopf „Nesting löschen“ der Palette (Rückfrage, CAM zuerst, alles des Nestings, Originale bleiben)."""
    def notify(self, args):
        try:
            design = _design()
            inv = CL.inventory(design, None) if design else None
            tg = _targets(inv) if inv else []
            _state['del_targets'] = tg
            cmd = args.command
            if len(tg) > 1:
                dd = cmd.commandInputs.addDropDownCommandInput('ln_del_target', 'Nesting',
                                                               adsk.core.DropDownStyles.TextListDropDownStyle)
                cur = ((_state.get('ctx') or {}).get('project') or {}).get('id')
                for i, t in enumerate(tg):
                    dd.listItems.add(t[1], t[0] == cur if cur else i == 0)
            h = DeleteExecute()
            cmd.execute.add(h)
            handlers.append(h)
        except Exception:
            _err('LazyNest löschen')


class DeleteExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            tg = _state.pop('del_targets', None) or []
            if not tg:
                _msg('Keine von LazyNest/ContourNest erzeugten Rohteile oder Projekte gefunden.')
                return
            t = tg[0]
            inp = L.safe(lambda: args.command.commandInputs.itemById('ln_del_target'))
            if inp is not None and len(tg) > 1:
                sel = L.safe(lambda: inp.selectedItem.name)
                t = next((x for x in tg if x[1] == sel), tg[0])
            _state['del_req'] = {'project': t[0], 'unassigned': t[2], 'source': 'toolbar'}
            _fire(EV_DELETE)            # Rückfrage + CAM erst NACH Ende dieses Befehls
        except Exception:
            _err('Löschen fehlgeschlagen')


# ====================================================================== run / stop
def _tlog(level, msg):
    (L.warn if level == 'WARN' else L.info)('%s', msg)


def _tooltip(tip):
    return '%s\n(%s v%s)' % (tip, APP_NAME, VERSION)


class CreateExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            _open_main()
        except Exception:
            _err('CA LazyNest konnte nicht geöffnet werden')


class CreateCommandCreated(adsk.core.CommandCreatedEventHandler):
    """„CA LazyNest“: KEIN Dialog mehr – öffnet direkt das Hauptfenster (Palette)."""
    def notify(self, args):
        try:
            design = _design_or_switch(CMD_CREATE)
            if not design:
                if _state.get('switch_cmd') != CMD_CREATE:
                    _msg('Bitte im Arbeitsbereich KONSTRUKTION ausführen.')
                return
            h = CreateExecute()
            args.command.execute.add(h)
            handlers.append(h)
        except Exception:
            _err('CA LazyNest öffnen')


COMMANDS = (
    (CMD_CREATE, 'CA LazyNest', 'CA LazyNest öffnen: Teile wählen, Platte einstellen, nesten, Vorschau bearbeiten und '
     'übernehmen – alles in einem Fenster', 'LazyNest', CreateCommandCreated, True),
    (CMD_EDIT, 'LazyNest bearbeiten', 'Gespeichertes Nesting-Projekt mit seiner Anordnung in CA LazyNest öffnen, '
     'ändern und die Rohteile ersetzen', 'Repeat', EditCreated, True),
    (CMD_REPEAT, 'LazyNest wiederholen', 'Gespeichertes Nesting-Projekt (gewähltes Rohteil, sonst das neueste) neu '
     'rechnen – fixierte Teile bleiben', 'Repeat', RepeatCreated, True),
    (CMD_DELETE, 'LazyNest löschen', 'Ein Nesting vollständig löschen: Rohteile mit Teilen, verknüpfte CAM-Setups, '
     'Baugruppen-Kopien und Projektdaten – Original-Teile bleiben', 'Delete',
     DeleteCreated, True),
    (CMD_APPLY, 'Nesting übernehmen', 'Ergebnis der LazyNest-Palette übernehmen (intern)', 'Create', ApplyCreated, False),
    (CMD_PICK, 'LazyNest: Teile wählen', 'Teile für CA LazyNest auswählen (aus der Palette gestartet)', 'LazyNest',
     PickCreated, False),
    (CMD_PREP, 'LazyNest: Nesting vorbereiten', 'Auftrag aus der Auswahl bauen (intern)', 'LazyNest', PrepCreated,
     False),
    (CMD_REFRESH, 'LazyNest: Kopien aktualisieren', 'Baugruppen-Kopien aus der Quelle neu lesen (intern)', 'Repeat',
     RefreshCreated, False),
    (CMD_DELETE_NEST, 'LazyNest: Nesting löschen', 'Rohteile, Kopien und Projektdaten eines Nestings löschen '
     '(intern)', 'Delete', DeleteNestCreated, False),
    (CMD_CLEANUP, 'LazyNest: aufräumen', 'Reste manuell gelöschter Nestings entfernen (intern)', 'Delete',
     CleanupCreated, False),
    (CMD_DIAG, 'LazyNest Diagnose', 'Rohteile/Teile-Lagen in eine Diagnose-Datei schreiben (ändert nichts)',
     'Repeat', DiagCreated, 'addins'),
    (CMD_IMPORT, 'LazyNest-Projekt importieren', 'Gesicherte Projektdatei (projects/*.json, auch von ContourNest) '
     'laden und bearbeiten', 'Repeat', ImportCreated, 'addins'),
)


def _migrate():
    """Einmalig: Benutzerdaten aus dem alten ContourNest-Ordner übernehmen (nie überschreiben)."""
    try:
        extra = []
        ad = os.environ.get('APPDATA')
        if ad:
            extra.append(os.path.join(ad, 'Autodesk', 'Autodesk Fusion 360', 'API', 'AddIns', 'ContourNest'))
        rep = CORE.ca_migrate.run_once(HERE, PREFS_FILE, 'ContourNest', ('user_prefs.json', 'user_stock.json'), True,
                                       extra, L.warn, mark_key='migrated_from_contournest')
        if rep is not None:
            L.info('Migration ContourNest -> LazyNest: Quelle %s, übernommen %d (%s), vorhanden %d',
                   rep['source'], len(rep['copied']), ', '.join(rep['copied'][:20]), len(rep['skipped']))
            _state['migration'] = {'source': rep['source'], 'copied': len(rep['copied'])} if rep['copied'] else None
    except Exception:
        L.exc('Migration ContourNest')


def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface
        L.info('==== %s v%s run() – Fusion %s, Python %s, Pfad %s', APP_NAME, VERSION, L.safe(lambda: _app.version),
               sys.version.split()[0], HERE)
        _migrate()
        for ev_id in (EV_APPLY, EV_VERIFY, EV_SWITCH, EV_PICK, EV_NEST, EV_REFRESH, EV_CAM, EV_DELETE, EV_CLEANUP):
            try:
                _app.unregisterCustomEvent(ev_id)
            except Exception:
                pass
            ev = _app.registerCustomEvent(ev_id)
            h = DeferredEvent(ev_id)
            ev.add(h)
            handlers.append(h)
            _custom_events[ev_id] = (ev, h)
        try:
            hd = DocActivated()
            _app.documentActivated.add(hd)
            handlers.append(hd)
            _state['doc_handler'] = hd
        except Exception:
            L.exc('documentActivated verbinden')
        try:
            hc = DocClosed()
            _app.documentClosed.add(hc)
            handlers.append(hc)
            _state['doc_close_handler'] = hc
        except Exception:
            L.exc('documentClosed verbinden')
        ws = _ui.workspaces.itemById(WS)
        ca = T.get_panel(_ui, 'design', _tlog)          # gemeinsames Panel „CA Tools“ (CA_ToolsPanel)
        defs = {}
        for cid, name, tip, res, H, visible in COMMANDS:
            cd = _ui.commandDefinitions.itemById(cid)
            if cd:
                cd.deleteMe()
            cd = _ui.commandDefinitions.addButtonDefinition(cid, name, _tooltip(tip), os.path.join(RES, res))
            h = H()
            cd.commandCreated.add(h)
            handlers.append(h)
            defs[cid] = cd
            if not visible:
                continue
            for pid in (PANELS if visible is True else (ADDINS_PANEL,)):
                panel = ws.toolbarPanels.itemById(pid) if ws else None
                if not panel:
                    continue
                T.add_command(panel, cd)
            if visible is True:
                panel = ca or (ws.toolbarPanels.itemById(ADDINS_PANEL) if ws else None)
                if panel:
                    T.add_command(panel, cd, promoted=(cid == CMD_CREATE))
        cam = T.get_panel(_ui, 'cam', _tlog)            # FERTIGUNG > FRÄSEN (CA_ToolsPanel_CAM)
        if cam is None:
            cws = L.safe(lambda: _ui.workspaces.itemById(CAM_WS))
            cam = L.safe(lambda: cws.toolbarPanels.itemById(CAM_ADDINS_PANEL)) if cws else None
        for cid, big in CAM_CMDS:
            if cam is not None:
                T.add_command(cam, defs[cid], promoted=big)
        T.register(_ui, 'LazyNest', VERSION, os.path.join(RES, 'CATools'), handlers, _tlog)
    except Exception:
        tb = L.exc('run()')
        if _ui:
            _ui.messageBox('CA LazyNest konnte nicht gestartet werden:\n%s\n\nProtokoll: %s' % (tb, L.LOG_FILE),
                           APP_NAME)


def stop(context):
    try:
        hd = _state.pop('doc_handler', None)
        if hd is not None:
            L.safe(lambda: _app.documentActivated.remove(hd))
        hc = _state.pop('doc_close_handler', None)
        if hc is not None:
            L.safe(lambda: _app.documentClosed.remove(hc))
        if _ui:
            pal = _ui.palettes.itemById(PALETTE_ID)
            if pal:
                _remember_size(pal)
                pal.deleteMe()
            for ev_id, (ev, h) in list(_custom_events.items()):
                try:
                    ev.remove(h)
                except Exception:
                    pass
                try:
                    _app.unregisterCustomEvent(ev_id)
                except Exception:
                    pass
            _custom_events.clear()
            _state['pal_hooked'] = False
            ws = _ui.workspaces.itemById(WS)
            cws = L.safe(lambda: _ui.workspaces.itemById(CAM_WS))
            own = [c[0] for c in COMMANDS]
            T.unregister(_ui, 'LazyNest', own, _tlog)    # nur eigene Befehle; leeres „CA Tools“ wird gelöscht
            for cid in own:
                for w, pid in [(ws, p) for p in PANELS + (ADDINS_PANEL,)] + [(cws, CAM_ADDINS_PANEL)]:
                    panel = L.safe(lambda: w.toolbarPanels.itemById(pid)) if w else None
                    ctl = panel.controls.itemById(cid) if panel else None
                    if ctl:
                        ctl.deleteMe()
                cd = _ui.commandDefinitions.itemById(cid)
                if cd:
                    cd.deleteMe()
    except Exception:
        tb = L.exc('stop()')
        if _ui:
            _ui.messageBox('CA LazyNest stop():\n%s' % tb, APP_NAME)
    handlers.clear()
    L.info('==== %s stop()', APP_NAME)


# Alle Handler protokollieren (Ereignis + JEDE Ausnahme mit Traceback in logs/lazynest.log)
for _cls in (CreateCommandCreated, CreateExecute, PaletteHTML, PaletteClosed, ApplyCreated, ApplyExecute,
             RepeatCreated, RepeatExecute, DeleteCreated, DeleteExecute, DiagCreated, DiagExecute, DeferredEvent,
             EditCreated, EditExecute, ImportCreated, ImportExecute, PickCreated, PickExecute, PrepCreated,
             PrepExecute, RefreshCreated, RefreshExecute):
    _cls.notify = L.wrap_handler(_cls.__name__, _cls.notify)
PickInputChanged.notify = L.wrap_handler('PickInputChanged', PickInputChanged.notify, quiet=True)
