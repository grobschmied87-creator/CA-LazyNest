# -*- coding: utf-8 -*-
"""CA LazyNest (vormals ContourNest) – Fusion-spezifische Teile: Teile analysieren, Parameter, Ergebnis aufbauen, löschen."""
import json
import math

import adsk.core
import adsk.fusion

try:
    from . import ln_geom as G
    from . import ln_assembly as A
    from . import ln_log as _L
    from . import ln_timing as TM
except ImportError:          # Test/Direktimport
    import ln_geom as G
    import ln_assembly as A
    import ln_log as _L
    import ln_timing as TM


def _cp(msg, *a):
    """Sicherheitsmodus: Kontrollpunkt (geschrieben + fsync) VOR einem riskanten API-Aufruf."""
    try:
        _L.checkpoint(msg, *a)
    except Exception:
        pass

MM = G.MM


def _t(p):
    return (p.x, p.y, p.z)


# ---------------------------------------------------------------- Konstruktionstyp
def is_parametric(design):
    """True nur bei Konstruktion MIT Verlauf. Direktmodell: keine userParameters/timeline/Joint-Erfassung."""
    try:
        return design.designType == adsk.fusion.DesignTypes.ParametricDesignType
    except Exception:
        return False


# ---------------------------------------------------------------- Benutzerparameter
def read_params(design):
    """Werte der nest_* Parameter in Anzeigeeinheiten (mm, Grad, ohne Einheit).
    Im Direktmodell gibt es keine Benutzerparameter -> Standardwerte."""
    out = {}
    param = is_parametric(design)
    for name, (unit, default, _c) in G.PARAMS.items():
        val = default
        if not param:
            out[name] = val
            continue
        try:
            p = design.userParameters.itemByName(name)
            if p:
                if unit == 'mm':
                    val = p.value / MM
                elif unit == 'deg':
                    val = math.degrees(p.value)
                else:
                    val = p.value
        except Exception:
            pass
        out[name] = val
    return out


def write_params(design, values):
    """values: name -> Wert in Anzeigeeinheit. Bestehende Parameter werden aktualisiert.
    Im Direktmodell (ohne Zeitachse) gibt es keine Benutzerparameter -> still überspringen,
    die Einstellungen liegen dann nur in den Design-Attributen."""
    try:
        if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
            return
        ups = design.userParameters
    except Exception:
        return
    for name, val in values.items():
        unit, _d, comment = G.PARAMS[name]
        expr = ('%g %s' % (val, unit)).strip() if unit else '%g' % val
        p = ups.itemByName(name)
        try:
            if p:
                if p.expression != expr:
                    p.expression = expr
            else:
                ups.add(name, adsk.core.ValueInput.createByString(expr), unit, comment)
        except Exception:
            pass   # Parameter optional – Nesting funktioniert auch ohne


def read_settings_attr(design):
    try:
        a = design.attributes.itemByName(G.GROUP, 'settings')
        return json.loads(a.value) if a else {}
    except Exception:
        return {}


def write_attr(entity, name, value):
    if not isinstance(value, str):
        value = json.dumps(value)
    try:
        entity.attributes.add(G.GROUP, name, value)
    except Exception:
        pass


# ---------------------------------------------------------------- Teil analysieren
def _comp_key(comp):
    for attr in ('id', 'entityToken'):
        try:
            v = getattr(comp, attr)
            if v:
                return 'C:' + v
        except Exception:
            pass
    return 'C:' + comp.name


LINKED_MSG = 'Teil liegt in verknüpfter Komponente %s – Baugruppenmodus aktivieren oder Verknüpfung lösen'


def resolve_entity(ent, design, assembly=False):
    """-> dict(comp, body, key, convert_reason, error, name, linked).
    2.0.0: Teile in verknüpften (referenzierten) Komponenten werden ohne Baugruppenmodus abgewiesen – Fusion kann
    sie nicht in die lokale Rohteil-Komponente einfügen (addExistingComponent-Fehler, KMD-PC-03)."""
    r = _resolve_entity(ent, design)
    lk = A.linked_parent(ent)
    if lk is not None:
        nm = A._g(lambda: lk.name, '?')
        r['linked'] = nm
        r['link_info'] = A.link_info(lk)
        if not assembly and not r.get('error'):
            r['error'] = LINKED_MSG % nm
    return r


def _solid_rank(b):
    """Größe eines Körpers für Mehrkörper-Auswahl (Volumen, sonst Fläche, sonst Face-Anzahl)."""
    for attr in ('volume', 'area'):
        try:
            v = float(getattr(b, attr))
            if v:
                return v
        except Exception:
            pass
    try:
        return float(b.faces.count)
    except Exception:
        return 0.0


def _pick_largest_solid(bodies):
    if not bodies:
        return None
    if len(bodies) == 1:
        return bodies[0]
    return max(bodies, key=_solid_rank)


def _resolve_entity(ent, design):
    r = {'comp': None, 'body': None, 'key': None, 'convert_reason': '', 'error': '', 'name': '?'}
    occ = adsk.fusion.Occurrence.cast(ent)
    if occ:
        comp = occ.component
        r['name'] = comp.name
        bodies = [b for b in comp.bRepBodies if b.isSolid]
        child_bodies = 0
        try:
            for o in comp.allOccurrences:
                child_bodies += o.component.bRepBodies.count
        except Exception:
            pass
        if child_bodies:
            r['error'] = 'Komponente hat Unterkomponenten mit Körpern – bitte einzelne Teilkomponente wählen'
        elif not bodies:
            r['error'] = 'Komponente enthält 0 Volumenkörper (erwartet: mindestens 1)'
        else:
            body = _pick_largest_solid(bodies)
            if len(bodies) > 1:
                r['convert_reason'] = 'Komponente „%s“ enthält %d Körper' % (comp.name, len(bodies))
                r['multibody'] = len(bodies)
            r.update(comp=comp, body=body, key=_comp_key(comp))
        return r
    body = adsk.fusion.BRepBody.cast(ent)
    if not body:
        r['error'] = 'Auswahl ist weder Körper noch Komponente'
        return r
    native = body.nativeObject or body
    comp = native.parentComponent
    r['name'] = native.name
    r['body'] = native
    if not native.isSolid:
        r['error'] = 'Körper ist kein Volumenkörper'
        return r
    if comp == design.rootComponent:
        r['convert_reason'] = 'Körper liegt in der Wurzelkomponente'
        r['needs_convert'] = True
        r['key'] = 'B:' + (native.entityToken or native.name)
        return r
    solids = [b for b in comp.bRepBodies if b.isSolid]
    r['comp'] = comp
    r['key'] = _comp_key(comp)
    r['name'] = comp.name
    if len(solids) > 1:
        r['convert_reason'] = 'Komponente „%s“ enthält %d Körper' % (comp.name, len(solids))
        r['multibody'] = len(solids)
    return r


def largest_planar_face(body):
    best, area = None, -1.0
    for f in body.faces:
        try:
            if f.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType and f.area > area:
                best, area = f, f.area
        except Exception:
            continue
    return best


def face_frame(face):
    """(o, e1, e2, n) in cm, n = Normale nach außen, e1 x e2 = n."""
    o = _t(face.pointOnFace)
    ok, nv = face.evaluator.getNormalAtPoint(face.pointOnFace)
    if not ok:
        raise RuntimeError('Flächennormale nicht ermittelbar')
    n = G.unit(_t(nv))
    try:
        u = _t(face.geometry.uDirection)
        e1 = G.unit(G.sub(u, G.mul(n, G.dot(u, n))))
    except Exception:
        e1 = G.any_perp(n)
    e2 = G.cross(n, e1)
    return o, e1, e2, n


def loop_polygon(loop, frame, tol_cm):
    """Liefert (punkte_2d_mm, gerade_segmente_2d_mm) eines BRepLoop."""
    o, e1, e2, _n = frame

    def proj(p):
        d = G.sub(_t(p), o)
        return (G.dot(d, e1) / MM, G.dot(d, e2) / MM)
    pts, segs = [], []
    for ce in loop.coEdges:
        edge = ce.edge
        ev = edge.evaluator
        ok, p0, p1 = ev.getParameterExtents()
        if not ok:
            raise RuntimeError('Parameterbereich einer Kante nicht ermittelbar')
        ok, strokes = ev.getStrokes(p0, p1, tol_cm)
        if not ok or not strokes:
            raise RuntimeError('Kante konnte nicht tesselliert werden')
        q = [proj(p) for p in strokes]
        if ce.isOpposedToEdge:
            q.reverse()
        try:
            if edge.geometry.curveType == adsk.core.Curve3DTypes.Line3DCurveType:
                segs.append((q[0], q[-1]))
        except Exception:
            pass
        if pts and abs(pts[-1][0] - q[0][0]) < 1e-3 and abs(pts[-1][1] - q[0][1]) < 1e-3:
            q = q[1:]
        pts.extend(q)
    return G.clean_ring(pts), segs


def _vertex_heights(body, o, n):
    ws = []
    for v in body.vertices:
        ws.append(G.dot(G.sub(_t(v.geometry), o), n))
    if len(ws) < 2:
        try:
            bb = body.boundingBox
            ws = [G.dot(G.sub(_t(bb.minPoint), o), n), G.dot(G.sub(_t(bb.maxPoint), o), n)]
        except Exception:
            ws = [0.0]
    return ws


def analyze_part(res, tol_mm=0.1):
    """Ergänzt das resolve-Ergebnis um Geometrie: outer/holes (mm, lokal), grain, Dicke, Material.

    Alles im Koordinatensystem der KOMPONENTE des nativen Körpers. res['diag'] enthält Prüfdaten."""
    body = res['body']
    diag = res.setdefault('diag', {})
    face = largest_planar_face(body)
    if not face:
        res['error'] = res['error'] or 'keine ebene Fläche gefunden'
        return res
    frame = face_frame(face)
    o, e1, e2, n = frame
    # Körper muss auf der Rückseite der Ablagefläche liegen (w <= 0). Sonst zeigt die Normale nach
    # innen -> Rahmen um 180° um e1 drehen, damit das Teil IN der Platte und nicht darüber liegt.
    ws = _vertex_heights(body, o, n)
    inverted = bool(ws) and max(ws) > 1e-4 and min(ws) > -1e-4
    if inverted:
        frame = (o, e1, G.mul(e2, -1), G.mul(n, -1))
        ws = [-w for w in ws]
    outer, holes, segs = None, [], []
    for loop in face.loops:
        ring, s = loop_polygon(loop, frame, tol_mm * MM)
        if len(ring) < 3:
            continue
        if loop.isOuter:
            outer, segs = G.orient_ring(ring, True), s
        else:
            holes.append(G.orient_ring(ring, False))
    if not outer:
        res['error'] = 'Außenkontur nicht gefunden'
        return res
    ang, src = G.grain_angle(outer, segs)
    thick = (max(ws) - min(ws)) / MM if ws else 0.0
    # Innenkonturen: nur DURCHGEHENDE Löcher zählen für Kollision/Teil-in-Teil; Sacktaschen -> features
    features, blind = [], 0
    try:
        through = []
        for h in holes:
            if _hole_is_through(body, frame, face, h, thick):
                through.append(h)
            else:
                blind += 1
        holes = through
        features = extract_features(body, frame, thick, tol_mm)
    except Exception as e:
        diag['features_error'] = str(e)
    mat = ''
    try:
        mat = body.material.name if body.material else ''
    except Exception:
        pass
    try:
        diag.update(face_area_cm2=face.area, normal_inverted=inverted,
                    frame_cm={'origin': list(frame[0]), 'x_axis': list(frame[1]), 'y_axis': list(frame[2]),
                              'normal': list(frame[3])},
                    heights_mm=[min(ws) / MM, max(ws) / MM] if ws else None,
                    contour_bbox_mm=list(G.bbox2d(outer)), contour_points=len(outer), holes=len(holes),
                    blind_openings=blind, features=feature_summary(features))
        bb = body.boundingBox
        diag['body_bbox_component_mm'] = {'min': [bb.minPoint.x / MM, bb.minPoint.y / MM, bb.minPoint.z / MM],
                                          'max': [bb.maxPoint.x / MM, bb.maxPoint.y / MM, bb.maxPoint.z / MM]}
    except Exception as e:
        diag['diag_error'] = str(e)
    res.update(frame=frame, outer=outer, holes=holes, features=features, grain_angle=ang, grain_src=src,
               thickness=thick, material=mat,
               area=abs(G.area2d(outer)) - sum(abs(G.area2d(h)) for h in holes))
    return res


