# -*- coding: utf-8 -*-
"""CA Tools (ca_core) – gemeinsamer Neuberechnungs-Stand (computeAll) für LazyNest und LazySetup (1.1.0).

Nach einem erfolgreichen design.computeAll() schreibt das Add-In einen Fingerabdruck des Zeitachsen-Stands als
Design-Attribut CA_Tools/computed_state. LazySetup („Neuberechnen: auto“) vergleicht ihn vor den CAM-Änderungen
mit dem aktuellen Stand und überspringt computeAll (bis ~2 min), wenn das Modell seither unverändert ist.

Fingerabdruck (bewusst OHNE Dokumentversion/Änderungsflag/Dokumentname-Version – Speichern, Attribute und
CAM-Setups ändern die Geometrie nicht):
  doc   Dokument-Kennung (dataFile.id, sonst Name ohne „ vN“)
  type  designType
  count/marker  timeline.count / timeline.markerPosition
  items Hash über Name, Unterdrückung, Zurückgerollt, Gruppe und Fehlerzustand ALLER Zeitachsen-Elemente
Kann der Fingerabdruck nicht vollständig bestimmt werden -> None (dann bleibt es beim computeAll).
Reine Duck-Typing-Logik (ohne adsk-Import) -> mit Attrappen testbar."""
import hashlib
import json
import re
import time

GROUP, NAME = 'CA_Tools', 'computed_state'
OLD_NAME = 'compute_state'           # 2.0.2 (nur Zeitachsen-Anzahl/-Marker)
FORMAT = 2
MAX_ITEMS = 20000                    # Sicherheitsgrenze (Zeitachse lesen ~ ms je Element)


def _g(fn, default=None):
    try:
        v = fn()
        return default if v is None else v
    except Exception:
        return default


def doc_key(design):
    doc = _g(lambda: design.parentDocument)
    if doc is None:
        return None
    fid = _g(lambda: doc.dataFile.id)
    if fid:
        return 'id:' + str(fid)
    nm = _g(lambda: doc.name, '') or ''
    return 'name:' + re.sub(r'\s+v\d+$', '', str(nm))


def fingerprint(design):
    """-> dict oder None (nicht bestimmbar)."""
    if design is None:
        return None
    dt = _g(lambda: int(design.designType))
    tl = _g(lambda: design.timeline)
    if tl is None or dt is None:
        return None
    n = _g(lambda: tl.count)
    mk = _g(lambda: tl.markerPosition)
    if not isinstance(n, int) or not isinstance(mk, int) or n > MAX_ITEMS:
        return None
    h = hashlib.sha1()
    for i in range(n):
        it = _g(lambda: tl.item(i))
        if it is None:
            return None
        rec = (_g(lambda: it.name, '?'), _g(lambda: bool(it.isSuppressed)), _g(lambda: bool(it.isRolledBack)),
               _g(lambda: bool(it.isGroup)), _g(lambda: int(it.healthState)))
        h.update(json.dumps(rec, ensure_ascii=False, default=str).encode('utf-8'))
        h.update(b'\n')
    return {'f': FORMAT, 'doc': doc_key(design), 'type': dt, 'count': n, 'marker': mk, 'items': h.hexdigest()[:16]}


KEYS = ('doc', 'type', 'count', 'marker', 'items')


def compare(stored, cur):
    """-> (gleich: bool, Grund: str)."""
    if cur is None:
        return False, 'aktueller Stand nicht bestimmbar'
    if not isinstance(stored, dict) or stored.get('f') != FORMAT or not isinstance(stored.get('fp'), dict):
        return False, 'kein gespeicherter Stand'
    fp = stored['fp']
    diff = ['%s %s -> %s' % (k, fp.get(k), cur.get(k)) for k in KEYS if fp.get(k) != cur.get(k)]
    if diff:
        return False, 'geändert: ' + ', '.join(diff)
    return True, 'unverändert seit %s (%s)' % (stored.get('by', '?'),
                                               time.strftime('%H:%M:%S', time.localtime(stored.get('time') or 0)))


def read(design):
    a = _g(lambda: design.attributes.itemByName(GROUP, NAME))
    try:
        return json.loads(a.value) if a is not None else None
    except Exception:
        return None


def write(design, by, fp=None):
    """Stand nach erfolgreichem computeAll ablegen. -> (ok, fp oder Fehlertext)."""
    fp = fp if fp is not None else fingerprint(design)
    if fp is None:
        return False, 'Fingerabdruck nicht bestimmbar'
    try:
        design.attributes.add(GROUP, NAME, json.dumps({'f': FORMAT, 'fp': fp, 'by': by, 'time': time.time()}))
        return True, fp
    except Exception as e:
        return False, str(e)


def clear(design):
    """Stand verwerfen (z. B. computeAll fehlgeschlagen)."""
    a = _g(lambda: design.attributes.itemByName(GROUP, NAME))
    if a is not None:
        _g(lambda: a.deleteMe())
