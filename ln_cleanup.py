"""CA LazyNest 2.0.5 – Nesting löschen und Waisen aufräumen.

Ohne adsk-Import (nur Objekt-Schnittstellen der Fusion-API) -> mit Attrappen testbar.
Was zu einem Nesting (Projekt <id>) gehört:
  * Rohteil-Occurrences unter der Wurzel mit ContourNest/sheet + ContourNest/project_id == <id> (inkl. eingefügter
    Teile-Occurrences und Silhouetten-Skizzen von LazySetup – sie liegen IN der Rohteil-Komponente)
  * CAM-Setups mit ContourNest/cam_link.project_id == <id> (inkl. LazySetup-Bearbeitungen darin)
  * Baugruppenmodus-Kopien unter „LazyNest Kopien“ (ContourNest/copy_source), die nur dieses Projekt nutzt
  * Attribute ContourNest/project_<id>, Eintrag im Index ContourNest/projects, ggf. ContourNest/lastJob
  * Projektdateien <Add-In>/projects/nest_*.json mit dieser project_id (werden nach projects/geloescht verschoben)
  * Marker CA_Tools/computed_state und CA_Tools/compute_state (Neuberechnung danach erzwingen)
Original-Teile (alles ohne diese Attribute) werden nie angefasst."""
import json
import os
import shutil
import time

GROUP = 'ContourNest'
COPY_ATTR = 'copy_source'
COPY_ROOT_ATTR = 'copy_root'
LINK_ATTR = 'cam_link'
KEPT_ATTR = 'kept_projects'      # 2.0.5: gelöscht mit „Kopien/Historie behalten“ – keine Waisen-Rückfrage
MARKERS = (('CA_Tools', 'computed_state'), ('CA_Tools', 'compute_state'))


def _g(fn, default=None):
    try:
        v = fn()
        return default if v is None else v
    except Exception:
        return default


def items(coll):
    """Fusion-Sammlung (count/item) oder Python-Liste -> Liste."""
    if coll is None:
        return []
    n = _g(lambda: coll.count)
    if isinstance(n, int):
        out = []
        for i in range(n):
            o = _g(lambda: coll.item(i))
            if o is not None:
                out.append(o)
        return out
    try:
        return list(coll)
    except Exception:
        return []


def attr(obj, name, group=GROUP):
    a = _g(lambda: obj.attributes.itemByName(group, name))
    return a.value if a is not None else None


def jattr(obj, name, group=GROUP, default=None):
    v = attr(obj, name, group)
    if v is None:
        return default
    try:
        return json.loads(v)
    except Exception:
        return v


def ident(obj):
    """Stabile Kennung einer Komponente/Occurrence für Vergleiche (Fusion liefert je Zugriff neue Wrapper)."""
    for f in (lambda: obj.id, lambda: obj.entityToken, lambda: obj.fullPathName, lambda: obj.name):
        v = _g(f)
        if isinstance(v, str) and v:
            return v
    return str(id(obj))


def is_valid(obj):
    v = _g(lambda: obj.isValid, True)
    return v is not False


# ---------------------------------------------------------------- Bestandsaufnahme (nur lesen)
def project_ids(design):
    v = attr(design, 'projects')
    try:
        ids = json.loads(v) if v else []
        return [i for i in ids if isinstance(i, str)]
    except Exception:
        return []


def kept_ids(design):
    """2.0.5: Projekte, deren Rohteile gelöscht wurden, deren Kopien/Historie aber bewusst bleiben."""
    v = jattr(design, KEPT_ATTR, default=[])
    return [i for i in v if isinstance(i, str)] if isinstance(v, list) else []


def set_kept(design, add=(), remove=(), log=None):
    ids = [i for i in kept_ids(design) if i not in set(remove or ())]
    for i in add or ():
        if i and i not in ids:
            ids.append(i)
    try:
        if ids:
            design.attributes.add(GROUP, KEPT_ATTR, json.dumps(ids))
        else:
            _del_attr(design, KEPT_ATTR)
        return True
    except Exception as e:
        if log is not None:
            log.append('Historie-Liste: %s' % e)
        return False


def project_attr_ids(design):
    """IDs aller vorhandenen Attribute project_<id> (auch ohne Index-Eintrag)."""
    out = []
    for a in items(_g(lambda: design.attributes.itemsByGroup(GROUP))):
        nm = _g(lambda: a.name, '') or ''
        if nm.startswith('project_') and nm != 'project_id':
            out.append(nm[len('project_'):])
    return out