# ---------------------------------------------------------------- Bearbeitungen (Taschen, Nuten, Sacklöcher)
EPS_W = 0.05          # mm: Toleranz für „liegt auf Ober-/Unterseite“


def _uvw(frame, p):
    o, e1, e2, n = frame
    d = G.sub(p, o)
    return (G.dot(d, e1) / MM, G.dot(d, e2) / MM, G.dot(d, n) / MM)


def _xyz(frame, u, v, w):
    o, e1, e2, n = frame
    return G.add(o, G.add(G.mul(e1, u * MM), G.add(G.mul(e2, v * MM), G.mul(n, w * MM))))


def _contains(body, frame, u, v, w):
    """True = Punkt im Material, False = außerhalb, None = unbekannt (API fehlt/Grenzfall)."""
    try:
        r = body.pointContainment(adsk.core.Point3D.create(*_xyz(frame, u, v, w)))
        PC = adsk.fusion.PointContainment
        if r == PC.PointInsidePointContainment:
            return True
        if r == PC.PointOutsidePointContainment:
            return False
    except Exception:
        pass
    return None


def _inner_point(ring):
    """Ein Punkt sicher im Inneren eines Polygons (Mitte des ersten Schnittintervalls einer Mittellinie)."""
    ys = sorted(set(p[1] for p in ring))
    if len(ys) < 2:
        return ring[0]
    for frac in (0.5, 0.37, 0.63, 0.21, 0.79):
        y = ys[0] + (ys[-1] - ys[0]) * frac
        xs = []
        for i in range(len(ring)):
            (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % len(ring)]
            if (y1 <= y < y2) or (y2 <= y < y1):
                xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        xs.sort()
        if len(xs) >= 2 and xs[1] - xs[0] > 1e-6:
            return ((xs[0] + xs[1]) / 2, y)
    return ring[0]


def _bbox_overlap(a, b, tol=0.5):
    return not (a[2] < b[0] - tol or b[2] < a[0] - tol or a[3] < b[1] - tol or b[3] < a[1] - tol)


def _hole_is_through(body, frame, face, ring, thick):
    """Innenkontur der Ablagefläche: durchgehend? 1) Punkt-im-Körper-Test 0,1 mm vor der Gegenseite,
    2) Rückfall: Gegenfläche (w = −Dicke) hat eine überlappende Innenkontur."""
    u, v = _inner_point(ring)
    c = _contains(body, frame, u, v, -thick + 0.1)      # kurz vor der Gegenseite: Sackloch endet vorher
    if c is not None:
        return not c
    bb = G.bbox2d(ring)
    opposite = False
    for f in body.faces:
        if f is face:
            continue
        try:
            if f.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
                continue
            w = _uvw(frame, _t(f.pointOnFace))[2]
            if abs(w + thick) > EPS_W:
                continue
            opposite = True
            for lp in f.loops:
                if lp.isOuter:
                    continue
                r2, _s = loop_polygon(lp, frame, 0.01)
                if len(r2) >= 3 and _bbox_overlap(bb, G.bbox2d(r2)):
                    return True
        except Exception:
            continue
    return not opposite          # ohne Gegenfläche nicht entscheidbar -> wie bisher als durchgehend werten


def _kind(ring):
    bb = G.bbox2d(ring)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    if min(w, h) <= 0:
        return 'Tasche'
    a = abs(G.area2d(ring))
    if len(ring) >= 12 and abs(w - h) < 0.05 * max(w, h) and abs(a - math.pi * w * h / 4) < 0.05 * a:
        return 'Bohrung'
    return 'Nut' if max(w, h) / min(w, h) >= 4 else 'Tasche'


def extract_features(body, frame, thick, tol_mm=0.1):
    """Nicht durchgehende Bearbeitungen relativ zur Ablagefläche (w = 0 Ablagefläche, w = −Dicke Gegenseite).

    - ebene Flächen parallel zur Ablagefläche in Zwischenhöhe (Taschen-/Nut-Böden, Falze) mit ihren Konturen
    - zylindrische Sacklöcher mit Achse parallel zur Normale (projizierter Kreis), sofern kein ebener Boden
    side: 'top' = öffnet zur Ablagefläche (liegt im Rohteil OBEN), 'bottom' = öffnet zur Gegenseite (unten,
    Tischseite). Wenden tauscht die Seiten (macht die Palette). depth in mm ab der offenen Seite."""
    o, e1, e2, n = frame
    out = []
    PT = adsk.core.SurfaceTypes
    for f in body.faces:
        try:
            st = f.geometry.surfaceType
        except Exception:
            continue
        try:
            if st == PT.PlaneSurfaceType:
                pof = _t(f.pointOnFace)
                ok, nv = f.evaluator.getNormalAtPoint(f.pointOnFace)
                d = G.dot(G.unit(_t(nv)), n)
                if abs(d) < 0.999:
                    continue
                u0, v0, w = _uvw(frame, pof)
                if w > -EPS_W or w < -thick + EPS_W:
                    continue
                above = _contains(body, frame, u0, v0, w + EPS_W)
                below = _contains(body, frame, u0, v0, w - EPS_W)
                if above is False and below is True:
                    opens_ref = True
                elif above is True and below is False:
                    opens_ref = False
                else:
                    opens_ref = d > 0          # Rückfall: Flächennormale zeigt in die Luft
                ring, islands = None, []
                for lp in f.loops:
                    r2, _s = loop_polygon(lp, frame, tol_mm * MM)
                    if len(r2) < 3:
                        continue
                    if lp.isOuter:
                        ring = G.orient_ring(r2, True)
                    else:
                        islands.append(G.orient_ring(r2, False))
                if not ring:
                    continue
                out.append({'side': 'top' if opens_ref else 'bottom', 'kind': _kind(ring),
                            'depth': round(-w if opens_ref else w + thick, 3), 'pts': ring, 'islands': islands,
                            'source': 'Ebene'})
            elif st == PT.CylinderSurfaceType:
                g = f.geometry
                ax = G.unit(_t(g.axis))
                if abs(G.dot(ax, n)) < 0.999:
                    continue
                ws = []
                for e in f.edges:
                    ev = e.evaluator
                    ok, p0, p1 = ev.getParameterExtents()
                    ok, strokes = ev.getStrokes(p0, p1, tol_mm * MM)
                    ws.extend(_uvw(frame, _t(p))[2] for p in strokes)
                if not ws:
                    continue
                wmin, wmax = min(ws), max(ws)
                top, bot = wmax > -EPS_W, wmin < -thick + EPS_W
                if (top and bot) or not (top or bot):
                    continue                    # durchgehend (schon Innenkontur) oder innenliegend
                cu, cv, _w = _uvw(frame, _t(g.origin))
                r = g.radius / MM
                if _contains(body, frame, cu, cv, (wmin + wmax) / 2) is True:
                    continue                    # Zapfen, kein Loch
                side = 'top' if top else 'bottom'
                k = max(16, min(64, int(2 * math.pi * r / 2)))
                ring = [(cu + r * math.cos(2 * math.pi * i / k), cv + r * math.sin(2 * math.pi * i / k)) for i in range(k)]
                out.append({'side': side, 'kind': 'Bohrung', 'depth': round(-wmin if top else wmax + thick, 3),
                            'pts': ring, 'islands': [], 'source': 'Zylinder', 'radius': r})
        except Exception:
            continue
    # Sackloch mit flachem Boden: ebene Kreisfläche + Zylinder -> nur einmal (als Bohrung) zählen
    planar = [q for q in out if q['source'] == 'Ebene']
    keep = []
    for q in out:
        if q['source'] == 'Zylinder':
            r = q['radius']
            cu = sum(p[0] for p in q['pts']) / len(q['pts'])
            cv = sum(p[1] for p in q['pts']) / len(q['pts'])
            dup = None
            for pq in planar:
                bb = G.bbox2d(pq['pts'])
                if pq['side'] == q['side'] and abs((bb[0] + bb[2]) / 2 - cu) < 0.2 * r + 0.1 \
                        and abs((bb[1] + bb[3]) / 2 - cv) < 0.2 * r + 0.1 and abs(bb[2] - bb[0] - 2 * r) < 0.2 * r + 0.1:
                    dup = pq
            if dup is not None:
                dup['kind'] = 'Bohrung'
                continue
        keep.append(q)
    return keep


def feature_summary(features):
    s = {'top': {}, 'bottom': {}}
    for f in features or []:
        s[f['side']][f['kind']] = s[f['side']].get(f['kind'], 0) + 1
    return s


def convert_body_to_component(body):
    """BRepBody.createComponent(): verschiebt den Körper in eine neue Komponente (ändert die Konstruktion!)."""
    nb = body.createComponent()
    return nb.parentComponent, nb


# ---------------------------------------------------------------- Rohteile finden/löschen
def sheet_occurrences(root):
    out = []
    for occ in root.occurrences:
        try:
            if occ.attributes.itemByName(G.GROUP, 'sheet'):
                out.append(occ)
        except Exception:
            continue
    return out


def sheet_project(occ):
    try:
        a = occ.attributes.itemByName(G.GROUP, 'project_id')
        return a.value if a else ''
    except Exception:
        return ''


def _delete_sheet_occ(occ):
    """Ein Rohteil löschen: vorher Festsetzen/Fixierungen lösen. Rückgabe True bei Erfolg."""
    nm = _safe(lambda: occ.name)
    try:
        _cp('Rohteil %s: isGrounded=False', nm)
        occ.isGrounded = False
    except Exception:
        pass
    try:
        for ch in list(occ.component.occurrences):
            try:
                if _flag(ch, 'isGroundToParent'):
                    _cp('Rohteil %s: Fixierung %s lösen', nm, _safe(lambda: ch.name))
                    _set_ground_to_parent(ch, occ, False)
            except Exception:
                pass
    except Exception:
        pass
    try:
        _cp('Rohteil %s: deleteMe()', nm)
        occ.deleteMe()
        return True
    except Exception:
        return False


def delete_nesting(design, project_id=None, log=None):
    """Rohteile löschen (alle oder nur die eines Projekts). Vorher Fixierungen lösen.
    ACHTUNG: verweisen CAM-Setups auf diese Rohteile, MÜSSEN deren Modelle/Bearbeitungen vorher gelöst sein
    (ContourNest._prepare_cam_for_delete) – sonst stürzt Fusion 2705 beim nächsten CAM-Zugriff ab (Log 12:48/12:53)."""
    n = 0
    for occ in sheet_occurrences(design.rootComponent):
        if project_id is not None and sheet_project(occ) != project_id:
            continue
        if _delete_sheet_occ(occ):
            n += 1
    return n


# ---------------------------------------------------------------- Bearbeiten: Rohteil an Ort und Stelle
def _attr_value(occ, name):
    """Attribut lesen – erst am Objekt, dann am nativen Objekt (Proxy)."""
    for o in (occ, _safe(lambda: occ.nativeObject)):
        if o is None:
            continue
        try:
            a = o.attributes.itemByName(G.GROUP, name)
            if a:
                return a.value
        except Exception:
            continue
    return None


def _json_attr(occ, name, default=None):
    v = _attr_value(occ, name)
    if v is None:
        return default
    try:
        return json.loads(v)
    except Exception:
        return v


def project_sheets(design, project_id):
    """[(Projekt-Rohteil-Nr, Rohteil-Occurrence, sheet-Attribut)] eines Projekts, sortiert."""
    out = []
    for occ in sheet_occurrences(design.rootComponent):
        if sheet_project(occ) != project_id:
            continue
        d = _json_attr(occ, 'sheet', {}) or {}
        if not isinstance(d, dict):
            d = {}
        out.append((int(d.get('project_sheet') or d.get('index') or 0), occ, d))
    out.sort(key=lambda t: t[0])
    return out


def part_key(p):
    """Schlüssel eines Ergebnis-Teils (Palette): 'key' oder '<id>#<copy>'."""
    return p.get('key') or '%s#%s' % (p.get('id'), p.get('copy', 0))


def _copy_of(p):
    """Kopie-Index (0-basiert) eines Ergebnis-Teils: 'copy' oder aus dem Schlüssel '<id>#<n>'."""
    if p.get('copy') is not None:
        try:
            return int(p['copy'])
        except Exception:
            pass
    try:
        return int(str(p.get('key', '')).rsplit('#', 1)[1])
    except Exception:
        return 0


