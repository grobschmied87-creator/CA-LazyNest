"""ContourNest – Nesting-Projekte: speichern, laden, Teile wiederfinden.

Ein Projekt beschreibt ein übernommenes Nesting vollständig (Einstellungen, Teile mit Konturen, Lage jedes
Stücks, Fixierungen/Wenden/Ausschlüsse, Herkunft der Teile) und liegt
  1) als Attribut  ContourNest/project_<id>  an der Konstruktion (reist mit der .f3d), Index in  ContourNest/projects
  2) als Datei     <Add-In>/projects/nest_<Konstruktion>_<Zeit>.json  (Sicherung, importierbar).
Teile werden robust wiedergefunden: Komponenten-ID -> Token des Körpers -> Token der Komponente -> Name."""
import json
import os
import re
import time
import uuid

import adsk.core
import adsk.fusion

from . import ln_geom as G

FORMAT = 'ContourNest-Projekt'
FORMAT_VERSION = 1
VERSION = ''          # ContourNest-Version, von ContourNest.py gesetzt
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(HERE, 'projects')
KEEP_HISTORY = 20


def new_id():
    return uuid.uuid4().hex[:10]


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _tok(obj):
    try:
        v = obj.entityToken
        return v if isinstance(v, str) else ''
    except Exception:
        return ''


def _sfe(fn, default=''):
    try:
        v = fn()
        return default if v is None else v
    except Exception:
        return default


def _str(fn):
    v = _sfe(fn)
    return v if isinstance(v, str) else ''


def source_record(pid, info):
    """Herkunft eines Teiltyps (alles, was zum Wiederfinden nötig ist)."""
    comp = info.get('comp')
    body = None
    try:
        bodies = [b for b in comp.bRepBodies if b.isSolid] if comp else []
        body = bodies[0] if len(bodies) == 1 else None
    except Exception:
        body = None
    src = info.get('src') or []
    return {
        'part_id': pid,
        'component_id': _str(lambda: comp.id) if comp else (pid[2:] if pid.startswith('C:') else ''),
        'component_name': _str(lambda: comp.name) if comp else info.get('name', ''),
        'component_token': _tok(comp) if comp else info.get('token', ''),
        'body_name': _str(lambda: body.name) if body else '',
        'body_token': _tok(body) if body else '',
        'source_paths': [s.get('assemblyContext') for s in src if isinstance(s, dict)
                         and isinstance(s.get('assemblyContext'), str)],
        'name': info.get('name', ''),
        'frame': [list(v) for v in info['frame']] if info.get('frame') else None,
    }