def _sheets(design):
    root = design.rootComponent
    out = []
    for occ in items(_g(lambda: root.occurrences)):
        d = jattr(occ, 'sheet')
        if d is None:
            continue
        if not isinstance(d, dict):
            d = {}
        pid = attr(occ, 'project_id') or ''
        comps = set()
        for ch in items(_g(lambda: occ.component.occurrences)):
            c = _g(lambda: ch.component)
            if c is not None:
                comps.add(ident(c))
        out.append({'occ': occ, 'project_id': pid, 'name': _g(lambda: occ.name, '') or '',
                    'no': int(d.get('project_sheet') or d.get('index') or 0), 'comps': comps})
    return out


def _copy_root(design):
    for o in items(_g(lambda: design.rootComponent.occurrences)):
        if attr(o, COPY_ROOT_ATTR):
            return o
    return None


def _copies(design):
    ro = _copy_root(design)
    out = []
    if ro is None:
        return ro, out
    for o in items(_g(lambda: ro.component.occurrences)):
        c = _g(lambda: o.component)
        a = jattr(c, COPY_ATTR) if c is not None else None
        if isinstance(a, dict) and a.get('key'):
            inst = _g(lambda: design.rootComponent.allOccurrencesByComponent(c).count)
            out.append({'occ': o, 'comp': c, 'key': a['key'], 'ident': ident(c),
                        'instances': inst if isinstance(inst, int) else 1,
                        'referenced': bool(_g(lambda: c.isReferencedComponent, False)),
                        'token': _g(lambda: c.entityToken, '') or '', 'name': a.get('name') or _g(lambda: c.name, '')})
    return ro, out


def _setups(cam):
    out = []
    if cam is None:
        return out
    for s in items(_g(lambda: cam.setups)):
        ln = jattr(s, LINK_ATTR)
        if isinstance(ln, dict) and ln.get('project_id') is not None:
            out.append({'setup': s, 'link': ln, 'name': _g(lambda: s.name, '?')})
    return out


def _owned_copy(project, cp):
    """Gehört die Kopie zum Projekt? Teil-ID = Quellschlüssel 'A:…' oder Komponenten-ID/-Token der Quelle."""
    if not isinstance(project, dict):
        return False
    srcs = project.get('sources') or {}
    if cp['key'] in srcs:
        return True
    for p in (project.get('job') or {}).get('parts') or []:
        if p.get('id') == cp['key']:
            return True
    for s in srcs.values():
        if not isinstance(s, dict):
            continue
        if s.get('component_id') and s.get('component_id') == cp['ident']:
            return True
        if s.get('component_token') and s.get('component_token') == cp['token']:
            return True
    return False


def inventory(design, cam=None):
    ids = project_ids(design)
    attr_ids = project_attr_ids(design)
    projects = {}
    for pid in set(ids) | set(attr_ids):
        data = jattr(design, 'project_' + pid)
        projects[pid] = {'in_list': pid in ids, 'has_attr': isinstance(data, dict),
                         'data': data if isinstance(data, dict) else None}
    sheets = _sheets(design)
    ro, copies = _copies(design)
    return {'projects': projects, 'ids': ids, 'sheets': sheets, 'copy_root': ro, 'copies': copies,
            'setups': _setups(cam), 'last_job': jattr(design, 'lastJob'), 'kept': kept_ids(design)}


def _copy_free(cp):
    """Kopie darf weg: nur LazyNest-Kopie (Attribut, geprüft in _copies), nicht referenziert (xref) und keine
    weitere Instanz außer der in „LazyNest Kopien“."""
    return not cp.get('referenced') and int(cp.get('instances') or 1) <= 1


def _used_by_sheets(cp, sheets):
    return any(cp['ident'] in s['comps'] for s in sheets)


# ---------------------------------------------------------------- Pläne
def _last_job_of(inv, pid, proj):
    """lastJob gehört zu GENAU diesem Nesting? (Projekt-ID im Auftrag oder gleiche Job-ID wie im Projekt)"""
    lj = inv.get('last_job')
    if not isinstance(lj, dict) or not pid:
        return False
    job = lj.get('job') or {}
    lj_pid = (job.get('project') or {}).get('id') if isinstance(job.get('project'), dict) else None
    if lj_pid:
        return lj_pid == pid
    jid = job.get('jobId')
    return bool(jid) and jid == ((proj or {}).get('job') or {}).get('jobId')