def plan_edit(design, project_id, result, sh, ctx=None, tol=1e-6):
    """Plan für „Nesting bearbeiten“ OHNE Neuaufbau: vorhandene Rohteile und Teile-Occurrences bleiben erhalten.

    Je Ergebnis-Rohteil i: vorhandenes Projekt-Rohteil i wird wiederverwendet; seine Teile-Occurrences werden
    über die Projekt-Kennung (Attribute part_id + copy) den Ergebnis-Teilen zugeordnet (erst gleiche Kopie,
    dann beliebige Instanz desselben Teils). Nur echte Überzähligkeiten werden gelöscht, nur Fehlende eingefügt.
    Ändert sich das Plattenmaß, wird nur der Rohteil-Körper neu aufgebaut (resize).
    Konstruktion MIT Verlauf (Fusion 2705): die native Lage verschachtelter Occurrences bleibt auf dem Einfügewert,
    Festsetzen/Neuberechnen springt dorthin zurück (Logs 10:10/10:25) -> ein Teil, dessen Lage sich ändert, kann
    dort NICHT dauerhaft verschoben werden; es wird ersetzt (gelöscht + kompensiert neu eingefügt, 'replaced').
    Unveränderte Teile bleiben immer unangetastet. Direktmodell: geänderte Teile werden verschoben.
    sh: Rohteil-Einstellungen in mm (length/width/thickness); ctx: Auftrag (Teile-Rahmen) für den Lagevergleich."""
    old = project_sheets(design, project_id)
    parametric = is_parametric(design)
    plan = {'project_id': project_id, 'sheets': [], 'remove_sheets': [], 'kept': 0, 'added': 0, 'removed': 0,
            'resized': 0, 'replaced': 0, 'moved': 0, 'unchanged': 0, 'turned': 0, 'parametric': parametric}
    # 1.10.0: je Blatt eigenes Maß/Ausrichtung; Teillagen im Materialsystem (quer = Rohteil-Occurrence gedreht)
    result = G.material_result(result, sh, (ctx or {}).get('job', {}).get('material', '') if ctx else '')
    sheets = result.get('sheets') or []
    hits, rest = G.match_sheets(old, len(sheets))     # 2.0.4: Zuordnung über die Projekt-Rohteil-Nr.
    for i, S in enumerate(sheets):
        gm = S['geom']
        if hits[i] is None:
            plan['sheets'].append({'index': i, 'occ': None, 'no': None, 'resize': False, 'turn': False, 'match': {},
                                   'remove': [], 'geom': gm})
            plan['added'] += len(S.get('parts') or [])
            continue
        _ps, so, d = hits[i]
        resize = False
        for k_mm, k in (('length_mm', 'length'), ('width_mm', 'width'), ('thickness_mm', 'thickness'),
                        ('frame_mm', 'frame')):
            try:
                if abs(float(d.get(k_mm, gm[k] if k == 'frame' else None)) - float(gm[k])) > 1e-6:
                    resize = True
            except Exception:
                resize = True
        # Drehen längs/quer: nur die Rohteil-Occurrence wird gedreht – Körper und Teile bleiben (CAM-sicher)
        turn = (d.get('orient') or 'laengs') != gm['orient']
        pool = {}
        for ch in list(_safe(lambda: so.component.occurrences) or []):
            pid = _attr_value(ch, 'part_id')
            if not pid:
                continue                       # fremde Occurrence (vom Anwender) – nie anfassen
            try:
                cp = int(_json_attr(ch, 'copy', 0) or 0)
            except Exception:
                cp = 0
            pool.setdefault(pid, []).append([cp, ch])
        match = {}
        parts = S.get('parts') or []
        for p in parts:                        # 1) gleiche Instanz (Teil + Kopie)
            lst = pool.get(p.get('id')) or []
            want = _copy_of(p) + 1
            hit = next((e for e in lst if e[0] == want), None)
            if hit:
                lst.remove(hit)
                match[part_key(p)] = hit[1]
        for p in parts:                        # 2) irgendeine Instanz desselben Teils
            k = part_key(p)
            if k in match:
                continue
            lst = pool.get(p.get('id')) or []
            if lst:
                match[k] = lst.pop(0)[1]
        remove = [e[1] for lst in pool.values() for e in lst]
        # Lagevergleich: unverändert -> behalten; geändert -> Direktmodell verschieben, mit Verlauf ersetzen
        sw = _safe(lambda: _coord_sys(so.transform2))
        if turn:                               # Vergleich gegen die Lage NACH dem Drehen des Rohteils
            sw = _safe(lambda: G.sheet_matrix(gm, machine_origin_cm(so, d)))
        moved = set()
        for p in parts:
            k = part_key(p)
            occ = match.get(k)
            info = ((ctx or {}).get('parts') or {}).get(p.get('id'))
            if occ is None or sw is None or not info or not info.get('frame'):
                continue
            try:
                wa = G.compose_axes(sw, part_axes(info, p, gm['thickness']))
                _loc, wld = _read_pose(occ, so)
                dev = _axes_diff(wld, wa) if wld else None
            except Exception:
                dev = None
            if dev is not None and dev <= tol:
                plan['unchanged'] += 1
                continue
            if parametric:
                remove.append(match.pop(k))
                plan['replaced'] += 1
            else:
                moved.add(k)
                plan['moved'] += 1
        plan['sheets'].append({'index': i, 'occ': so, 'no': int(d.get('index') or i + 1), 'resize': resize,
                               'turn': turn, 'match': match, 'remove': remove, 'name': _safe(lambda: so.name),
                               'geom': gm, 'old_attr': d})
        plan['turned'] += 1 if turn else 0
        plan['kept'] += len(match)
        plan['added'] += len(parts) - len(match)
        plan['removed'] += len(remove)
        plan['resized'] += 1 if resize else 0
    plan['remove_sheets'] = [so for _p, so, _d in rest]
    return plan


def plan_deletions(plan):
    """Was der Plan löschen würde (für die CAM-Abhängigkeitsprüfung VOR dem Löschen).
    paths: fullPathName-Präfixe im Wurzel-Kontext (Rohteil bzw. Rohteil+Teil), stock_sheets: Rohteile, deren
    Plattenkörper neu aufgebaut wird, sheet_nos: betroffene Projekt-Rohteil-Nummern (1-basiert)."""
    out = {'paths': [], 'stock_sheets': [], 'sheet_nos': set(), 'count': 0, 'idents': []}
    if not plan:
        return out
    for e in plan['sheets']:
        if e.get('occ') is None:
            continue
        sn = _safe(lambda: e['occ'].name) or ''
        for ch in e.get('remove') or []:
            out['paths'].append('%s+%s' % (sn, _safe(lambda: ch.name)))
            out['idents'].append({'path': '%s+%s' % (sn, _safe(lambda: ch.name)), 'name': A._g(lambda: ch.name, ''),
                                  'token': A._g(lambda: ch.entityToken, ''),
                                  'comp': A._g(lambda: ch.component.id, '')})   # 2.0.1: Abgleich per Identität
            out['sheet_nos'].add(e['index'] + 1)
            out['count'] += 1
        if e.get('resize'):
            out['stock_sheets'].append(sn)
            out['sheet_nos'].add(e['index'] + 1)
            out['count'] += 1
    for so in plan.get('remove_sheets') or []:
        out['paths'].append(_safe(lambda: so.name) or '')
        d = _json_attr(so, 'sheet', {}) or {}
        out['sheet_nos'].add(int(d.get('project_sheet') or 0) if isinstance(d, dict) else 0)
        out['count'] += 1
    return out


def _delete_part_occ(ch, sheet_occ):
    nm = _safe(lambda: ch.name)
    try:
        if _flag(ch, 'isGroundToParent'):
            _cp('Teil %s: Fixierung lösen (zum Löschen)', nm)
            _set_ground_to_parent(ch, sheet_occ, False)
    except Exception:
        pass
    try:
        _cp('Teil %s: deleteMe()', nm)
        ch.deleteMe()
        return True
    except Exception:
        return False


def _remove_stock(sheet_comp, log):
    """Plattenkörper + Skizzen eines Rohteils entfernen (nur bei geändertem Plattenmaß)."""
    n = 0
    try:
        ex = sheet_comp.features.extrudeFeatures
        for k in range(ex.count - 1, -1, -1):
            f = ex.item(k)
            names = [_safe(lambda: f.bodies.item(j).name) for j in range(_safe(lambda: f.bodies.count) or 0)]
            if 'Rohteil' in names:
                _cp('Rohteil-Körper: Extrusion %s löschen', _safe(lambda: f.name))
                f.deleteMe()
                n += 1
    except Exception as e:
        log.append('Rohteil-Körper entfernen: %s' % e)
    try:
        sks = sheet_comp.sketches
        for k in range(sks.count - 1, -1, -1):
            sk = sks.item(k)
            if _safe(lambda: sk.name) in ('Rohteil Kontur', 'Faserrichtung'):
                _cp('Skizze %s löschen', _safe(lambda: sk.name))
                sk.deleteMe()
    except Exception as e:
        log.append('Rohteil-Skizzen entfernen: %s' % e)
    return n


def _update_grain_text(sheet_comp, text):
    """Beschriftung (Ausnutzung) der Faserskizze aktualisieren – nur Zierde, nie fatal."""
    try:
        sks = sheet_comp.sketches
        for k in range(sks.count):
            sk = sks.item(k)
            if _safe(lambda: sk.name) == 'Faserrichtung' and sk.sketchTexts.count:
                sk.sketchTexts.item(0).text = text
                return True
    except Exception:
        pass
    return False


def _move_existing(occ, sheet_occ, la, wa, prec, parametric, tol=1e-6):
    """Vorhandene Teile-Occurrence an die neue Lage bringen (statt löschen + neu einfügen).
    Die Occurrence bleibt dieselbe -> CAM-Setups/Bearbeitungen behalten ihre Bezüge."""
    rec = {'method': 'beibehalten', 'kept': True, 'attempts': []}
    prec['insertion'] = rec
    loc, wld = _read_pose(occ, sheet_occ)
    dw = _axes_diff(wld, wa) if wld else None
    if dw is not None and dw <= tol:
        rec['moved'] = False
        rec['native_ok'] = True
        return False
    rec['moved'] = True
    rec['dev_before_cm'] = dw
    nm = _safe(lambda: occ.name)
    if _flag(occ, 'isGroundToParent'):
        try:
            _cp('Teil %s: Fixierung lösen (Verschieben)', nm)
            rec['unfixed'] = _set_ground_to_parent(occ, sheet_occ, False)
        except Exception as e:
            rec['unfix_error'] = str(e)
    if not parametric:
        # 2.0.0: Direktmodell – NATIVE Lage setzen (sonst springt das Teil beim nächsten Neuberechnen zurück,
        # nur die Kontext-Lage des Proxys zu setzen genügte nicht)
        try:
            _cp('Teil %s: native transform2 setzen (direkt)', nm)
            occ.transform2 = _matrix(la)
            rec['attempts'].append({'how': 'native.transform2=lokal (direkt)'})
        except Exception as e:
            rec['attempts'].append({'how': 'native.transform2=lokal (direkt)', 'error': str(e)})
        _l2, w2 = _read_pose(occ, sheet_occ)
        d2 = _axes_diff(w2, wa) if w2 else None
        rec['dev_after_native_cm'] = d2
        if d2 is not None and d2 <= tol:
            rec['native_ok'] = True
            return True
    try:
        _cp('Teil %s: proxy.transform2 setzen', nm)
        occ.createForAssemblyContext(sheet_occ).transform2 = _matrix(wa)
        rec['attempts'].append({'how': 'proxy.transform2=welt'})
    except Exception as e:
        rec['attempts'].append({'how': 'proxy.transform2=welt', 'error': str(e)})
        try:
            _cp('Teil %s: native transform2 setzen', nm)
            occ.transform2 = _matrix(la)
            rec['attempts'].append({'how': 'native.transform2=lokal'})
        except Exception as e2:
            rec['attempts'][-1]['error2'] = str(e2)
    # mit Verlauf bleibt die native (Zeitachsen-)Lage verschachtelter Occurrences in Fusion 2705 auf dem
    # Einfügewert -> nicht festsetzen (würde zurückspringen), sondern starre Verbindung / Überschreibung
    rec['native_ok'] = not parametric
    return True


# ---------------------------------------------------------------- Ergebnis aufbauen
def _sketch_rect(sk, x0, y0, x1, y1, construction=False):
    lines = sk.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(x0, y0, 0), adsk.core.Point3D.create(x1, y1, 0))
    if construction:
        for i in range(lines.count):
            lines.item(i).isConstruction = True
    return lines


