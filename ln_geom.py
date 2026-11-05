# -*- coding: utf-8 -*-
"""ContourNest – reine Geometrie-/Datenfunktionen (ohne Fusion-API, headless testbar).

Einheiten: Fusion intern cm; Nesting-Daten (JSON) in mm.
"""
import math

MM = 0.1          # 1 mm in cm
GROUP = 'ContourNest'

# Benutzerparameter: name -> (einheit, standardwert in Anzeigeeinheit, kommentar)
PARAMS = {
    'nest_laenge':        ('mm', 2800.0, 'LazyNest: Rohteil Länge'),
    'nest_breite':        ('mm', 2070.0, 'LazyNest: Rohteil Breite'),
    'nest_dicke':         ('mm', 19.0,   'LazyNest: Rohteil Dicke'),
    'nest_rand':          ('mm', 10.0,   'LazyNest: Randbesäumung'),
    'nest_abstand':       ('mm', 10.0,   'LazyNest: Teileabstand (z. B. Fräserdurchmesser)'),
    'nest_rohteil_abstand': ('mm', 200.0, 'LazyNest: Abstand zwischen Rohteilen in der Konstruktion'),
    'nest_dicken_tol':    ('mm', 0.5,    'LazyNest: Toleranz Teiledicke'),
    'nest_drehschritt':   ('deg', 90.0,  'LazyNest: Drehschritt freie Teile (0 = keine Drehung)'),
    'nest_faser_y':       ('', 0.0,      'LazyNest: Faserrichtung Platte 0 = X (Länge), 1 = Y (Breite)'),
    'nest_max_rohteile':  ('', 5.0,      'LazyNest: max. Anzahl Rohteile'),
    'nest_rechenzeit':    ('', 60.0,     'LazyNest: Rechenzeit in s'),
    'nest_generationen':  ('', 30.0,     'LazyNest: max. Generationen des GA'),
    'nest_population':    ('', 10.0,     'LazyNest: Populationsgröße des GA'),
    'nest_vakuum_gewicht': ('', 0.0,     'LazyNest: 0 = Ausnutzung, 100 = kleine Teile mittig'),
    'nest_vakuum_flaeche': ('', 100.0,   'LazyNest: Mindestfläche Vakuum in cm²'),
}

ROT_STEPS = [('90°', 90.0), ('45°', 45.0), ('30°', 30.0), ('15°', 15.0), ('10°', 10.0), ('5°', 5.0),
             ('keine Drehung', 0.0)]
GRAIN_MODES = [('längs', 'laengs'), ('quer', 'quer'), ('frei', 'frei')]