def plan_delete(inv, project_id, protect=(), include_unassigned=False):
    """Löschplan für EIN Nesting. protect: Komponenten-Kennungen, die nicht gelöscht werden dürfen (Auswahl/Auftrag
    der laufenden Sitzung). include_unassigned: zusätzlich Rohteile ohne Projekt-Kennung (Altbestand)."""
    pid = project_id
    sheets = [s for s in inv['sheets'] if s['project_id'] == pid or (include_unassigned and not s['project_id'])]
    gone = set(id(s['occ']) for s in sheets)
    remaining = [s for s in inv['sheets'] if id(s['occ']) not in gone]
    live = dict((k, v['data']) for k, v in inv['projects'].items() if k != pid and v.get('data'))
    proj = (inv['projects'].get(pid) or {}).get('data')
    protect = set(protect or ())
    copies = []
    for cp in inv['copies']:
        mine = _owned_copy(proj, cp) or any(cp['ident'] in s['comps'] for s in sheets)
        if not mine or cp['ident'] in protect or cp['key'] in protect:
            continue
        if _used_by_sheets(cp, remaining) or any(_owned_copy(d, cp) for d in live.values()):
            continue
        if not _copy_free(cp) and not any(cp['ident'] in s['comps'] for s in sheets):
            continue          # weitere Instanz außerhalb der zu löschenden Rohteile (z. B. vom Anwender kopiert)
        copies.append(cp)
    names = set(s['name'] for s in sheets)
    setups = [u for u in inv['setups'] if u['link'].get('project_id') == pid
              or (include_unassigned and not u['link'].get('project_id'))
              or (u['link'].get('sheet_name') in names and not any(
                  r['project_id'] == u['link'].get('project_id') and r['no'] == int(u['link'].get('project_sheet') or -1)
                  for r in remaining))]
    drop_last = _last_job_of(inv, pid, proj)
    copy_left = [c for c in inv['copies'] if not any(c is x for x in copies)]
    return {'project_id': pid, 'sheets': sheets, 'setups': setups, 'copies': copies,
            'attrs': ['project_' + pid] if pid in inv['projects'] else [],
            'list_remove': [pid] if pid in inv['ids'] else [], 'last_job': drop_last,
            'remove_copy_root': inv['copy_root'] is not None and not copy_left and bool(copies),
            # Marker (CA_Tools) gelten für die ganze Konstruktion: nur entfernen, wenn kein Rohteil übrig bleibt –
            # sonst entwertet sich der Fingerabdruck ohnehin selbst (Zeitachse ändert sich)
            'markers': bool(sheets) and not remaining, 'files_for': [pid] if pid else []}


