# -*- coding: utf-8 -*-
"""Datei-Protokoll für CA LazyNest (Selbstdiagnose auf dem Anwender-PC).

logs/lazynest.log         laufendes Protokoll (rotierend, max. 2 MB × 4 Dateien)
logs/apply_<zeit>.json    vollständige Daten je „Übernehmen“
logs/diagnose_<zeit>.json Ergebnis des Befehls „LazyNest Diagnose“
Das Protokoll darf NIE eine Funktion stören: jeder Fehler hier wird verschluckt."""
import datetime
import functools
import json
import os
import traceback

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'lazynest.log')
VERSION = ''          # wird von LazyNest.py gesetzt


MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 3


def _write(level, msg, sync=False):
    """Zeile anhängen: Datei je Schreibvorgang öffnen/schließen (kein offener Handle, sofort kopierbar).
    sync=True: zusätzlich os.fsync – die Zeile steht auch dann auf der Platte, wenn Fusion direkt danach abstürzt."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        try:
            if os.path.getsize(LOG_FILE) > MAX_BYTES:
                for i in range(BACKUPS, 0, -1):
                    src = LOG_FILE + ('.%d' % (i - 1) if i > 1 else '')
                    dst = LOG_FILE + '.%d' % i
                    if os.path.exists(src):
                        if os.path.exists(dst):
                            os.remove(dst)
                        os.replace(src, dst)
        except OSError:
            pass
        line = '%s %-5s %s\n' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3], level, msg)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
            f.flush()
            if sync:
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
    except Exception:
        pass


def _fmt(msg, a):
    try:
        return msg % a if a else msg
    except Exception:
        return '%s %r' % (msg, a)


def info(msg, *a):
    _write('INFO', _fmt(msg, a))


SAFE_MODE = True     # Sicherheitsmodus: Kontrollpunkt (fsync) vor jedem riskanten Fusion-API-Aufruf


_LAST_CP = [None]


def checkpoint(msg, *a):
    """Kontrollpunkt VOR einem riskanten API-Aufruf (Löschen, CAM-Zuweisung, Neuberechnung …).
    Stürzt Fusion im Aufruf ab, ist die letzte Zeile des Protokolls genau dieser Aufruf („CP …“).
    2.0.2: am Ende „[+N ms]“ = Abstand zum vorigen Kontrollpunkt (Profil direkt aus dem Protokoll) – Profil direkt aus dem Protokoll."""
    if SAFE_MODE:
        import time as _time
        now = _time.perf_counter()
        d = '' if _LAST_CP[0] is None else ' [+%d ms]' % int((now - _LAST_CP[0]) * 1000)
        _LAST_CP[0] = now
        _write('CP', _fmt(msg, a) + d, sync=True)


def debug(msg, *a):
    _write('DEBUG', _fmt(msg, a))


def warn(msg, *a):
    _write('WARN', _fmt(msg, a))


def exc(context):
    """Aktuelle Ausnahme mit vollständigem Traceback protokollieren; liefert den Traceback-Text."""
    tb = traceback.format_exc()
    _write('ERROR', '%s\n%s' % (context, tb))
    return tb


def stamp():
    return datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]


def dump_json(prefix, data):
    """Schreibt logs/<prefix>_<zeit>.json und gibt den Pfad zurück ('' bei Fehler)."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, '%s_%s.json' % (prefix, stamp()))
        if isinstance(data, dict) and VERSION:
            data = dict(data)
            data.setdefault('lazynest_version', VERSION)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1, default=_default)
        info('JSON geschrieben: %s', path)
        _prune(prefix, keep=30)
        return path
    except Exception:
        exc('dump_json(%s)' % prefix)
        return ''


def _prune(prefix, keep):
    try:
        fs = sorted(f for f in os.listdir(LOG_DIR) if f.startswith(prefix + '_') and f.endswith('.json'))
        for f in fs[:-keep]:
            os.remove(os.path.join(LOG_DIR, f))
    except Exception:
        pass


def _default(o):
    try:
        return repr(o)
    except Exception:
        return '?'


def wrap_handler(name, fn, quiet=False):
    """Handler-notify umhüllen: Ereignis protokollieren, JEDE Ausnahme mit Traceback loggen."""
    @functools.wraps(fn)
    def notify(self, args):
        if not quiet:
            extra = ''
            try:
                inp = getattr(args, 'input', None)
                if inp is not None and isinstance(getattr(inp, 'id', None), str):
                    extra = ' input=%s' % inp.id
            except Exception:
                pass
            try:
                act = getattr(args, 'action', None)
                if isinstance(act, str):
                    d = getattr(args, 'data', '') or ''
                    extra += ' action=%s data=%d Zeichen' % (act, len(d) if isinstance(d, str) else -1)
            except Exception:
                pass
            debug('EVENT %s%s', name, extra)
        try:
            return fn(self, args)
        except Exception:
            exc('Ausnahme in %s' % name)
            raise
    return notify


# ------------------------------------------------------------------ Fusion-Daten → JSON
def mat(m):
    """Matrix3D -> 4x4-Liste (Zeilen), cm."""
    try:
        a = m.asArray()
        return [list(a[i * 4:(i + 1) * 4]) for i in range(4)]
    except Exception:
        try:
            o, x, y, z = m.getAsCoordinateSystem()
            return {'origin': [o.x, o.y, o.z], 'x': [x.x, x.y, x.z], 'y': [y.x, y.y, y.z], 'z': [z.x, z.y, z.z]}
        except Exception:
            return None


def bbox(b):
    """BoundingBox3D -> {'min': [..mm], 'max': [..mm]}."""
    try:
        return {'min_mm': [b.minPoint.x * 10, b.minPoint.y * 10, b.minPoint.z * 10],
                'max_mm': [b.maxPoint.x * 10, b.maxPoint.y * 10, b.maxPoint.z * 10]}
    except Exception:
        return None


def safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        return default if default is not None else 'FEHLER: %s' % e