def build_project(design, ctx, result, opts, project_id=None, prev=None, app=None):
    """Projekt-JSON aus Auftrag (ctx['job']), Paletten-Ergebnis und Optionen."""
    job = json.loads(json.dumps(ctx['job']))
    job.pop('initialLayout', None)
    for k in ('length', 'width', 'thickness', 'frame', 'spacing', 'grain'):    # Schnelleinstellungen der Palette
        if k in (result.get('sheet') or {}):
            job['sheet'][k] = result['sheet'][k]
    if result.get('material') is not None:
        job['material'] = result['material']
    job.pop('stock', None)
    st = result.get('settings') or {}
    if st.get('rotStep') is not None:
        job['rotStep'] = st['rotStep']
    if st.get('vacuumWeight') is not None:
        job['vacuumWeight'] = st['vacuumWeight']
    overrides = json.loads(json.dumps(result.get('overrides') or job.get('overrides') or {}))
    job['overrides'] = overrides
    placements = []
    sheets = []
    # 1.10.0: Platzierungen im Maschinensystem (wie die Palette); je Rohteil die Definition (Abschnitt, längs/quer,
    # Nullpunkt). Leere Blätter (Abschnitte ohne Teile) zählen nicht als Rohteil, ihre Definition bleibt in
    # sheet_defs (Reihenfolge = Füllreihenfolge der Palette; Fixierungen beziehen sich auf diese Reihenfolge).
    dz = result.get('defaultZero') or (opts or {}).get('cam_origin')
    sheet_defs = []
    no = 0
    for S in result.get('sheets', []):
        d = dict(S.get('def') or {'kind': 'standard', 'orient': 'laengs'})
        if not d.get('id'):
            d['id'] = 'P%d' % (len(sheet_defs) + 1)
        gm = G.resolve_sheet(d, job['sheet'], job.get('material', ''), dz)
        d.setdefault('zero', gm['zero'])
        sheet_defs.append(d)
        if not S.get('parts'):
            continue
        no += 1
        zp = G.zero_point(gm)
        sheets.append({'index': no, 'utilisation': S.get('utilisation', 0), 'def': d, 'def_id': d.get('id') or '',
                       'kind': gm['kind'], 'orient': gm['orient'], 'zero': gm['zero'],
                       'zero_box_point': G.zero_box_point(gm['zero']),
                       'length': gm['length'], 'width': gm['width'], 'thickness': gm['thickness'], 'frame': gm['frame'],
                       'grain': gm['grain'], 'material': gm['material'],
                       'machine_length': gm['W'], 'machine_width': gm['H'], 'grain_machine': gm['grain_axis'],
                       'zero_machine': [zp['x'], zp['y'], zp['z']], 'zero_local': [zp['u'], zp['v'], zp['z']],
                       'label': G.sheet_label(gm, no)})
        for p in S.get('parts', []):
            placements.append({'sheet': no, 'sheet_def': d.get('id') or '', 'key': p['key'], 'id': p['id'],
                               'name': p.get('name', ''),
                               'copy': p.get('copy', 0), 'x': p['x'], 'y': p['y'], 'rot': p['rot'],
                               'flip': bool(p.get('flip')), 'pinned': bool(p.get('pinned'))})
    version = (prev.get('version', 0) + 1) if prev else 1
    hist = list(prev.get('history', [])) if prev else []
    if prev:
        hist.append({'version': prev.get('version'), 'saved': prev.get('saved'), 'sheets': len(prev.get('sheets', [])),
                     'parts': len(prev.get('placements', [])), 'placements': prev.get('placements', []),
                     'sheet': prev.get('job', {}).get('sheet')})
        hist = hist[-KEEP_HISTORY:]
    doc = _sfe(lambda: (app or adsk.core.Application.get()).activeDocument.name, '')
    return {
        'format': FORMAT, 'format_version': FORMAT_VERSION, 'contournest_version': VERSION, 'lazynest_version': VERSION, 'created_by': 'CA LazyNest',
        'project_id': project_id or (prev or {}).get('project_id') or new_id(),
        'version': version,
        'created': (prev or {}).get('created') or _now(), 'saved': _now(),
        'document': doc if isinstance(doc, str) else '',
        'job': job,
        'opts': {k: v for k, v in (opts or {}).items() if k in ('stock_body', 'gap', 'fix', 'replace', 'cam', 'cam_origin',
                                                                   'cam_stock', 'cam_prog')},
        'sources': {pid: source_record(pid, info) for pid, info in ctx['parts'].items()},
        'placements': placements, 'sheets': sheets, 'sheet_defs': sheet_defs, 'coords': 'maschine',
        'default_zero': dz or 'lu_unten',
        'unplaced': result.get('unplaced') or [],
        'excluded': sorted(k for k, v in overrides.items() if v.get('excluded')),
        'utilisation': result.get('utilisation', 0),
        'history': hist,
    }


# ---------------------------------------------------------------- speichern / laden
def _attr(design, name):
    try:
        a = design.attributes.itemByName(G.GROUP, name)
        return a.value if a else None
    except Exception:
        return None


def list_ids(design):
    try:
        return json.loads(_attr(design, 'projects') or '[]')
    except Exception:
        return []