def find_orphans(inv, protect=()):
    """Waisen nach manuellem Löschen (ganz oder teilweise). Löscht NIE vorhandene Rohteile.
    Rückgabe Plan wie plan_delete (sheets immer leer) + 'reasons' (Text je Befund) + 'dead_projects'."""
    have = set(s['project_id'] for s in inv['sheets'] if s['project_id'])
    keys = set((s['project_id'], s['no']) for s in inv['sheets'])
    names = set(s['name'] for s in inv['sheets'])
    protect = set(protect or ())
    reasons, attrs, lst, dead = [], [], [], []
    unassigned = any(not s['project_id'] for s in inv['sheets'])
    kept = set(inv.get('kept') or [])
    for pid, pr in sorted(inv['projects'].items()):
        if pid not in have and pid in kept:
            continue                     # 2.0.5: bewusst behaltene Historie/Kopien – keine Waise
        if pid not in have and unassigned:
            # Rohteile älterer Versionen tragen evtl. keine Projekt-Kennung – Zuordnung unsicher: nur Hinweis
            reasons.append('Projekt %s: keine zugeordneten Rohteile, aber Rohteile ohne Projekt vorhanden – bleibt'
                           % pid)
            continue
        if pid in have:
            if not pr['has_attr']:
                reasons.append('Projekt %s: Rohteile vorhanden, Projektdaten fehlen (bleibt, nur Hinweis)' % pid)
            continue
        dead.append(pid)
        if pr['has_attr']:
            attrs.append('project_' + pid)
        if pr['in_list']:
            lst.append(pid)
        reasons.append('Projekt %s: keine Rohteile mehr – Projektdaten entfernt' % pid)
    for pid in sorted(have):
        if pid not in inv['projects']:
            reasons.append('Rohteile von Projekt %s ohne Projektdaten – bleiben erhalten („LazyNest löschen“ '
                           'entfernt sie)' % pid)
    setups = []
    for u in inv['setups']:
        ln = u['link']
        no = int(ln.get('project_sheet') or -1)
        if (ln.get('project_id'), no) in keys:
            continue
        if ln.get('sheet_name') and ln.get('sheet_name') in names:
            continue                     # Rohteil gleichen Namens vorhanden – im Zweifel behalten
        setups.append(u)
        reasons.append('CAM-Setup „%s“: Rohteil %s/%s fehlt – gelöscht' % (u['name'], ln.get('project_id') or '-',
                                                                          ln.get('project_sheet')))
    live = dict((k, v['data']) for k, v in inv['projects'].items() if k not in dead and v.get('data'))
    copies = []
    for cp in inv['copies']:
        if cp['ident'] in protect or cp['key'] in protect:
            continue
        if _used_by_sheets(cp, inv['sheets']) or any(_owned_copy(d, cp) for d in live.values()):
            continue
        if not _copy_free(cp):
            continue
        copies.append(cp)
        reasons.append('Kopie „%s“: von keinem Rohteil/Projekt benutzt – gelöscht' % cp['name'])
    drop_last = any(_last_job_of(inv, d_, (inv['projects'].get(d_) or {}).get('data')) for d_ in dead)
    copy_left = [c for c in inv['copies'] if not any(c is x for x in copies)]
    return {'project_id': None, 'sheets': [], 'setups': setups, 'copies': copies, 'attrs': attrs,
            'list_remove': lst, 'last_job': drop_last,
            'remove_copy_root': inv['copy_root'] is not None and not copy_left and bool(copies),
            'markers': False, 'files_for': dead, 'reasons': reasons, 'dead_projects': dead}


def has_history(plan):
    """2.0.5: Enthält der Löschplan Kopien oder Projekt-Historie (Attribute, Liste, Datei)?"""
    return bool(plan.get('copies') or plan.get('attrs') or plan.get('list_remove') or plan.get('files_for'))


def keep_history(plan):
    """2.0.5: „Auch LazyNest Kopien und Projekt-Historie entfernen?“ -> Nein: nur Rohteile (+ CAM-Setups, die
    der Aufrufer vorher löscht) – Kopien, Projektdaten, Liste, lastJob und Projektdatei bleiben."""
    out = dict(plan)
    out.update({'copies': [], 'attrs': [], 'list_remove': [], 'last_job': False, 'remove_copy_root': False,
                'files_for': [], 'keep_history': True})
    return out


def plan_empty(plan):
    return not (plan['sheets'] or plan['setups'] or plan['copies'] or plan['attrs'] or plan['list_remove']
                or plan['last_job'])


def describe(plan):
    out = []
    if plan['sheets']:
        out.append('%d Rohteil(e): %s' % (len(plan['sheets']), ', '.join(s['name'] for s in plan['sheets'][:8])))
    if plan['setups']:
        out.append('%d CAM-Setup(s): %s' % (len(plan['setups']), ', '.join(u['name'] for u in plan['setups'][:8])))
    if plan['copies']:
        out.append('%d Baugruppen-Kopie(n)' % len(plan['copies']))
    if plan['attrs'] or plan['list_remove']:
        out.append('Projektdaten (%s)' % ', '.join(plan['files_for'] or plan['list_remove']))
    return out


# ---------------------------------------------------------------- Ausführen
def setup_key(s):
    """Stabile Kennung eines Setups (operationId, sonst entityToken, sonst Name + Verknüpfung)."""
    for f in (lambda: s.operationId, lambda: s.entityToken):
        v = _g(f)
        if isinstance(v, (int, str)) and v != '':
            return 'k:%s' % v
    return 'n:%s|%s' % (_g(lambda: s.name, ''), attr(s, LINK_ATTR) or '')


def _generating(s):
    """Wird gerade ein Werkzeugweg dieses Setups berechnet?"""
    if _g(lambda: s.isGenerating, False) is True:
        return True
    for op in items(_g(lambda: s.allOperations)):
        if _g(lambda: op.isGenerating, False) is True:
            return True
    return False


