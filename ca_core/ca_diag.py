# -*- coding: utf-8 -*-
"""CA Tools (ca_core) – gemeinsame Protokoll-/Diagnose-Helfer und Infobox-Daten (beide Add-Ins identisch)."""
import json
import os
import platform
import subprocess
import sys

AUTHOR = {'company': 'Chris Adam Modellbau', 'street': 'Riesebusch 17', 'city': '23611 Bad Schwartau',
          'mail': 'chris.n.adam@gmx.de', 'phone': '0176 99229588', 'year': '2026'}
SLOGANS = {
    'lazynest': '„Nesting für Macher und faule Typen, denn nur durch Faulheit entsteht Innovation!“',
    'lazysetup': '„Fräsen vorbereiten für Macher und faule Typen, denn nur durch Faulheit entsteht Innovation!“',
}


def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def info_payload(product, app_name, version, build_date, lic=None):
    """Alles, was die gemeinsame Infobox (web/ca_shell.js) anzeigt. lic optional (OSS-Kompat)."""
    return {'product': product, 'appName': app_name, 'version': version, 'buildDate': build_date,
            'author': AUTHOR, 'slogan': SLOGANS.get(product, ''), 'license': lic or {}}


def diag_text(app_name, version, build_date, app=None, lic=None, log_file='', extra=None):
    """Kurztext für „Diagnose kopieren“ (keine Geheimnisse)."""
    lic = lic or {}
    lines = ['%s %s (Build %s)' % (app_name, version, build_date),
             'Fusion %s · Python %s · %s %s' % (safe(lambda: app.version, '?') if app else '?',
                                               sys.version.split()[0], platform.system(), platform.release()),
             'Edition: %s' % (lic.get('line') or 'Open Source – kostenlos'),
             'Protokoll: %s' % log_file]
    for k, v in (extra or {}).items():
        lines.append('%s: %s' % (k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)))
    return '\n'.join(x for x in lines if x)


def open_folder(path):
    """Ordner im Explorer/Finder öffnen. -> True/False."""
    try:
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
        if hasattr(os, 'startfile'):
            os.startfile(path)           # Windows
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
        return True
    except Exception:
        return False
