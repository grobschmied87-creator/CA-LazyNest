# -*- coding: utf-8 -*-
"""LazyNest 2.0.2: Zeitmessung je Phase (ms) für das Protokoll. Ohne Fusion-Abhängigkeit.

    with TM.phase('Einlesen'):
        ...
    TM.report() -> 'Einlesen 2710 ms, Skizzen 812 ms (3×), …'
"""
import time
from contextlib import contextmanager

_T = {}         # Phase -> [ms gesamt, Anzahl]
_C = {}         # Zähler
_ORDER = []


def reset():
    _T.clear()
    _C.clear()
    del _ORDER[:]


@contextmanager
def phase(name):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        add(name, (time.perf_counter() - t0) * 1000.0)


def add(name, ms):
    if name not in _T:
        _T[name] = [0.0, 0]
        _ORDER.append(name)
    _T[name][0] += ms
    _T[name][1] += 1


def count(name, n=1):
    _C[name] = _C.get(name, 0) + n


def data():
    return {'phases_ms': {k: round(_T[k][0], 1) for k in _ORDER}, 'calls': {k: _T[k][1] for k in _ORDER},
            'counters': dict(_C)}


def report():
    parts = ['%s %.0f ms%s' % (k, _T[k][0], ' (%d×)' % _T[k][1] if _T[k][1] > 1 else '') for k in _ORDER]
    parts += ['%s: %d' % (k, v) for k, v in sorted(_C.items())]
    return ', '.join(parts) or '-'