def delete_setups(plan, log, cam=None):
    """Phase 1 (AUSSERHALB eines Geometrie-Befehls): CAM-Setups löschen.
    2.0.4 (Review P1.1): erst nur Kennungen sammeln und ALLE Python-Verweise auf Setups/Operationen fallen lassen,
    dann je Kennung (rückwärts) frisch aus cam.setups holen, isValid prüfen, deleteMe. Läuft eine
    Werkzeugwegberechnung, wird NICHTS gelöscht (Rückgabe -1). Fehlt ein Setup schon -> übersprungen (idempotent)."""
    keys = []
    for u in plan['setups']:
        s = u.get('setup')
        if s is not None and is_valid(s):
            if _generating(s):
                log.append('Werkzeugweg von „%s“ wird gerade berechnet – bitte warten und erneut löschen' % u['name'])
                return -1
            keys.append((setup_key(s), u['name']))
        u['setup'] = None               # Verweis lösen
    if cam is None:
        return 0
    n = 0
    for key, nm in reversed(keys):
        fresh = next((x for x in items(_g(lambda: cam.setups)) if setup_key(x) == key), None)
        if fresh is None or not is_valid(fresh):
            continue
        try:
            fresh.deleteMe()
            n += 1
        except Exception as e:
            log.append('Setup %s: %s' % (nm, e))
        fresh = None
    return n


def _model_path(m):
    p = _g(lambda: m.fullPathName)
    if isinstance(p, str) and p:
        return p
    ctx = _g(lambda: m.assemblyContext)
    cp = _g(lambda: ctx.fullPathName, '') if ctx is not None else ''
    return ('%s+%s' % (cp, _g(lambda: m.name, ''))) if cp else (_g(lambda: m.name, '') or '')


def _under(path, sheet_names):
    return any(path == n or path.startswith(n + '+') for n in sheet_names)


def release_user_setups(cam, sheet_names, log, make_coll=None):
    """Review P1.2: Setups OHNE LazyNest-Verknüpfung, die Modelle aus zu löschenden Rohteilen enthalten, bleiben
    erhalten. Entfernt werden NUR diese Modelle und LazySetup-Bearbeitungen (Attribut Finalschnitt/op) für diese
    Rohteile – vom Anwender gewählte Körper und eigene Bearbeitungen bleiben unangetastet. Rückgabe Anzahl Setups."""
    names = set(n for n in sheet_names if n)
    n = 0
    if cam is None or not names:
        return 0
    for s in items(_g(lambda: cam.setups)):
        if jattr(s, LINK_ATTR) is not None:
            continue
        models = items(_g(lambda: s.models))
        keep = [m for m in models if not _under(_model_path(m), names)]
        ops = [op for op in items(_g(lambda: s.allOperations))
               if isinstance(jattr(op, 'op', 'Finalschnitt'), dict)
               and (jattr(op, 'op', 'Finalschnitt').get('sheet') or '') in names]
        if len(keep) == len(models) and not ops:
            continue
        for op in ops:
            try:
                op.deleteMe()
            except Exception as e:
                log.append('Bearbeitung: %s' % e)
        if len(keep) != len(models):
            try:
                s.models = make_coll(keep) if make_coll else keep
            except Exception as e:
                log.append('Setup %s: Modelle nicht gelöst (%s)' % (_g(lambda: s.name, '?'), e))
        n += 1
    return n


def _del_attr(obj, name, group=GROUP):
    a = _g(lambda: obj.attributes.itemByName(group, name))
    if a is None:
        return False
    try:
        a.deleteMe()
        return True
    except Exception:
        try:
            obj.attributes.add(group, name, '')
            return True
        except Exception:
            return False