def _grain_sketch(comp, L, W, grain, text):
    """Faserpfeil + Text auf der Oberseite (cm)."""
    sk = comp.sketches.add(comp.xYConstructionPlane)
    sk.name = 'Faserrichtung'
    ln = sk.sketchCurves.sketchLines
    P = adsk.core.Point3D.create
    if grain == 'Y':
        x, y0, y1 = 0.05 * L, 0.15 * W, 0.45 * W
        ln.addByTwoPoints(P(x, y0, 0), P(x, y1, 0))
        ln.addByTwoPoints(P(x, y1, 0), P(x - 0.012 * L, y1 - 0.03 * W, 0))
        ln.addByTwoPoints(P(x, y1, 0), P(x + 0.012 * L, y1 - 0.03 * W, 0))
        tp = P(x + 0.02 * L, y0, 0)
    else:
        y, x0, x1 = 0.06 * W, 0.08 * L, 0.35 * L
        ln.addByTwoPoints(P(x0, y, 0), P(x1, y, 0))
        ln.addByTwoPoints(P(x1, y, 0), P(x1 - 0.02 * L, y + 0.02 * W, 0))
        ln.addByTwoPoints(P(x1, y, 0), P(x1 - 0.02 * L, y - 0.02 * W, 0))
        tp = P(x0, y + 0.03 * W, 0)
    h = max(1.0, min(L, W) * 0.02)
    try:
        st = sk.sketchTexts
        ti = None
        if hasattr(st, 'createInput3'):      # aktuelle API: Ausdruck, Text in einfachen Hochkommas
            ti = st.createInput3("'%s'" % text.replace("'", ' '), adsk.core.ValueInput.createByReal(h))
        elif hasattr(st, 'createInput2'):    # zurückgezogen, ältere Fusion-Versionen
            ti = st.createInput2(text, h)
        if ti is not None:
            ti.setAsMultiLine(tp, P(tp.x + 0.6 * L, tp.y + 4 * h, 0),
                              adsk.core.HorizontalAlignments.LeftHorizontalAlignment,
                              adsk.core.VerticalAlignments.BottomVerticalAlignment, 0)
        else:
            ti = st.createInput(text, h, tp)
        st.add(ti)
    except Exception:
        pass     # Text ist nur Zierde
    return sk


def _flag(occ, name):
    try:
        return bool(getattr(occ, name))
    except Exception:
        return None


def _coord_sys(matrix):
    o, x, y, z = matrix.getAsCoordinateSystem()
    return (_t(o), _t(x), _t(y), _t(z))


def _axes_diff(a, b):
    return max(abs(u - v) for A, B in zip(a, b) for u, v in zip(A, B))


def _matrix(axes):
    t, xa, ya, za = axes
    m = adsk.core.Matrix3D.create()
    m.setWithCoordinateSystem(adsk.core.Point3D.create(*t), adsk.core.Vector3D.create(*xa),
                              adsk.core.Vector3D.create(*ya), adsk.core.Vector3D.create(*za))
    return m


def _axes_json(axes):
    return {'origin': list(axes[0]), 'x': list(axes[1]), 'y': list(axes[2]), 'z': list(axes[3])}


def part_axes(info, p, thickness_mm):
    """Lage (Rohteil-Koordinaten) eines Teils aus dem Nesting-Datensatz; gewendete Teile: Ablagefläche unten."""
    o, e1, e2, n = info['frame']
    if p.get('flip'):
        # Drehung 180° um e1: e2 -> -e2, n -> -n; 2D-Kontur (u, -v) = Spiegelung; Ablagefläche bei Z = 0
        return G.placement_axes(o, e1, G.mul(e2, -1), G.mul(n, -1), p['rot'], p['x'], p['y'], 0.0)
    return G.placement_axes(o, e1, e2, n, p['rot'], p['x'], p['y'], thickness_mm)


def _read_pose(occ, sheet_occ):
    """(lokal relativ zum Rohteil, Welt) – jeweils Achsen-Tupel oder None."""
    loc = wld = None
    try:
        loc = _coord_sys(occ.transform2)
    except Exception:
        pass
    try:
        wld = _coord_sys(occ.createForAssemblyContext(sheet_occ).transform2)
    except Exception:
        pass
    return loc, wld


def _world_bbox(occ, sheet_occ):
    """Welt-Hüllquader (mm) aller Körper der platzierten Occurrence."""
    try:
        prox = occ.createForAssemblyContext(sheet_occ)
        bodies = prox.bRepBodies
        lo, hi = [1e18] * 3, [-1e18] * 3
        n = 0
        for i in range(bodies.count):
            bb = bodies.item(i).boundingBox
            for k, (a, b) in enumerate(((bb.minPoint.x, bb.maxPoint.x), (bb.minPoint.y, bb.maxPoint.y),
                                        (bb.minPoint.z, bb.maxPoint.z))):
                lo[k] = min(lo[k], a / MM)
                hi[k] = max(hi[k], b / MM)
            n += 1
        return (lo, hi) if n else None
    except Exception:
        return None


def verify_part(occ, sheet_occ, local_axes, world_axes, rec, tag, fix=True, tol=1e-6):
    """Liest die Lage zurück (lokal + Welt), protokolliert sie in rec[tag] und korrigiert bei Abweichung.

    Korrektur-Reihenfolge: 1) Proxy im Wurzel-Kontext (Rohteil-Occurrence).transform2 = Welt-Soll-Matrix –
    in Konstruktionen mit Verlauf der EINZIGE erlaubte Weg (Fusion: „transform overrides can only be set on
    Occurrence proxy from root component“), 2) native Occurrence.transform2 = lokale Soll-Matrix.
    Rückgabe: 'ok' | 'fixed' | 'bad'."""
    loc, wld = _read_pose(occ, sheet_occ)
    dl = _axes_diff(loc, local_axes) if loc else None
    dw = _axes_diff(wld, world_axes) if wld else None
    r = {'local_read': _axes_json(loc) if loc else None, 'world_read': _axes_json(wld) if wld else None,
         'dev_local_cm': dl, 'dev_world_cm': dw}
    rec[tag] = r
    if (dw is not None and dw <= tol) or (dw is None and dl is not None and dl <= tol):
        r['status'] = 'ok'
        if dw is not None and dl is not None and dl > tol:
            # Fusion 2705 (Log 10:10): das native transform2 verschachtelter Occurrences behält den Wert vom
            # Einfügen – auch nach dem Snapshot. Nur Info; maßgeblich sind Welt-Lage + hasPendingSnapshot.
            r['native_differs'] = True
        return 'ok'
    r['status'] = 'bad'
    if not fix:
        return 'bad'
    attempts = []
    for how, setter in (('proxy.transform2=welt',
                         lambda: setattr(occ.createForAssemblyContext(sheet_occ), 'transform2', _matrix(world_axes))),
                        ('native.transform2=lokal', lambda: setattr(occ, 'transform2', _matrix(local_axes))),
                        ('lösen+proxy+fixieren', lambda: _set_unfixed(occ, sheet_occ, world_axes))):
        try:
            setter()
            l2, w2 = _read_pose(occ, sheet_occ)
            d2 = _axes_diff(w2, world_axes) if w2 else None
            attempts.append({'how': how, 'dev_world_cm': d2, 'local_read': _axes_json(l2) if l2 else None})
            if d2 is not None and d2 <= tol:
                r['status'] = 'fixed'
                break
        except Exception as e:
            attempts.append({'how': how, 'error': str(e)})
    r['fix_attempts'] = attempts
    return r['status']


def _set_ground_to_parent(occ, sheet_occ, value):
    """isGroundToParent setzen – zuerst über den Proxy im Wurzel-Kontext, dann nativ. Rückgabe: Weg oder Fehler."""
    errs = []
    for how, tgt in (('proxy', lambda: occ.createForAssemblyContext(sheet_occ)), ('nativ', lambda: occ)):
        try:
            tgt().isGroundToParent = value
            return how
        except Exception as e:
            errs.append('%s: %s' % (how, e))
    raise RuntimeError('; '.join(errs))


def _set_unfixed(occ, sheet_occ, world_axes):
    """Lage eines fixierten Teils korrigieren: lösen, per Proxy setzen, wieder fixieren."""
    was = bool(_flag(occ, 'isGroundToParent'))
    if was:
        _set_ground_to_parent(occ, sheet_occ, False)
    try:
        occ.createForAssemblyContext(sheet_occ).transform2 = _matrix(world_axes)
    finally:
        if was:
            _set_ground_to_parent(occ, sheet_occ, True)


FIX_MODES = (('ground', 'Festsetzen (am Rohteil fixieren)'), ('joint', 'Starre Verbindung zum Rohteil'),
             ('none', 'Nicht fixieren'))


def fix_parts(design, entries, mode, log, stats, diag):
    """Teile NACH Korrektur + Snapshot fixieren und prüfen, dass das Fixieren nichts verschoben hat.
    entries: [(occ, sheet_occ, local_axes, world_axes, prec)]. mode: 'ground' | 'joint' | 'none'."""
    stats['fixed_mode'] = mode
    stats['fixed_parts'] = 0
    stats['moved_by_fixing'] = 0
    stats['moved_by_compute'] = 0
    stats['joint_fallback'] = 0
    if not entries:
        return
    if mode == 'none':
        _compute_check(design, entries, log, stats, diag)
        return
    for occ, so, la, wa, prec in entries:
        native_ok = (prec.get('insertion') or {}).get('native_ok', True)
        pmode = mode
        if mode == 'ground' and not native_ok:
            pmode = 'joint'               # Stufe (c): native Lage falsch -> Festsetzen würde zurückspringen
            stats['joint_fallback'] += 1
        frec = {'mode': pmode, 'native_ok': native_ok}
        prec['fixing'] = frec
        b_l, b_w = _read_pose(occ, so)
        frec['before_world'] = _axes_json(b_w) if b_w else None
        frec['before_native'] = _axes_json(b_l) if b_l else None
        done = False
        if pmode == 'joint':
            try:
                root = design.rootComponent
                ji = root.asBuiltJoints.createInput(occ.createForAssemblyContext(so), so, None)
                try:
                    ji.setAsRigidJointMotion()
                except Exception:
                    pass
                j = root.asBuiltJoints.add(ji)
                try:
                    j.name = 'LazyNest %s' % _safe(lambda: occ.name)
                except Exception:
                    pass
                a_l, a_w = _read_pose(occ, so)
                dv = _axes_diff(a_w, wa) if a_w else None
                if dv is not None and dv > 1e-6:
                    frec['joint_moved_cm'] = dv
                    j.deleteMe()                     # Verbindung hat verschoben -> zurück
                    occ.createForAssemblyContext(so).transform2 = _matrix(wa)
                    if not native_ok:
                        frec['how'] = 'nicht fixiert (native Lage falsch, Verbindung verschob)'
                        log.append('%s: nicht fixiert (Lage nur als Überschreibung)' % prec.get('name'))
                        continue
                    log.append('Starre Verbindung verschob %s – stattdessen festgesetzt' % prec.get('name'))
                else:
                    frec['how'] = 'starre Verbindung'
                    done = True
            except Exception as e:
                frec['joint_error'] = str(e)
                if not native_ok:
                    frec['how'] = 'nicht fixiert (native Lage falsch, Verbindung fehlgeschlagen)'
                    continue
        if not done:
            try:
                frec['how'] = 'isGroundToParent (%s)' % _set_ground_to_parent(occ, so, True)
                done = True
            except Exception as e:
                frec['error'] = str(e)
                if len([l_ for l_ in log if l_.startswith('Fixieren')]) < 3:
                    log.append('Fixieren %s: %s' % (prec.get('name'), e))
        if done:
            stats['fixed_parts'] += 1
    diag['snapshots'].append(capture_positions(design, log, 'nach Fixieren'))
    for occ, so, la, wa, prec in entries:
        st = verify_part(occ, so, la, wa, prec, 'after_fixing', fix=True)
        prec['fixing']['after_world'] = prec['after_fixing'].get('world_read')
        prec['fixing']['after_native'] = prec['after_fixing'].get('local_read')
        prec['fixing']['isGroundToParent'] = _flag(occ, 'isGroundToParent')
        if st != 'ok':
            stats['moved_by_fixing'] += 1
    if stats['moved_by_fixing']:
        diag['snapshots'].append(capture_positions(design, log, 'Korrektur nach Fixieren'))
        log.append('%d Teil(e) beim Fixieren verschoben – korrigiert (Überschreibung; Prüfung nach Neuberechnung '
                   'folgt)' % stats['moved_by_fixing'])
    _compute_check(design, entries, log, stats, diag)