# ---------------------------------------------------------------- Vektoren
def sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def mul(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def length(a): return math.sqrt(dot(a, a))


def unit(a):
    n = length(a)
    if n < 1e-12:
        raise ValueError('Nullvektor')
    return (a[0] / n, a[1] / n, a[2] / n)


def any_perp(n):
    """Beliebiger Einheitsvektor senkrecht zu n."""
    ref = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    return unit(cross(cross(n, ref), n))


# ---------------------------------------------------------------- 2D-Polygone
def area2d(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def clean_ring(pts, eps=1e-4):
    """Doppelte/aufeinanderfolgende gleiche Punkte entfernen, Ring nicht schließen."""
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append((p[0], p[1]))
    while len(out) > 1 and abs(out[0][0] - out[-1][0]) <= eps and abs(out[0][1] - out[-1][1]) <= eps:
        out.pop()
    return out


def orient_ring(pts, ccw=True):
    return list(pts) if (area2d(pts) > 0) == ccw else list(reversed(pts))


def convex_hull(pts):
    p = sorted(set((round(x, 6), round(y, 6)) for x, y in pts))
    if len(p) < 3:
        return p

    def cr(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for q in p:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    for q in reversed(p):
        while len(up) >= 2 and cr(up[-2], up[-1], q) <= 0:
            up.pop()
        up.append(q)
    return lo[:-1] + up[:-1]


def min_area_rect(pts):
    """Minimal umschließendes Rechteck: (winkel_grad_der_langen_seite in [0,180), lang, kurz)."""
    h = convex_hull(pts)
    if len(h) < 3:
        return 0.0, 0.0, 0.0
    best = None
    for i in range(len(h)):
        x1, y1 = h[i]
        x2, y2 = h[(i + 1) % len(h)]
        a = math.atan2(y2 - y1, x2 - x1)
        c, s = math.cos(a), math.sin(a)
        us = [x * c + y * s for x, y in h]
        vs = [-x * s + y * c for x, y in h]
        w, hh = max(us) - min(us), max(vs) - min(vs)
        if best is None or w * hh < best[0] - 1e-9:
            best = (w * hh, a, w, hh)
    _, a, w, hh = best
    ang = math.degrees(a)
    if hh > w:
        ang += 90.0
        w, hh = hh, w
    return ang % 180.0, w, hh


def grain_angle(outer, straight_segments=None, min_ratio=0.0):
    """Faserrichtung eines Teils in Grad [0,180) im lokalen 2D-System.

    straight_segments: Liste ((x1,y1),(x2,y2)) der geraden Kanten der Außenkontur.
    Regel: Richtung der längsten geraden Kante; ohne gerade Kanten Längsachse des minimalen
    umschließenden Rechtecks. Liefert (winkel, quelle)."""
    best = None
    for (a, b) in (straight_segments or []):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if best is None or L > best[0] + 1e-6:
            best = (L, a, b)
    if best and best[0] > min_ratio:
        a, b = best[1], best[2]
        return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0, 'längste gerade Kante'
    ang, _, _ = min_area_rect(outer)
    return ang, 'Längsachse Hüllrechteck'


def allowed_rotations(mode, part_grain_deg, sheet_grain, step):
    """Gleiche Regel wie in nest_engine.js (für Tests/Anzeige)."""
    G = 90.0 if sheet_grain == 'Y' else 0.0
    if mode == 'laengs':
        b = G - part_grain_deg
        return sorted({round(b % 360, 6), round((b + 180) % 360, 6)})
    if mode == 'quer':
        b = G + 90 - part_grain_deg
        return sorted({round(b % 360, 6), round((b + 180) % 360, 6)})
    if not step or step <= 0:
        return [0.0]
    n = max(1, int(round(360.0 / step)))
    return [round(k * 360.0 / n, 6) for k in range(n)]


# ---------------------------------------------------------------- Transformation
def placement_axes(o, e1, e2, n, rot_deg, tx_mm, ty_mm, z_top_mm):
    """Lage einer Teil-Occurrence im Rohteil-Koordinatensystem (cm).

    Lokales Ablagesystem des Teils (Komponentenkoordinaten, cm): Ursprung o, Achsen e1, e2
    in der Ablagefläche, n = Flächennormale (nach außen), e1 x e2 = n.
    Ein Komponentenpunkt p wird abgebildet auf
        X = cos*u - sin*v + tx,  Y = sin*u + cos*v + ty,  Z = w + z_top
    mit u=(p-o)·e1, v=(p-o)·e2, w=(p-o)·n. Die Ablagefläche liegt damit in Z = z_top
    (Oberseite Rohteil), der Körper reicht nach unten in die Platte.
    Rückgabe (origin, xAxis, yAxis, zAxis) für Matrix3D.setWithCoordinateSystem: Bilder des
    Komponenten-Ursprungs und der Komponenten-Achsen."""
    r = math.radians(rot_deg)
    c, s = math.cos(r), math.sin(r)
    r1 = sub(mul(e1, c), mul(e2, s))
    r2 = add(mul(e1, s), mul(e2, c))
    r3 = n
    t = (-dot(r1, o) + tx_mm * MM, -dot(r2, o) + ty_mm * MM, -dot(r3, o) + z_top_mm * MM)
    xa = (r1[0], r2[0], r3[0])
    ya = (r1[1], r2[1], r3[1])
    za = (r1[2], r2[2], r3[2])
    return t, xa, ya, za


def compose_axes(parent, child):
    """Achsen-Tupel verketten: Welt = parent ∘ child (child relativ zu parent)."""
    t, xa, ya, za = parent
    def lin(v):
        return add(mul(xa, v[0]), add(mul(ya, v[1]), mul(za, v[2])))
    ct, cx, cy, cz = child
    return (add(t, lin(ct)), lin(cx), lin(cy), lin(cz))


def flip_frame(frame):
    """Ablage-System eines gewendeten Teils: 180° um e1 gedreht (e2 -> -e2, n -> -n)."""
    o, e1, e2, n = frame
    return o, e1, mul(e2, -1), mul(n, -1)


def invert_axes(a):
    """Inverse einer starren Transformation (origin, x, y, z)."""
    t, xa, ya, za = a
    ix = (xa[0], ya[0], za[0])
    iy = (xa[1], ya[1], za[1])
    iz = (xa[2], ya[2], za[2])
    it = (-dot(xa, t), -dot(ya, t), -dot(za, t))
    return (it, ix, iy, iz)


def apply_axes(axes, p):
    """Punkt mit (origin, x, y, z) transformieren – zum Testen."""
    t, xa, ya, za = axes
    return add(t, add(mul(xa, p[0]), add(mul(ya, p[1]), mul(za, p[2]))))


def place_2d(pts, rot_deg, flip, x, y):
    """Wie placePts der Engine: (gespiegelt (u,-v)) -> gedreht -> verschoben."""
    r = math.radians(rot_deg)
    c, s = math.cos(r), math.sin(r)
    out = []
    for u, v in pts:
        if flip:
            v = -v
        out.append((c * u - s * v + x, s * u + c * v + y))
    return out


def bbox2d(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys)) if pts else None


# ---------------------------------------------------------------- Job / Ergebnis
def sheet_name(i):
    return 'Rohteil %02d' % i


def build_job(settings, parts, job_id):
    """settings: dict (mm); parts: Liste dicts mit id, name, outer, holes, grainAngle, grainMode, qty, enabled."""
    return {
        'jobId': job_id,
        'material': settings.get('material', ''),
        'sheet': {
            'length': settings['length'], 'width': settings['width'], 'thickness': settings['thickness'],
            'frame': settings['frame'], 'spacing': settings['spacing'], 'grain': settings['grain'],
        },
        'maxSheets': int(settings['max_sheets']),
        'rotStep': float(settings['rot_step']),
        'vacuumWeight': float(settings['vac_weight']),
        'minVacArea': float(settings['vac_area_cm2']) * 100.0,
        'population': int(settings.get('population', 10)),
        'maxGenerations': int(settings['generations']),
        'timeLimit': float(settings['time_limit']),
        'parts': [p for p in parts if p.get('enabled', True) and p.get('qty', 0) > 0],
        'featView': 'all' if settings.get('feat_view') == 'all' else 'top',
    }


def validate_settings(s):
    """Liefert Liste deutscher Fehlermeldungen."""
    e = []
    if s['length'] <= 0 or s['width'] <= 0:
        e.append('Länge und Breite des Rohteils müssen > 0 sein.')
    if s['thickness'] <= 0:
        e.append('Dicke muss > 0 sein.')
    if s['frame'] < 0 or s['spacing'] < 0:
        e.append('Randbesäumung und Teileabstand dürfen nicht negativ sein.')
    if 2 * s['frame'] + s['spacing'] >= min(s['length'], s['width']):
        e.append('Randbesäumung/Teileabstand zu groß für das Rohteil.')
    if s['max_sheets'] < 1:
        e.append('Mindestens 1 Rohteil.')
    return e


def part_fits_sheet(outer, s, rotations):
    """Grobprüfung: passt das (um Abstand vergrößerte) Hüllrechteck in einer erlaubten Drehung?"""
    ux = s['length'] - 2 * s['frame'] - s['spacing']
    uy = s['width'] - 2 * s['frame'] - s['spacing']
    for r in rotations:
        a = math.radians(r)
        c, si = math.cos(a), math.sin(a)
        xs = [x * c - y * si for x, y in outer]
        ys = [x * si + y * c for x, y in outer]
        if max(xs) - min(xs) <= ux + 1e-6 and max(ys) - min(ys) <= uy + 1e-6:
            return True
    return False


def de(x, d=1):
    """Zahl deutsch formatieren."""
    s = ('{:,.%df}' % d).format(x)
    return s.replace(',', '§').replace('.', ',').replace('§', '.')


# ---------------------------------------------------------------- Rohteile je Blatt (1.10.0)
# Gleiche Regeln wie palette/nest_engine.js (resolveSheet, zeroPoint, sheetLabel, toMaterial).
# Palette/Projekt: Maschinentisch-Koordinaten (X rechts, Y hinten). Fusion-Komponente „Rohteil NN“:
# Materialsystem u (Plattenlänge) / v (Plattenbreite). quer: Maschine (x, y) = (Breite − v, u).
ZERO_CORNER_TXT = {'lu': 'unten links', 'ru': 'unten rechts', 'lh': 'oben links', 'rh': 'oben rechts', 'mitte': 'Mitte'}
ZERO_SIDE_TXT = {'unten': 'Unterseite', 'oben': 'Oberseite'}
ZERO_KEYS = ['%s_%s' % (c, s) for c in ('lu', 'ru', 'lh', 'rh', 'mitte') for s in ('unten', 'oben')]
# Fusion SetupWCSPoint: 1 = Xmin/Ymin, 2 = Xmax/Ymin, 3 = Xmin/Ymax, 4 = Xmax/Ymax (Maschinensystem = Modell)
_BOX_NO = {'lu': '1', 'ru': '2', 'lh': '3', 'rh': '4'}


def valid_zero(z):
    return z if z in ZERO_KEYS else None


def zero_box_point(key):
    """Nullpunkt-Schlüssel -> Fusion wcs_origin_boxPoint ('bottom 1' … 'top center')."""
    key = valid_zero(key) or 'lu_unten'
    c, s = key.split('_')
    side = 'top' if s == 'oben' else 'bottom'
    return '%s %s' % (side, 'center' if c == 'mitte' else _BOX_NO[c])


def zero_text(key):
    key = valid_zero(key) or 'lu_unten'
    c, s = key.split('_')
    return '%s, %s' % (ZERO_CORNER_TXT[c], ZERO_SIDE_TXT[s])


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def resolve_sheet(defn, std, material='', default_zero=None):
    """Blatt-Definition + Standardformat -> vollständiges Blatt (mm). W/H = Maschine X/Y."""
    d = defn if isinstance(defn, dict) else {}
    s = std or {}
    ab = d.get('kind') == 'abschnitt'

    def pick(k, ok):
        v = _num(d.get(k))
        return v if (ab and v is not None and ok(v)) else (_num(s.get(k)) or 0.0)
    length = pick('length', lambda v: v > 0)
    width = pick('width', lambda v: v > 0)
    frame = pick('frame', lambda v: v >= 0)
    thickness = pick('thickness', lambda v: v > 0)
    grain = d.get('grain') if (ab and d.get('grain') in ('X', 'Y')) else ('Y' if s.get('grain') == 'Y' else 'X')
    quer = d.get('orient') == 'quer'
    g_axis = 'Y' if ((grain == 'Y') != quer) else 'X'
    return {'id': d.get('id') or '', 'kind': 'abschnitt' if ab else 'standard', 'length': length, 'width': width,
            'thickness': thickness, 'frame': frame, 'grain': grain,
            'material': str(d.get('material')) if (ab and d.get('material')) else (material or ''),
            'orient': 'quer' if quer else 'laengs',
            'zero': valid_zero(d.get('zero')) or valid_zero(default_zero) or 'lu_unten',
            'W': width if quer else length, 'H': length if quer else width, 'grain_axis': g_axis}


def zero_point(r):
    """Nullpunkt: Maschine (x, y, z) und Lage im Materialsystem (u, v) der Fusion-Komponente, mm."""
    key = valid_zero(r.get('zero')) or 'lu_unten'
    c, sd = key.split('_')
    x = r['W'] if c in ('ru', 'rh') else (r['W'] / 2.0 if c == 'mitte' else 0.0)
    y = r['H'] if c in ('lh', 'rh') else (r['H'] / 2.0 if c == 'mitte' else 0.0)
    u, v = (y, r['width'] - x) if r.get('orient') == 'quer' else (x, y)
    return {'key': key, 'corner': c, 'side': sd, 'x': x, 'y': y, 'z': r['thickness'] if sd == 'oben' else 0.0,
            'u': u, 'v': v}


def de_num(x):
    """wie nest_engine.deNum: höchstens eine Nachkommastelle, Komma, ohne Tausenderpunkt."""
    r = math.floor(float(x) * 10.0 + 0.5) / 10.0      # wie JS Math.round (halbe aufwärts)
    a = abs(r)
    ip = int(math.floor(a + 1e-9))
    fr = int(round((a - ip) * 10))
    return ('-' if r < 0 else '') + str(ip) + (',%d' % fr if fr else '')


def sheet_label(r, no):
    """Beschriftung des Rohteils (Faserskizze in Fusion, Palette): Maß im Maschinensystem + Status."""
    return 'Rohteil %02d: %s × %s × %s mm, %s, Faser %s, %s | Nullpunkt: %s' % (
        int(no), de_num(r['W']), de_num(r['H']), de_num(r['thickness']), 'quer' if r['orient'] == 'quer' else 'längs',
        r['grain_axis'], 'Abschnitt' if r['kind'] == 'abschnitt' else 'Standard', zero_text(r['zero']))


def to_material(p, r):
    """Teillage (x, y, rot) Maschine -> Materialsystem (Umkehrung der Blattdrehung bei „quer“)."""
    x, y, rot = float(p['x']), float(p['y']), float(p['rot'])
    if r.get('orient') != 'quer':
        return x, y, rot % 360.0
    return y, r['W'] - x, (rot - 90.0) % 360.0


def to_machine(u, v, r):
    """Punkt Materialsystem -> Maschine."""
    if r.get('orient') != 'quer':
        return u, v
    return r['width'] - v, u


def material_result(result, std, material='', default_zero=None):
    """Paletten-Ergebnis für Fusion: leere Blätter entfallen, je Blatt die aufgelöste Definition ('geom') und die
    Teillagen im Materialsystem. Original bleibt unverändert (Projekt speichert Maschinen-Lagen)."""
    import copy
    if result.get('_material'):
        return result
    out = copy.deepcopy(result)
    sheets = []
    for S in out.get('sheets') or []:
        if not S.get('parts'):
            continue
        gm = resolve_sheet(S.get('def'), std, material, default_zero if default_zero is not None
                           else result.get('defaultZero'))
        for p in S['parts']:
            p['machine'] = {'x': p['x'], 'y': p['y'], 'rot': p['rot']}
            p['x'], p['y'], p['rot'] = to_material(p, gm)
        S['geom'] = gm
        sheets.append(S)
    out['sheets'] = sheets
    out['_material'] = True
    return out


def sheet_matrix(gm, sx_cm):
    """Lage der Rohteil-Occurrence (Achsen in cm): Maschinen-Ursprung bei (sx, 0, 0); quer = 90° um Z gedreht,
    Materialursprung dann bei x = sx + Breite."""
    if gm.get('orient') == 'quer':
        return ((sx_cm + gm['width'] * MM, 0.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    return ((sx_cm, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


# ---------------------------------------------------------------- 2.0.4: Prüfungen der finalen Anordnung
def _seg_dist2(p, q, a, b):
    """Quadrat des Abstands zweier Strecken pq und ab (2D)."""
    def pt_seg(c, s0, s1):
        dx, dy = s1[0] - s0[0], s1[1] - s0[1]
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 <= 0 else max(0.0, min(1.0, ((c[0] - s0[0]) * dx + (c[1] - s0[1]) * dy) / L2))
        ex, ey = s0[0] + t * dx - c[0], s0[1] + t * dy - c[1]
        return ex * ex + ey * ey

    def ccw(A, B, C):
        return (C[1] - A[1]) * (B[0] - A[0]) - (B[1] - A[1]) * (C[0] - A[0])
    d1, d2, d3, d4 = ccw(a, b, p), ccw(a, b, q), ccw(p, q, a), ccw(p, q, b)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 and d3 and d4:
        return 0.0
    return min(pt_seg(p, a, b), pt_seg(q, a, b), pt_seg(a, p, q), pt_seg(b, p, q))


def _ring_edges(r):
    n = len(r)
    out = []
    for i in range(n):
        p, q = r[i], r[(i + 1) % n]
        out.append((p, q, min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1])))
    return out


def _rings_closer(ea, eb, lim):
    """True, wenn zwei Ringe (Kantenlisten) näher als lim liegen (oder sich schneiden)."""
    lim2 = lim * lim
    for p, q, ax0, ay0, ax1, ay1 in ea:
        for a, b, bx0, by0, bx1, by1 in eb:
            if bx0 > ax1 + lim or ax0 > bx1 + lim or by0 > ay1 + lim or ay0 > by1 + lim:
                continue
            if _seg_dist2(p, q, a, b) < lim2:
                return True
    return False


def point_in_ring(pt, ring):
    x, y = pt
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / ((y2 - y1) or 1e-300)
            if x < xi:
                inside = not inside
    return inside


def _inside_solid(pt, shape):
    """Punkt im Material eines Teils (in der Außenkontur, in keinem Loch)?"""
    if not point_in_ring(pt, shape['outer']):
        return False
    return not any(point_in_ring(pt, h) for h in shape['holes'])


def placed_shapes(sheet, parts_by_id):
    """Konturen der Teile eines Ergebnis-Blatts (Maschinensystem, mm) – wie placePts der Engine."""
    out = []
    for p in sheet.get('parts') or []:
        t = parts_by_id.get(p.get('id'))
        if not t or not t.get('outer'):
            continue
        rot, flip = float(p.get('rot', 0)), bool(p.get('flip'))
        x, y = float(p.get('x', 0)), float(p.get('y', 0))
        outer = place_2d(t['outer'], rot, flip, x, y)
        holes = [place_2d(h, rot, flip, x, y) for h in (t.get('holes') or []) if len(h) >= 3]
        out.append({'key': p.get('key') or '%s#%s' % (p.get('id'), p.get('copy', 0)), 'id': p.get('id'),
                    'name': p.get('name') or t.get('name') or p.get('id'), 'outer': outer, 'holes': holes,
                    'bb': bbox2d(outer), 'edges': _ring_edges(outer),
                    'hedges': [_ring_edges(h) for h in holes]})
    return out


def placement_conflicts(result, job, tol=0.05):
    """2.0.4: unabhängige Prüfung der FINALEN Anordnung (Paletten-Ergebnis, Maschinensystem, mm).
    Je Blatt: Teil-Teil-Abstand >= Teileabstand, Teil-Rand >= Randbesäumung + ½ Teileabstand (Regeln wie
    layoutConflicts der Engine), keine Überlappung (auch Teil komplett im Material eines anderen).
    Liefert Liste {sheet, key, other, kind ('Überlappung'|'Abstand'|'Rand'), name}."""
    job = job or {}
    std = job.get('sheet') or {}
    s = float(std.get('spacing') or 0.0)
    pb = {p.get('id'): p for p in job.get('parts') or []}
    out = []
    for si, S in enumerate((result or {}).get('sheets') or []):
        if not S.get('parts'):
            continue
        gm = resolve_sheet(S.get('def'), std, job.get('material', ''))
        W, H, f = gm['W'], gm['H'], gm['frame']
        shapes = placed_shapes(S, pb)
        e = f + s / 2.0 - tol
        for a in shapes:
            x0, y0, x1, y1 = a['bb']
            if x0 < e or y0 < e or x1 > W - e or y1 > H - e:
                out.append({'sheet': si, 'key': a['key'], 'other': None, 'kind': 'Rand', 'name': a['name']})
        lim = max(0.0, s - 2 * tol)
        for i in range(len(shapes)):
            A_ = shapes[i]
            for j in range(i + 1, len(shapes)):
                B_ = shapes[j]
                ab, bb = A_['bb'], B_['bb']
                if bb[0] > ab[2] + lim or ab[0] > bb[2] + lim or bb[1] > ab[3] + lim or ab[1] > bb[3] + lim:
                    continue
                kind = None
                if _inside_solid(B_['outer'][0], A_) or _inside_solid(A_['outer'][0], B_):
                    kind = 'Überlappung'
                elif _rings_closer(A_['edges'], B_['edges'], max(lim, 1e-9)):
                    kind = 'Abstand' if lim > 0 else 'Überlappung'
                else:
                    for he in A_['hedges']:
                        if _rings_closer(he, B_['edges'], max(lim, 1e-9)):
                            kind = 'Abstand'
                    for he in B_['hedges']:
                        if _rings_closer(he, A_['edges'], max(lim, 1e-9)):
                            kind = 'Abstand'
                if kind:
                    out.append({'sheet': si, 'key': A_['key'], 'other': B_['key'], 'kind': kind,
                                'name': '%s / %s' % (A_['name'], B_['name'])})
    return out


def next_sheet_x(sheet_specs, gap_cm, fallback_len_cm):
    """2.0.4: Maschinen-Ursprung (cm) für ein NEUES Rohteil: hinter ALLEN vorhandenen Rohteilen
    (behaltene des bearbeiteten Projekts UND andere Nestings). sheet_specs: [(x_origin_cm, machine_len_cm|None)].
    Früher setzte Bearbeiten x auf das erste alte Rohteil -> neues Rohteil lag auf Rohteil 01 (Log 17:51)."""
    x = 0.0
    for sx, ln in sheet_specs or []:
        try:
            x = max(x, float(sx) + float(fallback_len_cm if ln is None else ln) + gap_cm)
        except (TypeError, ValueError):
            x = max(x, x + fallback_len_cm + gap_cm)
    return x


def match_sheets(old, n):
    """2.0.4: Ergebnis-Blatt i (0-basiert) <-> vorhandenes Rohteil mit Projekt-Rohteil-Nr. i+1.
    old: [(project_sheet, occ, attr)]; Rückgabe (liste[n] von Eintrag|None, übrige Einträge).
    Früher positionsweise (old[i]) – nach manuellem Löschen eines Rohteils landeten Teile im falschen Rohteil."""
    by_no, rest = {}, []
    for e in old or []:
        try:
            no = int(e[0])
        except (TypeError, ValueError):
            no = 0
        if no > 0 and no not in by_no:
            by_no[no] = e
        else:
            rest.append(e)
    hits = [by_no.pop(i + 1, None) for i in range(n)]
    rest.extend(by_no.values())
    return hits, rest