def delete_rest(design, plan, log, delete_sheet=None):
    """Phase 2 (im Befehl): Rohteile, Kopien, Attribute, Marker. delete_sheet(occ) -> bool (Fixierungen lösen)."""
    rep = {'sheets': 0, 'copies': 0, 'attrs': 0, 'markers': 0, 'last_job': False, 'copy_root': False}
    for s in plan['sheets']:
        occ = s['occ']
        if not is_valid(occ):
            continue
        try:
            ok = delete_sheet(occ) if delete_sheet else (occ.deleteMe() is not False)
        except Exception as e:
            ok = False
            log.append('Rohteil %s: %s' % (s['name'], e))
        rep['sheets'] += 1 if ok else 0
    for cp in plan['copies']:
        if not is_valid(cp['occ']):
            continue
        try:
            cp['occ'].deleteMe()
            rep['copies'] += 1
        except Exception as e:
            log.append('Kopie %s: %s' % (cp['name'], e))
    if plan.get('remove_copy_root'):
        ro = _copy_root(design)
        if ro is not None and not items(_g(lambda: ro.component.occurrences)):
            try:
                ro.deleteMe()
                rep['copy_root'] = True
            except Exception as e:
                log.append('LazyNest Kopien: %s' % e)
    for nm in plan['attrs']:
        rep['attrs'] += 1 if _del_attr(design, nm) else 0
    if plan['list_remove']:
        ids = [i for i in project_ids(design) if i not in plan['list_remove']]
        try:
            design.attributes.add(GROUP, 'projects', json.dumps(ids))
        except Exception as e:
            log.append('Projektliste: %s' % e)
    if plan.get('last_job'):
        rep['last_job'] = _del_attr(design, 'lastJob')
    if plan.get('markers'):
        for g, nm in MARKERS:
            rep['markers'] += 1 if _del_attr(design, nm, g) else 0
    pid = plan.get('project_id')
    if pid and plan.get('keep_history'):
        set_kept(design, add=[pid], log=log)          # Waisen-Prüfung fragt dafür nicht nach
    elif pid and pid in kept_ids(design) and (plan.get('attrs') or plan.get('list_remove')):
        set_kept(design, remove=[pid], log=log)       # Historie jetzt vollständig entfernt
    elif plan.get('dead_projects'):
        set_kept(design, remove=plan['dead_projects'], log=log)
    return rep


TS_SEP = '__'


def move_project_files(folder, project_ids_, log, stamp=None):
    """Projektdateien der gelöschten Projekte nach <folder>/geloescht verschieben (wiederherstellbar), Name immer
    mit Zeitstempel davor (<JJJJMMTT_hhmmss>__<Name>) -> keine Kollisionen. Nur nach ERFOLGREICHEM Löschen."""
    moved = []
    want = set(p for p in project_ids_ or [] if p)
    if not want or not folder or not os.path.isdir(folder):
        return moved
    dst = os.path.join(folder, 'geloescht')
    stamp = stamp or time.strftime('%Y%m%d_%H%M%S')
    for fn in sorted(os.listdir(folder)):
        path = os.path.join(folder, fn)
        if not fn.lower().endswith('.json') or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding='utf-8') as f:
                pid = (json.load(f) or {}).get('project_id')
        except Exception:
            continue
        if pid not in want:
            continue
        try:
            os.makedirs(dst, exist_ok=True)
            target = os.path.join(dst, '%s%s%s' % (stamp, TS_SEP, fn))
            k = 1
            while os.path.exists(target):
                k += 1
                target = os.path.join(dst, '%s_%d%s%s' % (stamp, k, TS_SEP, fn))
            shutil.move(path, target)
            moved.append(target)
        except Exception as e:
            log.append('Datei %s: %s' % (fn, e))
    return moved


def restore_project_files(folder, live_ids, log):
    """Review P1.3: nach Rückgängig (Strg+Z) sind Projekt-Attribute wieder da -> verschobene Dateien dieser
    Projekte zurückholen (Zeitstempel entfernen; vorhandene Datei wird nie überschrieben)."""
    back = []
    src = os.path.join(folder or '', 'geloescht')
    live = set(live_ids or [])
    if not live or not os.path.isdir(src):
        return back
    for fn in sorted(os.listdir(src)):
        path = os.path.join(src, fn)
        if not fn.lower().endswith('.json') or TS_SEP not in fn:
            continue
        try:
            with open(path, encoding='utf-8') as f:
                pid = (json.load(f) or {}).get('project_id')
        except Exception:
            continue
        if pid not in live:
            continue
        target = os.path.join(folder, fn.split(TS_SEP, 1)[1])
        if os.path.exists(target):
            continue
        try:
            shutil.move(path, target)
            back.append(target)
        except Exception as e:
            log.append('Datei %s: %s' % (fn, e))
    return back


def plan_done(design, plan):
    """Phase 2 vollständig? (alle Rohteile/Kopien weg, Attribute entfernt) – Grundlage fürs Verschieben der Dateien
    und für „erneut ausführen“ bei Teilfehlern."""
    if any(is_valid(s['occ']) for s in plan['sheets']):
        return False
    if any(attr(design, nm) for nm in plan['attrs']):          # '' = per Rückfall geleert
        return False
    return not any(i in project_ids(design) for i in plan['list_remove'])
