# -*- coding: utf-8 -*-
"""ContourNest – Rohteilbibliothek (stock_library.json + user_stock.json).

Reine Python-Logik ohne Fusion-API (testbar).  Die Bibliothek liegt editierbar im
Add-In-Ordner; eigene Vorlagen („Als Vorlage speichern“) landen in user_stock.json und
werden beim Laden zusammengeführt.
"""
import copy
import json
import os

LIB_FILE = 'stock_library.json'
USER_FILE = 'user_stock.json'
GROUPS = ('Holzwerkstoff', 'Blech')
CUSTOM = 'Benutzerdefiniert'
FREE = '(freie Eingabe)'
UNVERIFIED_MARK = ' (ungeprüft)'


def _num(v):
    f = float(v)
    return int(round(f)) if abs(f - round(f)) < 1e-6 else round(f, 3)


def fmt_key(f):
    return '%gx%g' % (float(f[0]), float(f[1]))


def fmt_num(v):
    return ('%g' % float(v)).replace('.', ',')


def _read(path):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None


def _norm_entry(m):
    m = dict(m)
    m['group'] = m.get('group') or CUSTOM
    m['material'] = str(m.get('material') or 'Eigene Vorlage')
    m['formats'] = [[_num(f[0]), _num(f[1])] for f in (m.get('formats') or []) if len(f) >= 2]
    m['thicknesses'] = sorted(set(_num(t) for t in (m.get('thicknesses') or [])))
    m['grain'] = 'Y' if str(m.get('grain', 'X')).upper() == 'Y' else 'X'
    m.setdefault('format_thicknesses', {})
    m.setdefault('unverified_formats', [])
    m.setdefault('unverified_thicknesses', [])
    m.setdefault('sources', [])
    m.setdefault('density', None)
    return m


def merge(lib, user):
    """user-Einträge in lib übernehmen (gleiche Gruppe+Material -> Formate/Dicken vereinigen)."""
    out = copy.deepcopy(lib) if lib else {'materials': [], 'sources': {}}
    out['materials'] = [_norm_entry(m) for m in out.get('materials', [])]
    for um in (user or {}).get('materials', []):
        um = _norm_entry(um)
        um['user'] = True
        hit = next((m for m in out['materials']
                    if m['group'] == um['group'] and m['material'] == um['material']), None)
        if not hit:
            out['materials'].append(um)
            continue
        for f in um['formats']:
            if f not in hit['formats']:
                hit['formats'].append(f)
                # eigenes Format -> alle Dicken der Vorlage zulassen
            if hit['format_thicknesses'].get(fmt_key(f)) is not None:
                ft = set(hit['format_thicknesses'][fmt_key(f)]) | set(um['thicknesses'])
                hit['format_thicknesses'][fmt_key(f)] = sorted(ft)
        hit['thicknesses'] = sorted(set(hit['thicknesses']) | set(um['thicknesses']))
        if um.get('density'):
            hit['density'] = um['density']
        hit['user'] = True
    return out


def load(folder):
    lib = _read(os.path.join(folder, LIB_FILE)) or {'materials': [], 'sources': {}}
    user = _read(os.path.join(folder, USER_FILE)) or {}
    return merge(lib, user)


def save_user(folder, group, material, length, width, thickness, grain='X', density=None):
    """Vorlage in user_stock.json ablegen (zusammenführen); gibt den Dateipfad zurück."""
    path = os.path.join(folder, USER_FILE)
    data = _read(path) or {}
    data.setdefault('format', 'ContourNest-Benutzervorlagen')
    data.setdefault('materials', [])
    if group not in GROUPS:
        group = CUSTOM
    material = (material or '').strip() or 'Eigene Vorlage'
    f = [_num(length), _num(width)]
    t = _num(thickness)
    hit = next((m for m in data['materials'] if m.get('group') == group and m.get('material') == material), None)
    if not hit:
        hit = {'group': group, 'material': material, 'formats': [], 'thicknesses': [], 'grain': grain}
        data['materials'].append(hit)
    if f not in [[_num(a), _num(b)] for a, b in hit['formats']]:
        hit['formats'].append(f)
    if t not in [_num(x) for x in hit['thicknesses']]:
        hit['thicknesses'].append(t)
    hit['grain'] = 'Y' if str(grain).upper() == 'Y' else 'X'
    if density:
        hit['density'] = density
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    return path


# ----------------------------------------------------------------- Auswahl-Helfer
def groups(lib):
    return list(GROUPS) + [CUSTOM]


def materials(lib, group):
    ms = [m for m in lib.get('materials', []) if m['group'] == group]
    return ([FREE] if group == CUSTOM else []) + [m['material'] for m in ms]


def find(lib, group, material):
    return next((m for m in lib.get('materials', []) if m['group'] == group and m['material'] == material), None)


def _unv(m, f):
    return [_num(f[0]), _num(f[1])] in [[_num(a), _num(b)] for a, b in m.get('unverified_formats', [])]


def format_labels(m):
    if not m:
        return []
    return [('%d × %d mm' % (f[0], f[1])) + (UNVERIFIED_MARK if _unv(m, f) else '') for f in m['formats']]


def thickness_values(m, fmt_index=None):
    if not m:
        return []
    if fmt_index is not None and 0 <= fmt_index < len(m['formats']):
        ft = m.get('format_thicknesses', {}).get(fmt_key(m['formats'][fmt_index]))
        if ft:
            return sorted(set(_num(t) for t in ft))
    return list(m['thicknesses'])


def thickness_labels(m, fmt_index=None):
    unv = set(_num(t) for t in (m or {}).get('unverified_thicknesses', []))
    return [fmt_num(t) + ' mm' + (UNVERIFIED_MARK if t in unv else '') for t in thickness_values(m, fmt_index)]


def values(lib, group, material, fmt_index=None, thk_index=None):
    """Feldwerte für eine Auswahl -> dict(length, width, thickness, material, grain, density)."""
    m = find(lib, group, material)
    if not m:
        return {}
    out = {'material': m['material'], 'grain': m['grain']}
    if m.get('density'):
        out['density'] = m['density']
    if fmt_index is not None and 0 <= fmt_index < len(m['formats']):
        out['length'], out['width'] = m['formats'][fmt_index]
    th = thickness_values(m, fmt_index)
    if thk_index is not None and 0 <= thk_index < len(th):
        out['thickness'] = th[thk_index]
    return out


def match(lib, material, length, width, thickness):
    """Passende Auswahl (group, material, fmt_index, thk_index) zu vorhandenen Werten finden."""
    for m in lib.get('materials', []):
        if m['material'] != material:
            continue
        fi = next((i for i, f in enumerate(m['formats'])
                   if abs(f[0] - length) < 0.05 and abs(f[1] - width) < 0.05), None)
        th = thickness_values(m, fi)
        ti = next((i for i, t in enumerate(th) if abs(t - thickness) < 0.005), None)
        return m['group'], m['material'], fi, ti
    return CUSTOM, FREE, None, None


def for_palette(lib):
    """Kompakte Bibliothek für die Palette."""
    out = []
    for m in lib.get('materials', []):
        out.append({'group': m['group'], 'material': m['material'], 'formats': m['formats'],
                    'thicknesses': m['thicknesses'], 'format_thicknesses': m.get('format_thicknesses', {}),
                    'unverified_formats': m.get('unverified_formats', []),
                    'unverified_thicknesses': m.get('unverified_thicknesses', []),
                    'grain': m['grain'], 'density': m.get('density'), 'user': bool(m.get('user'))})
    return out
