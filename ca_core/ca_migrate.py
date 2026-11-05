# -*- coding: utf-8 -*-
"""CA Tools (ca_core) – Benutzerdaten aus dem Ordner des Vorgänger-Add-Ins übernehmen (einmalig, nie überschreibend).

LazyNest  <- ContourNest  (user_prefs.json, user_stock.json, projects/*.json)
LazySetup <- Finalschnitt (user_settings.json, user_presets.json)
Quelle: <AddIns>/<Alt> oder das neueste <AddIns>/<Alt>_backup_* (Installer sichert dorthin)."""
import json
import os
import shutil


def candidates(addins_dir, old_name, extra=()):
    out = []
    for d in list(extra) + [os.path.join(addins_dir, old_name)]:
        if d and os.path.isdir(d) and d not in out:
            out.append(d)
    try:
        bk = sorted((n for n in os.listdir(addins_dir) if n.startswith(old_name + '_backup_')), reverse=True)
    except OSError:
        bk = []
    for n in bk:
        p = os.path.join(addins_dir, n)
        if os.path.isdir(p) and p not in out:
            out.append(p)
    return out


def migrate(new_dir, old_dirs, files, with_projects=False, log=None):
    """Kopiert fehlende Benutzerdateien aus der ersten Quelle, die Daten hat.
    -> dict(copied=[...], skipped=[...], source=pfad|None)."""
    log = log or (lambda *a: None)
    rep = {'copied': [], 'skipped': [], 'source': None}
    for src in old_dirs:
        if not os.path.isdir(src) or os.path.abspath(src) == os.path.abspath(new_dir):
            continue
        have = [f for f in files if os.path.isfile(os.path.join(src, f))]
        pdir = os.path.join(src, 'projects')
        projs = sorted(f for f in os.listdir(pdir) if f.lower().endswith('.json')) \
            if with_projects and os.path.isdir(pdir) else []
        if not have and not projs:
            continue
        rep['source'] = src
        for f in have:
            dst = os.path.join(new_dir, f)
            if os.path.exists(dst):
                rep['skipped'].append(f)
                continue
            try:
                shutil.copy2(os.path.join(src, f), dst)
                rep['copied'].append(f)
            except OSError as e:
                log('Migration %s: %s' % (f, e))
        if projs:
            os.makedirs(os.path.join(new_dir, 'projects'), exist_ok=True)
        for f in projs:
            dst = os.path.join(new_dir, 'projects', f)
            if os.path.exists(dst):
                rep['skipped'].append('projects/' + f)
                continue
            try:
                shutil.copy2(os.path.join(pdir, f), dst)
                rep['copied'].append('projects/' + f)
            except OSError as e:
                log('Migration projects/%s: %s' % (f, e))
        break
    return rep


def run_once(new_dir, marker_file, old_name, files, with_projects=False, extra=(), log=None, mark_key=None):
    """Einmal-Migration mit Merker (JSON-Schlüssel mark_key in marker_file; die Datei kann selbst eine der
    übernommenen sein). -> Bericht oder None (schon erledigt)."""
    mark_key = mark_key or ('migrated_from_' + old_name.lower())

    def rd():
        try:
            with open(marker_file, 'r', encoding='utf-8') as fh:
                d = json.load(fh)
                return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    if rd().get(mark_key):
        return None
    rep = migrate(new_dir, candidates(os.path.dirname(os.path.abspath(new_dir)), old_name, extra), files,
                  with_projects, log)
    d = rd()
    d[mark_key] = {'source': rep['source'], 'copied': len(rep['copied'])}
    try:
        with open(marker_file, 'w', encoding='utf-8') as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return rep