def _safe_name(s):
    return re.sub(r'[^A-Za-z0-9ÄÖÜäöüß_-]+', '_', s or 'Konstruktion').strip('_')[:60] or 'Konstruktion'


def save(design, project, folder=None, write_file=True):
    """Attribut an der Konstruktion + Sicherungsdatei. Rückgabe: Dateipfad (oder '')."""
    pid = project['project_id']
    data = json.dumps(project, default=str)
    design.attributes.add(G.GROUP, 'project_' + pid, data)
    ids = [i for i in list_ids(design) if i != pid] + [pid]
    design.attributes.add(G.GROUP, 'projects', json.dumps(ids))
    if not write_file:
        return ''
    folder = folder or PROJECT_DIR
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, 'nest_%s_%s_v%d.json' % (_safe_name(project.get('document')),
                                                            time.strftime('%Y%m%d_%H%M%S'), project['version']))
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(project, f, ensure_ascii=False, indent=1, default=str)
        return path
    except Exception:
        return ''


def load(design, pid):
    v = _attr(design, 'project_' + pid)
    return json.loads(v) if v else None


def all_projects(design):
    """Alle Projekte der Konstruktion, neuestes zuerst."""
    out = []
    for pid in list_ids(design):
        try:
            p = load(design, pid)
            if p:
                out.append(p)
        except Exception:
            continue
    out.sort(key=lambda p: p.get('saved', ''), reverse=True)
    return out


def label(p):
    return '%s v%d – %s – %d Rohteil(e), %d Teile' % (p.get('project_id'), p.get('version', 1),
                                                      p.get('saved', '')[5:16], len(p.get('sheets', [])),
                                                      len(p.get('placements', [])))


def read_file(path):
    with open(path, encoding='utf-8') as f:
        p = json.load(f)
    if p.get('format') != FORMAT or not p.get('project_id') or not p.get('job'):
        raise ValueError('Keine ContourNest-Projektdatei')
    return p


# ---------------------------------------------------------------- Teile wiederfinden
def _components(design):
    try:
        coll = design.allComponents
        return [coll.item(i) for i in range(coll.count)]
    except Exception:
        try:
            return list(design.allComponents)
        except Exception:
            return []


def _by_token(design, tok):
    if not tok:
        return None
    try:
        ents = design.findEntityByToken(tok)
        return ents[0] if ents else None
    except Exception:
        return None


def find_component(design, src, comps=None):
    """-> (component, wie gefunden) oder (None, Grund)."""
    comps = comps if comps is not None else _components(design)
    cid = src.get('component_id')
    if cid:
        for c in comps:
            if _sfe(lambda: c.id) == cid:
                return c, 'Komponenten-ID'
    b = _by_token(design, src.get('body_token'))
    if b is not None:
        try:
            native = b.nativeObject or b
            return native.parentComponent, 'Körper-Token'
        except Exception:
            pass
    c = _by_token(design, src.get('component_token'))
    if c is not None:
        try:
            adsk.fusion.Component.cast(c)
            if _sfe(lambda: c.bRepBodies) != '':
                return c, 'Komponenten-Token'
        except Exception:
            pass
    name = src.get('component_name')
    cand = [c for c in comps if _sfe(lambda: c.name) == name] if name else []
    if len(cand) > 1 and src.get('body_name'):
        cand2 = [c for c in cand if any(_sfe(lambda: b.name) == src['body_name'] for b in c.bRepBodies)]
        cand = cand2 or cand
    if cand:
        return cand[0], 'Name' + (' (mehrdeutig: %d)' % len(cand) if len(cand) > 1 else '')
    return None, 'nicht gefunden (ID, Token, Name „%s“)' % name