LAST_COMPUTE = {}      # 2.0.1: letzte erfolgreiche Neuberechnung nach dem Übernehmen (doppeltes computeAll vermeiden)
APPLY_SEQ = 0


def _compute_check(design, entries, log, stats, diag):
    """Erzwungene Neuberechnung (design.computeAll) und erneute Prüfung: nur was danach noch stimmt, ist dauerhaft.
    Abweichende Teile werden gelöst, per Proxy korrigiert und erfasst (Notbehelf, Warnung im Protokoll)."""
    info = {'tag': 'computeAll'}
    try:
        global APPLY_SEQ
        APPLY_SEQ += 1
        LAST_COMPUTE.clear()
        _cp('design.computeAll() (Prüfung nach Übernehmen)')
        with TM.phase('computeAll (Prüfung nach Übernehmen)'):
            design.computeAll()
        _cp('design.computeAll() fertig')
        info['result'] = 'ok'
        LAST_COMPUTE.update(ok=True, apply_seq=APPLY_SEQ)
        write_compute_state(design, 'LazyNest')
    except Exception as e:
        info['result'] = 'FEHLER: %s' % e
        if CC is not None:
            _safe(lambda: CC.clear(design))         # 2.0.3: kein „berechnet“-Stand nach fehlgeschlagenem computeAll
    diag.setdefault('snapshots', []).append(info)
    bad = []
    for occ, so, la, wa, prec in entries:
        st = verify_part(occ, so, la, wa, prec, 'after_compute', fix=False)
        if prec.get('fixing') is not None:
            prec['fixing']['after_compute_world'] = prec['after_compute'].get('world_read')
        if st != 'ok':
            bad.append((occ, so, la, wa, prec))
    stats['moved_by_compute'] = len(bad)
    if bad:
        for occ, so, la, wa, prec in bad:
            verify_part(occ, so, la, wa, prec, 'after_compute_fix', fix=True)
        diag['snapshots'].append(capture_positions(design, log, 'Korrektur nach Neuberechnung'))
        log.append('WARNUNG: %d Teil(e) nach Neuberechnung verschoben – nur per Überschreibung korrigiert, '
                   'Lage evtl. nicht dauerhaft' % len(bad))


STATE_GROUP, STATE_NAME = 'CA_Tools', 'compute_state'   # 2.0.2: gemeinsam mit LazySetup 2.0.2 (fs_run)


def core_sig(design):
    """Zeitachsen-Stand (Dokument, Anzahl, Marker, Typ) – identisch zu LazySetup fs_run.core_sig."""
    tl = _safe(lambda: design.timeline)
    return [_safe(lambda: design.parentDocument.name) or '',
            _safe(lambda: tl.count) if tl is not None else None,
            _safe(lambda: tl.markerPosition) if tl is not None else None,
            _safe(lambda: int(design.designType))]


CC = None      # 2.0.3: ca_core.ca_compute (von LazyNest.py gesetzt)


def write_compute_state(design, by):
    """2.0.2: Nach erfolgreichem computeAll den Zeitachsen-Stand als Design-Attribut ablegen.
    2.0.3: Fingerabdruck CA_Tools/computed_state (ca_core.ca_compute: Dokument, Typ, Anzahl, Marker, Hash über
    Name/Unterdrückung/Zustand aller Zeitachsen-Elemente). LazySetup „auto“ überspringt dann sein computeAll."""
    if CC is None:
        return False
    try:
        import time as _time
        t0 = _time.time()
        ok, fp = CC.write(design, by)
        if ok:
            LAST_COMPUTE['fp'] = fp
            _L.info('Neuberechnungs-Stand gespeichert (%s, %.0f ms): %s', by, (_time.time() - t0) * 1000.0, fp)
        else:
            _L.warn('Neuberechnungs-Stand nicht gespeichert: %s', fp)
        return ok
    except Exception as e:
        _L.warn('Neuberechnungs-Stand nicht gespeichert: %s', e)
        return False


def capture_positions(design, log=None, tag=''):
    """Mit Verlauf: geänderte Occurrence-Lagen als Snapshot („Position erfassen“) festschreiben – sonst setzt
    Fusion sie bei der nächsten Neuberechnung der Zeitachse zurück. Direktmodell: nichts zu tun."""
    info = {'tag': tag, 'parametric': is_parametric(design)}
    if not info['parametric']:
        info['result'] = 'Direktmodell – kein Snapshot nötig'
        return info
    try:
        snaps = design.snapshots
        pend = snaps.hasPendingSnapshot
        info['pending_before'] = bool(pend)
        if pend:
            _cp('snapshots.add() (%s)', tag)
            snaps.add()
            info['result'] = 'Snapshot erstellt'
        else:
            info['result'] = 'nichts ausstehend'
        info['pending_after'] = bool(snaps.hasPendingSnapshot)
    except Exception as e:
        info['result'] = 'FEHLER: %s' % e
        if log is not None:
            log.append('Position erfassen (%s): %s' % (tag, e))
    return info


def component_occurrences(design, comp):
    """Alle Instanzen einer Komponente mit Welt-Matrix (Diagnose für die Fusion-Eigenheit beim Einfügen)."""
    out = []
    try:
        occs = design.rootComponent.allOccurrencesByComponent(comp)
        for i in range(occs.count):
            o = occs.item(i)
            out.append({'path': _safe(lambda: o.fullPathName),
                        'transform2': _safe(lambda: _axes_json(_coord_sys(o.transform2)))})
    except Exception as e:
        out.append({'error': str(e)})
    return out