def resolve(design, project):
    """Teile des Projekts in der Konstruktion wiederfinden.
    -> (ctx_parts, report) – report: {'found': {pid: wie}, 'missing': [Name], 'renamed': [(alt, neu)]}"""
    comps = _components(design)
    ctx_parts, rep = {}, {'found': {}, 'missing': [], 'renamed': []}
    for pid, src in project.get('sources', {}).items():
        comp, how = find_component(design, src, comps)
        if comp is None:
            rep['missing'].append(src.get('name') or src.get('component_name') or pid)
            continue
        rep['found'][pid] = how
        nm = _sfe(lambda: comp.name)
        if nm and nm != src.get('component_name'):
            rep['renamed'].append((src.get('component_name'), nm))
        ctx_parts[pid] = {'comp': comp, 'frame': tuple(tuple(v) for v in src['frame']), 'name': src.get('name') or nm,
                          'token': _tok(comp), 'src': [{'resolved_by': how, 'component': nm}],
                          'diag': {'resolved_by': how}}
    return ctx_parts, rep


def report_text(rep):
    t = []
    if rep['missing']:
        t.append('Nicht gefunden (werden weggelassen): ' + ', '.join(rep['missing']))
    if rep['renamed']:
        t.append('Umbenannt: ' + ', '.join('%s → %s' % r for r in rep['renamed']))
    byname = [pid for pid, how in rep['found'].items() if how.startswith('Name')]
    if byname:
        t.append('%d Teil(e) nur über den Namen gefunden – bitte prüfen.' % len(byname))
    return '\n'.join(t)


def project_defs(project):
    """Blatt-Definitionen eines Projekts (Palette-Reihenfolge); ältere Projekte: [] (= Standard, längs)."""
    out = []
    for d in project.get('sheet_defs') or []:
        if isinstance(d, dict):
            d = dict(d)
            d.setdefault('id', 'P%d' % (len(out) + 1))
            out.append(d)
    return out


def project_sheet(project, no):
    """Rohteil no (1-basiert) eines Projekts: aufgelöste Definition (Maß/Ausrichtung/Nullpunkt, mm)."""
    job = project.get('job') or {}
    rec = next((s for s in project.get('sheets') or [] if int(s.get('index', 0)) == int(no)), None) or {}
    return G.resolve_sheet(rec.get('def'), job.get('sheet') or {}, job.get('material', ''),
                           project.get('default_zero') or (project.get('opts') or {}).get('cam_origin'))


def job_from_project(project, ctx_parts, job_id, keep_layout=True):
    """Palettenauftrag aus einem Projekt; keep_layout: vorhandene Anordnung exakt übernehmen."""
    job = json.loads(json.dumps(project['job']))
    job['jobId'] = job_id
    job['parts'] = [p for p in job['parts'] if p['id'] in ctx_parts]
    ids = set(p['id'] for p in job['parts'])
    job['overrides'] = {k: v for k, v in (project['job'].get('overrides') or {}).items()
                        if k.rsplit('#', 1)[0] in ids}
    defs = project_defs(project)
    if keep_layout:
        nsh = max([p['sheet'] for p in project['placements']] or [0])
        if project.get('sheet_defs'):          # 1.10.0: Blätter in Palette-Reihenfolge (inkl. leerer Abschnitte)
            by_def = {}
            for p in project['placements']:
                by_def.setdefault(p.get('sheet_def') or ('#%d' % p['sheet']), []).append(p)
            init = [[dict(p) for p in by_def.get(d.get('id') or '', []) if p['id'] in ids] for d in defs]
            job['initialLayout'] = {'sheets': init, 'defs': defs}
            nsh = len(init)
        else:                                  # ältere Projekte: Standardformat, längs
            job['initialLayout'] = {'sheets': [[dict(p) for p in project['placements']
                                                if p['sheet'] == i + 1 and p['id'] in ids] for i in range(nsh)]}
        job['maxSheets'] = max(int(job.get('maxSheets', 1)), nsh)
    job['sheets'] = defs
    job['defaultZero'] = project.get('default_zero') or (project.get('opts') or {}).get('cam_origin') or 'lu_unten'
    job['project'] = {'id': project['project_id'], 'version': project.get('version', 1)}
    return job