def _build_stock(sheet_comp, sh, L, W, T, txt, opts, log):
    """Skizze „Rohteil Kontur“ (+ Rand), Faserskizze und Plattenkörper „Rohteil“ (cm)."""
    _cp('%s: Rohteil-Skizze/Körper aufbauen', _safe(lambda: sheet_comp.name))
    sk = sheet_comp.sketches.add(sheet_comp.xYConstructionPlane)
    sk.name = 'Rohteil Kontur'
    _sketch_rect(sk, 0, 0, L, W)
    f = sh['frame'] * MM
    if f > 0:
        _sketch_rect(sk, f, f, L - f, W - f, construction=True)
    try:
        _grain_sketch(sheet_comp, L, W, sh['grain'], txt)
    except Exception as e:
        log.append('Faserskizze: %s' % e)
    if opts.get('stock_body', True) and sk.profiles.count:
        try:
            prof = max((sk.profiles.item(k) for k in range(sk.profiles.count)),
                       key=lambda p: p.areaProperties().area)
            ext = sheet_comp.features.extrudeFeatures.addSimple(
                prof, adsk.core.ValueInput.createByReal(T),
                adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
            b = ext.bodies.item(0)
            b.name = 'Rohteil'
            try:
                b.opacity = 0.35
            except Exception:
                pass
        except Exception as e:
            log.append('Rohteil-Körper: %s' % e)


def _world_pts_mm(axes, pts):
    """Punkte (mm) im Rohteil-System -> Welt (mm) über die Achsen der Rohteil-Occurrence (cm)."""
    o, xa, ya = axes[0], axes[1], axes[2]
    return [((o[0] + (u * xa[0] + v * ya[0]) * MM) / MM, (o[1] + (u * xa[1] + v * ya[1]) * MM) / MM) for u, v in pts]


def machine_origin_cm(occ, attr):
    """X-Lage (cm) des Maschinen-Ursprungs (vordere linke Plattenecke) einer vorhandenen Rohteil-Occurrence."""
    t = occ.transform2.translation
    if isinstance(attr, dict) and attr.get('orient') == 'quer':
        return t.x - float(attr.get('width_mm') or 0) * MM
    return t.x


def _set_sheet_pose(sheet_occ, axes, log, rec):
    """Rohteil-Occurrence (Kind der Wurzel) auf eine neue Lage setzen: Teile (fixiert/verbunden) und Plattenkörper
    bewegen sich mit, nichts wird gelöscht. Festsetzen vorher lösen, danach setzt apply_result es wieder."""
    try:
        if _flag(sheet_occ, 'isGrounded'):
            _cp('%s: isGrounded=False (Drehen)', _safe(lambda: sheet_occ.name))
            sheet_occ.isGrounded = False
    except Exception as e:
        rec['unground_error'] = str(e)
    try:
        _cp('%s: transform2 setzen (längs/quer)', _safe(lambda: sheet_occ.name))
        sheet_occ.transform2 = _matrix(axes)
        rec['turned'] = True
        return True
    except Exception as e:
        log.append('Rohteil %s drehen fehlgeschlagen: %s' % (_safe(lambda: sheet_occ.name), e))
        rec['turn_error'] = str(e)
        return False


def _rebuild_grain(sheet_comp, gm, text, log):
    """Nur die Faserskizze neu (Faser des Abschnitts geändert) – Plattenkörper bleibt."""
    try:
        sks = sheet_comp.sketches
        for k in range(sks.count - 1, -1, -1):
            if _safe(lambda: sks.item(k).name) == 'Faserrichtung':
                sks.item(k).deleteMe()
        _grain_sketch(sheet_comp, gm['length'] * MM, gm['width'] * MM, gm['grain'], text)
    except Exception as e:
        log.append('Faserskizze: %s' % e)


def sheet_text(gm, no, material, util):
    """Text der Faserskizze: Beschriftung (Maß/Ausrichtung/Faser/Art/Nullpunkt) + Material/Ausnutzung."""
    return '%s\n%s  Ausnutzung %s %%' % (G.sheet_label(gm, no), material or 'ohne Material', G.de(util * 100, 1))


def apply_result(design, ctx, result, opts, log=None, diag=None):
    """Baut Rohteil-Komponenten mit Teile-Occurrences auf.

    Architektur (bewusst einfach): Wurzel -> Rohteil NN (Occurrence, nur Verschiebung in X) -> Teile als
    DIREKTE Occurrences der Original-Komponenten. Skizzen und Plattenkörper liegen in der Rohteil-
    Komponente selbst (keine Unter-Komponente, keine Verbindungen).
    ctx: {'job': job, 'parts': {part_id: {'comp', 'frame' (Komponentenraum, cm), 'name', ...}}}
    result: Layout aus der Palette (mm, Grad; inkl. manueller Änderungen, flip)
    diag: dict, wird mit allen Prüfdaten gefüllt (-> logs/apply_*.json)."""
    log = log if log is not None else []
    diag = diag if diag is not None else {}
    root = design.rootComponent
    job = dict(ctx['job'])
    if result.get('material') is not None:       # Material aus der Paletten-Bibliothek
        job['material'] = result['material']
    sh = dict(job['sheet'])
    for k in ('length', 'width', 'thickness', 'frame', 'spacing', 'grain'):     # Schnelleinstellungen der Palette
        if k in result.get('sheet', {}):
            sh[k] = result['sheet'][k]
    # 1.10.0: Blätter mit Teilen, je Blatt Maß/Ausrichtung/Nullpunkt, Teillagen im Materialsystem
    result = G.material_result(result, sh, job.get('material', ''))
    L = sh['length'] * MM
    gap = opts.get('gap', 200.0) * MM
    parametric = is_parametric(design)
    plan = opts.get('plan')                 # Bearbeiten an Ort und Stelle (plan_edit) – sonst None
    stats = {'sheets': 0, 'parts': 0, 'missing': 0, 'moved': 0, 'corrected': 0, 'bbox_mismatch': 0, 'native_ok': 0,
             'native_bad': 0, 'kept': 0, 'kept_moved': 0, 'added': 0, 'removed_parts': 0, 'removed_sheets': 0,
             'stock_rebuilt': 0, 'turned': 0, 'abschnitte': 0}
    if plan:
        # NUR das wirklich Überzählige löschen (CAM-Bezüge wurden vorher gelöst, siehe ContourNest._prepare_apply)
        for e in plan['sheets']:
            for ch in e.get('remove') or []:
                if _delete_part_occ(ch, e['occ']):
                    stats['removed_parts'] += 1
        for so in plan.get('remove_sheets') or []:
            if _delete_sheet_occ(so):
                stats['removed_sheets'] += 1
    existing = sheet_occurrences(root)
    start_no = len(existing) + 1
    if plan:
        reused = set(id(e['occ']) for e in plan['sheets'] if e.get('occ') is not None)
        nos = [int((_json_attr(o, 'sheet', {}) or {}).get('index') or 0) for o in existing]
        start_no = max(nos + [len(existing)]) + 1
        existing = [o for o in existing if id(o) not in reused]
    # 2.0.4: neue Rohteile IMMER hinter allen vorhandenen (auch den behaltenen des bearbeiteten Projekts).
    # Bis 2.0.3 setzte Bearbeiten x_off auf das erste alte Rohteil -> neues Rohteil lag auf Rohteil 01.
    specs = []

    def _okey(o):
        return _safe(lambda: o.entityToken) or _safe(lambda: o.fullPathName) or _safe(lambda: o.name) or id(o)
    kept_geom = dict((_okey(e['occ']), e['geom']) for e in (plan['sheets'] if plan else []) if e.get('occ') is not None)
    for occ in sheet_occurrences(root):
        try:
            a_ = _json_attr(occ, 'sheet', {}) or {}
            if not isinstance(a_, dict):
                a_ = {}
            mw = a_.get('machine_length_mm') or a_.get('length_mm')
            gk = kept_geom.get(_okey(occ))
            if gk is not None:                        # behaltenes Rohteil: neues Maß zählt (resize/drehen)
                mw = gk['W']
            specs.append((machine_origin_cm(occ, a_), float(mw) * MM if mw else None))
        except Exception:
            specs.append((0.0, None))
        # Review P3.9: zusätzlich die tatsächliche Welt-Ausdehnung (cm, inkl. gedrehter Rohteile und überstehender
        # Teile) – das Maximum zählt
        bx = _safe(lambda: occ.boundingBox.maxPoint.x)
        if isinstance(bx, (int, float)):
            specs.append((float(bx), 0.0))
    x_off = G.next_sheet_x(specs, gap, L)
    tl, tl_start = None, None
    if parametric:
        try:
            tl = design.timeline
            tl_start = tl.count
        except Exception:
            tl, tl_start = None, None
    try:
        design.activateRootComponent()      # Wurzel aktiv: Matrizen gelten im erwarteten Kontext
    except Exception:
        pass
    diag.update(parametric=parametric, sheet_settings=sh, x_off_cm=x_off, existing_sheets=len(existing),
                sheets=[], mode='an Ort und Stelle' if plan else 'neu')
    parts_by_id = {p['id']: p for p in job.get('parts', [])}
    diag['component_occurrences_before'] = {pid: component_occurrences(design, info['comp'])
                                            for pid, info in ctx['parts'].items() if info.get('comp')}
    diag['existing_before'] = _safe(lambda: diagnose(design)['sheets'])
    diag['snapshots'] = []
    ins_cache = {}
    new_k = 0
    x_new = x_off                                   # Maschinen-Ursprung des nächsten neuen Rohteils (cm)
    for i, S in enumerate(result['sheets']):
        pe = plan['sheets'][i] if (plan and i < len(plan['sheets'])) else None   # gleiche Indizes wie plan_edit
        reuse = pe is not None and pe.get('occ') is not None
        util = S.get('utilisation', 0.0)
        gm = S['geom']
        shi = dict(sh, length=gm['length'], width=gm['width'], thickness=gm['thickness'], frame=gm['frame'],
                   grain=gm['grain'])
        Li, Wi, Ti = gm['length'] * MM, gm['width'] * MM, gm['thickness'] * MM
        mat = gm.get('material') or job.get('material') or ''
        turned = False
        trec = {}
        if reuse:                                   # Bearbeiten: vorhandenes Rohteil behalten
            sheet_occ = pe['occ']
            sheet_comp = sheet_occ.component
            no = pe.get('no') or (i + 1)
            name = _safe(lambda: sheet_comp.name) or G.sheet_name(no)
            try:
                sx = machine_origin_cm(sheet_occ, pe.get('old_attr') or {})
            except Exception:
                sx = x_new
            if pe.get('turn'):                      # längs <-> quer: Occurrence drehen, Teile drehen mit
                turned = _set_sheet_pose(sheet_occ, G.sheet_matrix(gm, sx), log, trec)
                stats['turned'] += 1 if turned else 0
            try:
                sheet_world = _coord_sys(sheet_occ.transform2)
            except Exception:
                sheet_world = G.sheet_matrix(gm, sx)
        else:
            no = start_no + new_k
            new_k += 1
            name = G.sheet_name(no)
            sx = x_new
            x_new += gm['W'] * MM + gap
            _cp('%s: addNewComponent', name)
            sheet_occ = root.occurrences.addNewComponent(_matrix(G.sheet_matrix(gm, sx)))   # Kind der Wurzel
            sheet_comp = sheet_occ.component
            sheet_comp.name = name
            try:
                sheet_world = _coord_sys(sheet_occ.transform2)
            except Exception:
                sheet_world = G.sheet_matrix(gm, sx)
        stats['abschnitte'] += 1 if gm['kind'] == 'abschnitt' else 0
        srec = {'name': name, 'sx_cm': sx, 'transform2': _axes_json(sheet_world), 'reused': reuse,
                'fullPathName': _safe(lambda: sheet_occ.fullPathName), 'parts': [], 'geom': gm,
                'label': G.sheet_label(gm, no), 'turn': trec or None}
        if S.get('label') and S['label'].split(': ', 1)[-1] != srec['label'].split(': ', 1)[-1]:
            srec['label_palette'] = S['label']          # Abweichung Palette/Fusion nur protokollieren
        diag['sheets'].append(srec)
        txt = sheet_text(gm, no, mat, util)
        old_a = (pe or {}).get('old_attr') or {}
        if reuse and not pe.get('resize'):
            srec['stock'] = 'behalten' + (' (gedreht)' if turned else '')
            if (old_a.get('grain') or 'X') != gm['grain']:
                _rebuild_grain(sheet_comp, gm, txt, log)
            elif not _update_grain_text(sheet_comp, txt):
                _rebuild_grain(sheet_comp, gm, txt, log)
        else:
            if reuse:
                _remove_stock(sheet_comp, log)
                stats['stock_rebuilt'] += 1
                srec['stock'] = 'neu aufgebaut (Plattenmaß geändert)'
            _build_stock(sheet_comp, shi, Li, Wi, Ti, txt, opts, log)
        # Teile: Matrix im Koordinatensystem der Rohteil-Komponente (Eltern der neuen Occurrence)
        placed = []
        for p in S['parts']:
            info = ctx['parts'].get(p['id'])
            prec = {'key': p.get('key'), 'id': p['id'], 'name': p.get('name'), 'x': p['x'], 'y': p['y'],
                    'rot': p['rot'], 'flip': bool(p.get('flip')), 'pinned': bool(p.get('pinned'))}
            srec['parts'].append(prec)
            if not info or not info.get('comp'):
                stats['missing'] += 1
                prec['error'] = 'Komponente fehlt'
                stats.setdefault('skipped', []).append({'name': p.get('name') or '?', 'key': p.get('key'),
                                                        'reason': 'Komponente fehlt'})
                continue
            local_axes = part_axes(info, p, gm['thickness'])
            world_axes = G.compose_axes(sheet_world, local_axes)
            jp = parts_by_id.get(p['id'])
            exp_bb = b2 = None
            if jp:
                q = G.place_2d(jp['outer'], p['rot'], bool(p.get('flip')), p['x'], p['y'])
                b2 = G.bbox2d(q)
                bw = G.bbox2d(_world_pts_mm(sheet_world, q))
                exp_bb = ([bw[0], bw[1], sheet_world[0][2] / MM], [bw[2], bw[3], sheet_world[0][2] / MM + gm['thickness']])
            prec.update(component=_safe(lambda: info['comp'].name), frame=info.get('frame'),
                        source=info.get('src'), analysis=info.get('diag'),
                        target_local=_axes_json(local_axes), target_world=_axes_json(world_axes),
                        expected_world_bbox_mm=exp_bb)
            occ = pe['match'].get(part_key(p)) if reuse else None
            if occ is not None:
                stats['kept'] += 1
                if _move_existing(occ, sheet_occ, local_axes, world_axes, prec, parametric):
                    stats['kept_moved'] += 1
            else:
                _cp('%s: Teil %s einfügen', name, p.get('name'))
                occ = insert_part(design, sheet_comp, sheet_occ, info['comp'], local_axes, world_axes, ins_cache, prec,
                                  parametric)
                if occ and plan:
                    stats['added'] += 1
            if not occ:
                stats['missing'] += 1
                prec.setdefault('error', 'addExistingComponent lieferte nichts')
                stats.setdefault('skipped', []).append({'name': prec.get('name') or info.get('name', '?'),
                                                        'key': p.get('key'), 'reason': prec['error']})
                continue
            prec['occurrence'] = _safe(lambda: occ.fullPathName)
            ins = prec.get('insertion') or {}
            if not ins.get('kept'):
                stats['native_ok' if ins.get('native_ok') else 'native_bad'] = \
                    stats.get('native_ok' if ins.get('native_ok') else 'native_bad', 0) + 1
            if ins.get('native_ok') is False and not ins.get('kept') and stats.get('native_bad') == 1:
                log.append('Einfügen: native Lage von %s nicht korrigierbar – nur Überschreibung, '
                           'kein Festsetzen' % prec.get('name'))
            prec['flags_after_create'] = {'isGroundToParent': _flag(occ, 'isGroundToParent'),
                                          'isGrounded': _flag(occ, 'isGrounded')}
            verify_part(occ, sheet_occ, local_axes, world_axes, prec, 'after_create', fix=False)
            prec['fusion_offset_component_mm'] = _offset_in_component(prec['after_create'].get('world_read'),
                                                                     world_axes)
            for k, v in (('sheet', no), ('part_id', p['id']), ('part_name', info.get('name', '')),
                         ('copy', _copy_of(p) + 1), ('area_mm2', round(p.get('area', 0), 1)),
                         ('dist_center_mm', round(p.get('dist', 0), 1)), ('center_x_mm', round(p.get('cx', 0), 2)),
                         ('center_y_mm', round(p.get('cy', 0), 2)), ('pos_x_mm', round(p['x'], 3)),
                         ('pos_y_mm', round(p['y'], 3)), ('rotation_deg', p['rot']), ('grain', p.get('grain', '')),
                         ('flipped', bool(p.get('flip'))), ('pinned', bool(p.get('pinned'))),
                         ('has_inner_contours', bool(p.get('hasHoles'))), ('small_part', bool(p.get('small'))),
                         ('critical_vacuum', bool(p.get('critical'))),
                         ('local_transform', [list(a) for a in local_axes]),
                         ('expected_bbox_sheet_mm', [[b2[0], b2[1]], [b2[2], b2[3]]] if b2 else None)):
                write_attr(occ, k, v if isinstance(v, str) else json.dumps(v))
            placed.append((occ, local_axes, world_axes, prec, exp_bb))
            stats['parts'] += 1
        # Rohteil festsetzen (Kind der Wurzel) – danach alle Teile zurücklesen und ggf. korrigieren
        try:
            if not (reuse and _flag(sheet_occ, 'isGrounded')):
                _cp('%s: isGrounded=True', name)
                sheet_occ.isGrounded = True
        except Exception as e:
            log.append('Rohteil festsetzen: %s' % e)
        srec['sheet_after_ground'] = _safe(lambda: _axes_json(_coord_sys(sheet_occ.transform2)))
        for occ, la, wa, prec, _bb in placed:
            st = verify_part(occ, sheet_occ, la, wa, prec, 'after_ground', fix=True)
            if st != 'ok':
                stats['moved'] += 1
                if st == 'fixed':
                    stats['corrected'] += 1
                if len([l for l in log if l.startswith('Lageabweichung')]) < 3:
                    r = prec['after_ground']
                    log.append('Lageabweichung %s: %.4f cm (%s)' % (prec.get('name'), r.get('dev_world_cm') or -1,
                                                                   'korrigiert' if st == 'fixed' else 'NICHT korrigiert'))
        _cp('%s: Position erfassen', name)
        snap = capture_positions(design, log, name)
        snap['parts_corrected'] = sum(1 for _o, _l, _w, pr, _b in placed if pr['after_ground'].get('status') == 'fixed')
        diag['snapshots'].append(snap)
        if snap.get('result') == 'Snapshot erstellt':
            stats['captured'] = stats.get('captured', 0) + snap['parts_corrected']
        zp = G.zero_point(gm)
        # length/width = Materialsystem der Komponente (wie bisher); machine_* = Tisch X/Y (1.10.0)
        write_attr(sheet_occ, 'sheet', {
            'index': no, 'length_mm': gm['length'], 'width_mm': gm['width'], 'thickness_mm': gm['thickness'],
            'material': mat, 'grain': gm['grain'], 'frame_mm': gm['frame'],
            'spacing_mm': sh['spacing'], 'utilisation': round(util, 4), 'parts': len(placed),
            'job_id': job.get('jobId', ''), 'project_id': opts.get('project_id', ''), 'project_sheet': i + 1,
            'project_version': opts.get('project_version', 0),
            'kind': gm['kind'], 'orient': gm['orient'], 'zero': gm['zero'], 'def_id': gm.get('id') or '',
            'machine_length_mm': gm['W'], 'machine_width_mm': gm['H'], 'grain_machine': gm['grain_axis'],
            'zero_box_point': G.zero_box_point(gm['zero']), 'zero_machine_mm': [zp['x'], zp['y'], zp['z']],
            'zero_local_mm': [zp['u'], zp['v'], zp['z']], 'label': G.sheet_label(gm, no)})
        if opts.get('project_id'):
            write_attr(sheet_occ, 'project_id', opts['project_id'])
            write_attr(sheet_occ, 'project_version', str(opts.get('project_version', 1)))
        stats['sheets'] += 1
        srec['_placed'] = placed
        srec['_sheet_occ'] = sheet_occ
    # Abschluss: alle Rohteile fertig -> nochmals zurücklesen (inkl. Welt-Hüllquader)
    for srec in diag['sheets']:
        placed = srec.pop('_placed', [])
        so = srec.pop('_sheet_occ', None)
        srec['sheet_final'] = _safe(lambda: _axes_json(_coord_sys(so.transform2)))
        srec['_so'] = so
        srec['_check'] = [(o_, la_, wa_, pr_) for o_, la_, wa_, pr_, _b in placed]
        for occ, la, wa, prec, exp_bb in placed:
            verify_part(occ, so, la, wa, prec, 'final', fix=False)
            wb = _world_bbox(occ, so)
            prec['world_bbox_mm'] = {'min': wb[0], 'max': wb[1]} if wb else None
            if wb and exp_bb:
                d = max(max(abs(a - b) for a, b in zip(wb[0], exp_bb[0])),
                        max(abs(a - b) for a, b in zip(wb[1], exp_bb[1])))
                dxy = max(max(abs(wb[0][i] - exp_bb[0][i]), abs(wb[1][i] - exp_bb[1][i])) for i in (0, 1))
                prec['bbox_dev_mm'] = d
                prec['bbox_dev_xy_mm'] = dxy
                if d > 1.0 and dxy <= 1.0:
                    # 2.0.1 (KMD-PC-03 arbeitsplatte, 3,0 mm): nur Z weicht ab = Teil dicker/dünner als das Rohteil
                    # (oben bündig) – Lage stimmt, kein Lagefehler
                    stats['thickness_mismatch'] = stats.get('thickness_mismatch', 0) + 1
                    log.append('%s: Dicke %.1f mm ≠ Rohteil %.1f mm (oben bündig, Lage in X/Y exakt)' % (
                        prec.get('name'), wb[1][2] - wb[0][2], exp_bb[1][2] - exp_bb[0][2]))
                elif d > 1.0:                    # Hüllquader weicht >1 mm ab -> Analyse/Lage prüfen
                    stats['bbox_mismatch'] += 1
                    if stats['bbox_mismatch'] <= 3:
                        log.append('Hüllquader %s weicht %.1f mm von der Vorschau ab' % (prec.get('name'), d))
    stats.setdefault('captured', 0)
    snap = capture_positions(design, log, 'Ende')
    diag['snapshots'].append(snap)
    # nach dem Erfassen: Welt-Lage prüfen; offen ist nur, was Fusion selbst als ausstehend meldet
    stats['not_captured'] = 1 if snap.get('pending_after') else 0
    entries = []
    for srec in diag['sheets']:
        so = srec.get('_so')
        for occ, la, wa, prec in srec.pop('_check', []):
            st = verify_part(occ, so, la, wa, prec, 'after_capture', fix=True)
            if st != 'ok':
                stats['not_captured'] += 1
            entries.append((occ, so, la, wa, prec))
        srec.pop('_so', None)
    if stats['not_captured']:
        diag['snapshots'].append(capture_positions(design, log, 'Ende 2'))
    fix_parts(design, entries, opts.get('fix', 'ground'), log, stats, diag)
    if any(s_.get('pending_after') for s_ in diag['snapshots'][-1:]):
        log.append('Fusion meldet noch nicht erfasste Positionen – bitte „Position erfassen“ ausführen')
    if tl is not None and tl_start is not None:
        try:
            if tl.count - 1 > tl_start:
                g = tl.timelineGroups.add(tl_start, tl.count - 1)
                g.name = 'LazyNest'
        except Exception:
            pass
    diag['stats'] = stats
    return stats


def _first_instance_world(design, comp):
    """Welt-Lage der ersten Instanz der Komponente (Fusion 2705: Einfüge-Versatz T_c, Log 10:10)."""
    try:
        occs = design.rootComponent.allOccurrencesByComponent(comp)
        if occs.count:
            return _coord_sys(occs.item(0).transform2)
    except Exception:
        pass
    return None


def insert_part(design, sheet_comp, sheet_occ, comp, la, wa, cache, prec, parametric=True, tol=1e-6):
    """Teil-Occurrence so einfügen, dass die NATIVE Lage (= Zeitachse, auf die Festsetzen/Neuberechnen
    zurückspringen) und die Welt-Lage stimmen. Fusion 2705 legt bei addExistingComponent(comp, M) in der Welt
    M·T_c ab (T_c = Welt-Lage der ersten Instanz, Rohteil-Lage ignoriert) – Korrekturen über den Proxy sind nur
    Überschreibungen und gehen beim Festsetzen verloren (Log 10:25). Deshalb Vorab-Ausgleich mit Prüfung:
      1) bewährtes Verfahren dieser Komponente (Cache)
      2) parametrisch: Ausgleich erste Instanz  M = W·T_c⁻¹      | direkt: normal M = lokal
      3) die jeweils andere Variante
      4) gemessen: D aus dem letzten Versuch (rechts: M⁻¹·A, links: A·M⁻¹), neu einfügen
    Jeder Fehlversuch wird gelöscht. Erfolg = Welt UND nativ (relativ zum Rohteil) exakt."""
    rec = {'attempts': []}
    prec['insertion'] = rec
    key = _safe(lambda: comp.id) or _safe(lambda: comp.name)
    T_c = _first_instance_world(design, comp)
    rec['first_instance_world'] = _axes_json(T_c) if T_c else None
    cands = []
    if key in cache:
        cands.append(cache[key])
    comp_m = ('Ausgleich erste Instanz', 'right', T_c) if T_c else None
    normal = ('normal (lokal)', 'local', None)
    for c in ((comp_m, normal) if parametric else (normal, comp_m)):
        if c and c not in cands:
            cands.append(c)
    last = None
    occ = None
    fallback = None
    for step in range(6):
        if cands:
            how, kind, D = cands.pop(0)
        elif last is not None and step < 6:
            M_prev, A_prev = last
            if not rec.get('_right_done'):
                rec['_right_done'] = True
                how, kind, D = 'gemessen (rechts)', 'right', G.compose_axes(G.invert_axes(M_prev), A_prev)
            elif not rec.get('_left_done'):
                rec['_left_done'] = True
                how, kind, D = 'gemessen (links)', 'left', G.compose_axes(A_prev, G.invert_axes(M_prev))
            else:
                break
        else:
            break
        if kind == 'local':
            M = la
        elif kind == 'right':
            M = G.compose_axes(wa, G.invert_axes(D))
        else:
            M = G.compose_axes(G.invert_axes(D), wa)
        try:
            occ = sheet_comp.occurrences.addExistingComponent(comp, _matrix(M))
        except Exception as e:
            rec['attempts'].append({'how': how, 'error': str(e)})
            occ = None
            continue
        loc, wld = _read_pose(occ, sheet_occ)
        dw = _axes_diff(wld, wa) if wld else None
        dl = _axes_diff(loc, la) if loc else None
        rec['attempts'].append({'how': how, 'dev_world_cm': dw, 'dev_native_cm': dl,
                                'offset_component_mm': _offset_in_component(_axes_json(wld), wa) if wld else None})
        if dw is not None and dw <= tol and (dl is None or dl <= tol):
            rec['method'] = how
            cache[key] = (how, kind, D)
            break
        if dw is not None and dw <= tol:
            # 2.0.0 (KMD-PC-03): Welt-Lage exakt -> annehmen, auch wenn die native Lage (Zeitachse) abweicht.
            # 2.0.1 (KMD-PC-03 12:33, gedrehtes Rohteil: 5/6 nativ falsch -> Gelenk-Rückfall): zuerst die übrigen
            # Varianten probieren (noch 1 Kandidat), die welt-exakte bleibt Rückfall. Nur 1 Zusatzversuch.
            if fallback is None and cands and step < 4:
                fallback = (how, kind, D, M, dl)
                try:
                    occ.deleteMe()
                    occ = None
                    rec['attempts'][-1]['kept_as_fallback'] = True
                    continue
                except Exception as e:
                    rec['attempts'][-1]['delete_error'] = str(e)
            rec['method'] = how + ' (Welt exakt, nativ %.4g cm)' % dl
            rec['native_dev_cm'] = dl
            cache[key] = (how, kind, D)
            fallback = None
            break
        if fallback is not None and not cands:
            # Zusatzversuch nicht besser -> löschen, welt-exakte Variante wieder einfügen
            try:
                occ.deleteMe()
                occ = None
                fhow, fkind, fD, fM, fdl = fallback
                occ = sheet_comp.occurrences.addExistingComponent(comp, _matrix(fM))
                rec['attempts'].append({'how': fhow + ' (Rückfall, erneut)'})
                rec['method'] = fhow + ' (Welt exakt, nativ %.4g cm)' % fdl
                rec['native_dev_cm'] = fdl
                cache[key] = (fhow, fkind, fD)
            except Exception as e:
                rec['attempts'].append({'how': 'Rückfall', 'error': str(e)})
            fallback = None
            break
        if wld is not None:
            last = (M, wld)
        if step < 5 and (cands or last is not None):
            try:
                occ.deleteMe()
                occ = None
                continue
            except Exception as e:
                rec['attempts'][-1]['delete_error'] = str(e)
                break
    rec.pop('_right_done', None)
    rec.pop('_left_done', None)
    if occ is None:                    # alles gescheitert -> wie früher einfügen, Proxy korrigiert später
        try:
            _cp('Teil %s: Rückfall addExistingComponent (lokal)', _safe(lambda: comp.name))
            occ = sheet_comp.occurrences.addExistingComponent(comp, _matrix(la))
            rec['method'] = 'FEHLGESCHLAGEN – nur Proxy-Korrektur'
        except Exception as e:
            # 2.0.0 (KMD-PC-03): ungeschützter Rückfall brach das ganze Übernehmen ab -> Teil überspringen
            rec['method'] = 'FEHLGESCHLAGEN – nicht eingefügt'
            rec['native_ok'] = False
            prec['error'] = 'Einfügen fehlgeschlagen: %s' % _short_err(e)
            if _linked_comp(design, comp):
                prec['error'] += ' (Komponente ist verknüpft – Baugruppenmodus aktivieren oder Verknüpfung lösen)'
            _L.warn('insert_part %s: %s', _safe(lambda: comp.name), prec['error'])
            return None
    rec.setdefault('method', 'FEHLGESCHLAGEN – nur Proxy-Korrektur')
    rec['native_ok'] = not rec['method'].startswith('FEHL') and rec.get('native_dev_cm') is None
    return occ


def _short_err(e):
    t = str(e).strip().splitlines()
    return (t[-1] if t else e.__class__.__name__)[:300]


def _linked_comp(design, comp):
    """True, wenn eine Instanz der Komponente in einer verknüpften Occurrence liegt."""
    try:
        occs = design.rootComponent.allOccurrencesByComponent(comp)
        for i in range(occs.count):
            if A.linked_parent(occs.item(i)) is not None:
                return True
    except Exception:
        pass
    return False


def _offset_in_component(read_json, target_axes):
    """Versatz Ist − Soll, ausgedrückt in Komponenten-Achsen (mm): Fusion 2705 liefert hier z. B. (0, 740, 0)."""
    try:
        o = read_json['origin']
        d = [a - b for a, b in zip(o, target_axes[0])]
        return [G.dot(d, ax) / MM for ax in (target_axes[1], target_axes[2], target_axes[3])]
    except Exception:
        return None


def _count(coll):
    c = coll.count
    return len(coll) if callable(c) else c


def _safe(fn):
    try:
        return fn()
    except Exception as e:
        return 'FEHLER: %s' % e


# ---------------------------------------------------------------- Diagnose (ändert nichts)
def diagnose(design, app=None):
    """Bestandsaufnahme aller ContourNest-Rohteile: Soll (Attribut local_transform) vs. Ist."""
    out = {'designType': _safe(lambda: 'parametrisch' if is_parametric(design) else 'direkt'),
           'root_occurrences': [], 'sheets': [], 'summary': {}}
    root = design.rootComponent
    for o in list(root.occurrences):
        out['root_occurrences'].append({'name': _safe(lambda: o.name), 'component': _safe(lambda: o.component.name),
                                        'transform2': _safe(lambda: _axes_json(_coord_sys(o.transform2))),
                                        'isGrounded': _flag(o, 'isGrounded'),
                                        'is_sheet': bool(_safe(lambda: o.attributes.itemByName(G.GROUP, 'sheet')))})
    nbad = nparts = 0
    for so in sheet_occurrences(root):
        sw = _coord_sys(so.transform2)
        srec = {'name': so.name, 'transform2': _axes_json(sw),
                'attr': _safe(lambda: json.loads(so.attributes.itemByName(G.GROUP, 'sheet').value)),
                'children': []}
        out['sheets'].append(srec)
        for occ in list(so.component.occurrences):
            r = {'name': _safe(lambda: occ.name), 'component': _safe(lambda: occ.component.name),
                 'isGroundToParent': _flag(occ, 'isGroundToParent'), 'isGrounded': _flag(occ, 'isGrounded'),
                 'bodies': _safe(lambda: _count(occ.component.bRepBodies)),
                 'sub_occurrences': _safe(lambda: _count(occ.component.occurrences))}
            attrs = {}
            for k in ('part_id', 'pos_x_mm', 'pos_y_mm', 'rotation_deg', 'flipped', 'pinned', 'local_transform',
                      'expected_bbox_sheet_mm'):
                a = _safe(lambda: occ.attributes.itemByName(G.GROUP, k))
                if a and not isinstance(a, str):
                    attrs[k] = _safe(lambda: json.loads(a.value) if a.value[:1] in '[{tf-0123456789' else a.value)
            r['attr'] = attrs
            loc, wld = _read_pose(occ, so)
            r['local_read'] = _axes_json(loc) if loc else None
            r['world_read'] = _axes_json(wld) if wld else None
            lt = attrs.get('local_transform')
            if isinstance(lt, list) and len(lt) == 4:
                exp_l = tuple(tuple(v) for v in lt)
                exp_w = G.compose_axes(sw, exp_l)
                r['dev_local_cm'] = _axes_diff(loc, exp_l) if loc else None
                r['dev_world_cm'] = _axes_diff(wld, exp_w) if wld else None
                nparts += 1
                if (r['dev_world_cm'] or 0) > 1e-4:
                    nbad += 1
            wb = _world_bbox(occ, so)
            r['world_bbox_mm'] = {'min': wb[0], 'max': wb[1]} if wb else None
            srec['children'].append(r)
    out['summary'] = {'sheets': len(out['sheets']), 'parts': nparts, 'parts_deviating': nbad}
    return out


def verify_and_repair(design, fix=True, log=None):
    """Nach dem Befehl: alle Rohteil-Teile gegen ihre gespeicherte Soll-Lage prüfen, ggf. per Proxy
    korrigieren und (mit Verlauf) als Snapshot erfassen. Rückgabe: Bericht (dict)."""
    log = log if log is not None else []
    rep = {'checked': 0, 'deviating': 0, 'fixed': 0, 'parts': []}
    root = design.rootComponent
    for so in sheet_occurrences(root):
        try:
            sw = _coord_sys(so.transform2)
        except Exception:
            continue
        for occ in list(so.component.occurrences):
            a = None
            try:
                a = occ.attributes.itemByName(G.GROUP, 'local_transform')
            except Exception:
                pass
            if not a:
                continue
            try:
                la = tuple(tuple(v) for v in json.loads(a.value))
            except Exception:
                continue
            wa = G.compose_axes(sw, la)
            rec = {'sheet': _safe(lambda: so.name), 'occurrence': _safe(lambda: occ.name)}
            st = verify_part(occ, so, la, wa, rec, 'check', fix=fix)
            rep['checked'] += 1
            if st != 'ok':
                rep['deviating'] += 1
                if st == 'fixed':
                    rep['fixed'] += 1
                rep['parts'].append(rec)
    rep['snapshot'] = capture_positions(design, log, 'Nachprüfung')
    return rep


# ---------------------------------------------------------------- 2.0.0 Baugruppenmodus: Körper-Kopien
COPY_ROOT = 'LazyNest Kopien'
COPY_ATTR = 'copy_source'
COPY_ROOT_ATTR = 'copy_root'


def _identity():
    return adsk.core.Matrix3D.create()


def copy_root(design, create=True):
    """Unsichtbare Sammel-Komponente „LazyNest Kopien“ unter der Wurzel (Attribut ContourNest/copy_root)."""
    root = design.rootComponent
    for i in range(root.occurrences.count):
        o = root.occurrences.item(i)
        if _attr_value(o, COPY_ROOT_ATTR):
            return o
    if not create:
        return None
    _cp('Baugruppenmodus: Sammelkomponente anlegen')
    o = root.occurrences.addNewComponent(_identity())
    o.component.name = COPY_ROOT
    write_attr(o, COPY_ROOT_ATTR, '1')
    try:
        o.isLightBulbOn = False
    except Exception:
        pass
    return o


def copy_components(design):
    """{Quellschlüssel: Komponente} aller vorhandenen Kopien."""
    out = {}
    ro = copy_root(design, create=False)
    if ro is None:
        return out
    cc = ro.component
    for i in range(cc.occurrences.count):
        comp = cc.occurrences.item(i).component
        a = _safe_json(lambda: comp.attributes.itemByName(G.GROUP, COPY_ATTR).value)
        if isinstance(a, dict) and a.get('key'):
            out[a['key']] = comp
    return out


def _safe_json(fn):
    try:
        return json.loads(fn())
    except Exception:
        return None


def _add_body_copy(comp, src_body, parametric, name):
    """Temporäre Kopie des nativen Quellkörpers als Körper in comp (parametrisch: in einem Basis-Element)."""
    tmp = adsk.fusion.TemporaryBRepManager.get().copy(src_body)
    if tmp is None:
        raise RuntimeError('TemporaryBRepManager.copy lieferte nichts')
    if parametric:
        bf = comp.features.baseFeatures.add()
        bf.startEdit()
        try:
            nb = comp.bRepBodies.add(tmp, bf)
        finally:
            bf.finishEdit()
        try:
            bf.name = 'LazyNest Kopie'
        except Exception:
            pass
        # nach finishEdit gilt der Körper der Komponente (nicht der Quellkörper im Basis-Element)
        nb = comp.bRepBodies.item(comp.bRepBodies.count - 1) if comp.bRepBodies.count else nb
    else:
        nb = comp.bRepBodies.add(tmp)
    if nb is None:
        raise RuntimeError('bRepBodies.add lieferte nichts')
    try:
        nb.name = name
    except Exception:
        pass
    try:
        if src_body.material is not None:
            nb.material = src_body.material
    except Exception:
        pass
    return nb


def make_copies(design, groups, log):
    """Baugruppenmodus: je Gruppe (ln_assembly.collect) eine lokale Komponente mit Körper-Kopie.
    Vorhandene Kopie mit gleichem Quellschlüssel wird wiederverwendet. -> [(group, comp, body | None, fehler)]
    2.0.13: Fehler beim Anlegen der Sammelkomponente (z. B. CIRCULAR_DEPENDENCY) nur einmal versuchen."""
    parametric = is_parametric(design)
    have = copy_components(design)
    out = []
    ro = None
    root_err = None
    for g in groups:
        comp = have.get(g['key'])
        try:
            if comp is None:
                if root_err:
                    raise RuntimeError(root_err)
                try:
                    ro = ro or copy_root(design)
                except Exception as e:
                    root_err = _short_err(e)
                    raise
                if ro is None:
                    root_err = 'Sammelkomponente konnte nicht angelegt werden'
                    raise RuntimeError(root_err)
                _cp('Baugruppenmodus: Kopie %s anlegen', g.get('name'))
                occ = ro.component.occurrences.addNewComponent(_identity())
                comp = occ.component
                comp.name = g['name']
                _add_body_copy(comp, g['body'], parametric, g.get('body_name') or g['name'])
                log.append('Kopie angelegt: %s (%d×)' % (g['name'], g['count']))
            body = next((b for b in comp.bRepBodies if b.isSolid), None)
            write_attr(comp, COPY_ATTR, {
                'key': g['key'], 'component': A._g(lambda: g['comp'].name, ''), 'body': g.get('body_name'),
                'name': g['name'], 'paths': g.get('paths') or [], 'count': g.get('count', 1),
                'linked': g.get('linked'), 'created': _L.stamp()})
            out.append((g, comp, body, ''))
        except Exception as e:
            _L.exc('Kopie %s' % g.get('name'))
            msg = _short_err(e)
            if A.is_circular_dependency(e) or A.is_circular_dependency(msg):
                root_err = root_err or msg
            out.append((g, None, None, 'Kopie fehlgeschlagen: %s' % msg))
    return out


def _find_occ_by_path(design, path):
    try:
        occs = design.rootComponent.allOccurrences
        for i in range(occs.count):
            o = occs.item(i)
            if o.fullPathName == path:
                return o
    except Exception:
        pass
    return None


def refresh_copies(design, log):
    """„Kopien aktualisieren“: Geometrie aus der Quelle neu lesen, Komponenten (und damit alle platzierten
    Instanzen in den Rohteilen) bleiben -> Lagen bleiben erhalten. -> Bericht."""
    parametric = is_parametric(design)
    rep = {'updated': 0, 'failed': [], 'outdated': [], 'missing': []}
    for key, comp in copy_components(design).items():
        a = _safe_json(lambda: comp.attributes.itemByName(G.GROUP, COPY_ATTR).value) or {}
        src_occ = None
        for pth in a.get('paths') or []:
            src_occ = _find_occ_by_path(design, pth)
            if src_occ is not None:
                break
        if src_occ is None:
            rep['missing'].append(a.get('name') or comp.name)
            continue
        lk = A.linked_parent(src_occ)
        if lk is not None and A._g(lambda: lk.documentReference.isOutOfDate, False):
            rep['outdated'].append(A._g(lambda: lk.name, '?'))
        src = None
        for b in src_occ.component.bRepBodies:
            if b.name == a.get('body') and b.isSolid:
                src = b
                break
        if src is None:
            rep['missing'].append('%s (Körper %s)' % (a.get('name') or comp.name, a.get('body')))
            continue
        try:
            _cp('Kopie %s aktualisieren', comp.name)
            tmp = adsk.fusion.TemporaryBRepManager.get().copy(src)
            bfs = comp.features.baseFeatures if parametric else None
            if parametric and bfs is not None and bfs.count:
                bf = bfs.item(bfs.count - 1)
                bf.startEdit()
                try:
                    ok = bf.updateBody(bf.bodies.item(0), tmp)
                finally:
                    bf.finishEdit()
                if not ok:
                    raise RuntimeError('BaseFeature.updateBody lieferte False')
            else:
                old = next((b for b in comp.bRepBodies if b.isSolid), None)
                nb = comp.bRepBodies.add(tmp)
                if old is not None:
                    nm = old.name
                    old.deleteMe()
                    nb.name = nm
            a['linked'] = A.link_info(lk) if lk is not None else None
            a['refreshed'] = _L.stamp()
            write_attr(comp, COPY_ATTR, a)
            rep['updated'] += 1
        except Exception as e:
            _L.exc('Kopie aktualisieren %s' % comp.name)
            rep['failed'].append('%s: %s' % (comp.name, _short_err(e)))
    log.append('Kopien aktualisiert: %d, fehlgeschlagen %d, Quelle fehlt %d, Quelle veraltet %d'
               % (rep['updated'], len(rep['failed']), len(rep['missing']), len(rep['outdated'])))
    return rep
