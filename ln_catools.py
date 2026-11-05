# -*- coding: utf-8 -*-
"""Gemeinsames Werkzeugkasten-Panel „CA Tools“ für alle CA-Add-Ins (ContourNest, Finalschnitt, CabineoConnector,
CycloidGear).

Diese Datei liegt in JEDEM Add-In unter eigenem Namen (ln_catools.py, fs_catools.py, cab_catools.py,
zyk_catools.py) – inhaltlich identisch (Master: ca_toolbar_master.py). Eigene Modulnamen, damit sich die Add-Ins
im gemeinsamen Python-Prozess von Fusion nicht gegenseitig ein (evtl. älteres) Modul unterschieben.

Regeln (für alle Add-Ins gleich):
  * Panel per get-or-create mit derselben ID (PANEL_IDS) – wer zuerst startet, legt es an.
  * Jedes Add-In fügt nur SEINE Befehle hinzu und entfernt in stop() nur SEINE Befehle.
  * Das Panel wird nur gelöscht, wenn danach nichts mehr außer dem „CA Tools“-Infoeintrag darin ist.
  * Arbeitsbereich/Registerkarte nicht gefunden -> None (Aufrufer nimmt den alten Ort, z. B. Zusatzmodule) + Log.
  * Ein Panel kann in Fusion kein eigenes Symbol haben -> kleiner Infoeintrag „Über CA Tools“ (ABOUT_CMD) mit
    dem CA-Logo im Panel. Er gehört dem Add-In, das ihn angelegt hat; beendet sich dieses, übernimmt ein noch
    laufendes Add-In den Eintrag (gemeinsames Register in sys.modules).
"""
import os
import sys
import types

import adsk.core

PANEL_NAME = 'CA Tools'
PANEL_IDS = {'design': 'CA_ToolsPanel', 'cam': 'CA_ToolsPanel_CAM'}   # Panel-IDs müssen in Fusion global eindeutig sein
WS_IDS = {'design': 'FusionSolidEnvironment', 'cam': 'CAMEnvironment'}
TAB_IDS = {'design': ('SolidTab',), 'cam': ('MillingTab',)}
TAB_NAMES = {'design': ('SOLID', 'VOLUMENKÖRPER', 'VOLUMENKORPER'), 'cam': ('MILLING', 'FRÄSEN', 'FRAESEN')}
ASSIST_HINTS = ('assist',)          # CAM Assist: IDs werden NICHT fest verdrahtet, sondern zur Laufzeit gesucht
ABOUT_CMD = 'CA_Tools_About'
REG_NAME = '_ca_tools_registry'


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _items(coll):
    n = _safe(lambda: coll.count, 0) or 0
    out = []
    for i in range(n):
        it = _safe(lambda: coll.item(i))
        if it is not None:
            out.append(it)
    return out


def _txt(o, attr):
    return str(_safe(lambda: getattr(o, attr), '') or '')


def _registry():
    reg = sys.modules.get(REG_NAME)
    if reg is None:
        reg = types.ModuleType(REG_NAME)
        reg.addins = {}             # Name -> {'version': str, 'ensure_about': callable}
        reg.about_owner = None
        reg.layout_logged = set()
        sys.modules[REG_NAME] = reg
    return reg


def _nolog(level, msg):
    pass


# ---------------------------------------------------------------------------------------------- Diagnose
def log_layout(ui, where, log=_nolog, force=False):
    """Alle Registerkarten/Panels (IDs + Namen) des Arbeitsbereichs protokollieren (je Fusion-Sitzung einmal)."""
    reg = _registry()
    ws_id = WS_IDS[where]
    if ws_id in reg.layout_logged and not force:
        return
    ws = _safe(lambda: ui.workspaces.itemById(ws_id))
    if ws is None:
        log('WARN', 'CA Tools: Arbeitsbereich %s nicht gefunden' % ws_id)
        return
    reg.layout_logged.add(ws_id)
    lines = []
    for tab in _items(_safe(lambda: ws.toolbarTabs)):
        pans = ['%s „%s“' % (_txt(p, 'id'), _txt(p, 'name')) for p in _items(_safe(lambda: tab.toolbarPanels))]
        lines.append('  Tab %s „%s“: %s' % (_txt(tab, 'id'), _txt(tab, 'name'), ', '.join(pans) or '–'))
    if not lines:
        pans = ['%s „%s“' % (_txt(p, 'id'), _txt(p, 'name')) for p in _items(_safe(lambda: ws.toolbarPanels))]
        lines.append('  (keine toolbarTabs) Panels: %s' % (', '.join(pans) or '–'))
    log('INFO', 'CA Tools: Werkzeugleiste %s:\n%s' % (ws_id, '\n'.join(lines)))


def _match(o, hints):
    s = (_txt(o, 'id') + ' ' + _txt(o, 'name')).lower()
    return any(h in s for h in hints)


def find_cam_assist(ws):
    """(Tab, Panel) von CAM Assist – gesucht über ID/Name von Panels und deren Befehlen, sonst (None, None)."""
    for tab in _items(_safe(lambda: ws.toolbarTabs)):
        for p in _items(_safe(lambda: tab.toolbarPanels)):
            if _txt(p, 'id') in PANEL_IDS.values():
                continue
            if _match(p, ASSIST_HINTS) or any(_match(c, ASSIST_HINTS) for c in _items(_safe(lambda: p.controls))):
                return tab, p
    return None, None


def _find_tab(ws, where):
    tabs = _safe(lambda: ws.toolbarTabs)
    if tabs is None:
        return None
    for tid in TAB_IDS[where]:
        t = _safe(lambda: tabs.itemById(tid))
        if t is not None:
            return t
    for t in _items(tabs):
        if _txt(t, 'name').strip().upper() in TAB_NAMES[where]:
            return t
    if where == 'cam':
        for t in _items(tabs):
            if 'mill' in _txt(t, 'id').lower():
                return t
    return None


# ---------------------------------------------------------------------------------------------- Panel
def get_panel(ui, where, log=_nolog):
    """Gemeinsames CA-Tools-Panel holen oder anlegen; None = nicht möglich (Aufrufer nimmt den alten Ort)."""
    pid = PANEL_IDS[where]
    try:
        p = _safe(lambda: ui.allToolbarPanels.itemById(pid))
        ws = _safe(lambda: ui.workspaces.itemById(WS_IDS[where]))
        if p is None and ws is not None:
            p = _safe(lambda: ws.toolbarPanels.itemById(pid))
        if p is not None:
            log('INFO', 'CA Tools: Panel %s vorhanden (anderes Add-In) – wird mitbenutzt' % pid)
            return p
        log_layout(ui, where, log)
        if ws is None:
            log('WARN', 'CA Tools: Arbeitsbereich %s nicht gefunden – alter Ort wird verwendet' % WS_IDS[where])
            return None
        tab, pos = None, ''
        if where == 'cam':
            tab, ap = find_cam_assist(ws)
            if tab is not None:
                pos = _txt(ap, 'id')
                log('INFO', 'CA Tools: CAM Assist gefunden – Tab %s, Panel %s' % (_txt(tab, 'id'), pos))
            else:
                log('INFO', 'CA Tools: CAM Assist nicht gefunden – Panel ans Ende der Registerkarte FRÄSEN')
        if tab is None:
            tab = _find_tab(ws, where)
        if tab is None:
            log('WARN', 'CA Tools: Registerkarte %s in %s nicht gefunden – alter Ort wird verwendet'
                % ('/'.join(TAB_IDS[where]), WS_IDS[where]))
            return None
        panels = tab.toolbarPanels
        p = panels.add(pid, PANEL_NAME, pos, False) if pos else panels.add(pid, PANEL_NAME)
        log('INFO', 'CA Tools: Panel %s in Tab %s angelegt%s' % (pid, _txt(tab, 'id'),
                                                                   (' (rechts neben %s)' % pos) if pos else ''))
        return p
    except Exception as e:
        log('WARN', 'CA Tools: Panel %s konnte nicht angelegt werden (%s) – alter Ort wird verwendet' % (pid, e))
        return None


def add_command(panel, cd, promoted=False):
    """Befehl ins Panel (vorhandenes Steuerelement gleicher ID zuerst entfernen); promoted = großes Symbol."""
    old = _safe(lambda: panel.controls.itemById(cd.id))
    if old is not None:
        _safe(lambda: old.deleteMe())
    ctl = panel.controls.addCommand(cd)
    if promoted and ctl is not None:
        _safe(lambda: setattr(ctl, 'isPromotedByDefault', True))
        _safe(lambda: setattr(ctl, 'isPromoted', True))
    return ctl


def _panels(ui):
    out = []
    for where, pid in PANEL_IDS.items():
        p = _safe(lambda: ui.allToolbarPanels.itemById(pid))
        if p is None:
            ws = _safe(lambda: ui.workspaces.itemById(WS_IDS[where]))
            p = _safe(lambda: ws.toolbarPanels.itemById(pid)) if ws is not None else None
        if p is not None:
            out.append(p)
    return out


def _foreign(panel):
    """Steuerelemente außer dem Infoeintrag (Trennlinien ohne ID zählen nicht)."""
    return [c for c in _items(_safe(lambda: panel.controls)) if _txt(c, 'id') and _txt(c, 'id') != ABOUT_CMD]


# ---------------------------------------------------------------------------------------------- Infoeintrag
class _AboutCreated(adsk.core.CommandCreatedEventHandler):
    def __init__(self, ui):
        super().__init__()
        self.ui = ui

    def notify(self, args):
        try:
            reg = _registry()
            rows = ['• %s v%s' % (n, d.get('version', '?')) for n, d in sorted(reg.addins.items())]
            self.ui.messageBox('CA Tools – gemeinsamer Werkzeugkasten\n\nGestartete Add-Ins:\n%s'
                               % ('\n'.join(rows) or '–'), 'CA Tools')
        except Exception:
            pass


def ensure_about(ui, owner, logo_dir, handlers, log=_nolog):
    """Infoeintrag „Über CA Tools“ (mit CA-Logo) in jedes vorhandene CA-Tools-Panel; Besitzer = owner."""
    reg = _registry()
    try:
        if not _panels(ui):             # kein CA-Tools-Panel (Rückfall auf alten Ort) -> kein Infoeintrag
            return
        cd = _safe(lambda: ui.commandDefinitions.itemById(ABOUT_CMD))
        if cd is None or reg.about_owner not in reg.addins:
            if cd is not None:
                for p in _panels(ui):
                    c = _safe(lambda: p.controls.itemById(ABOUT_CMD))
                    if c is not None:
                        _safe(lambda: c.deleteMe())
                _safe(lambda: cd.deleteMe())
            res = logo_dir if os.path.isfile(os.path.join(logo_dir, '16x16.png')) else ''
            cd = ui.commandDefinitions.addButtonDefinition(ABOUT_CMD, 'Über CA Tools',
                                                           'CA Tools – gemeinsamer Werkzeugkasten der CA-Add-Ins', res)
            h = _AboutCreated(ui)
            cd.commandCreated.add(h)
            handlers.append(h)
            reg.about_owner = owner
        for p in _panels(ui):
            if _safe(lambda: p.controls.itemById(ABOUT_CMD)) is None:
                p.controls.addCommand(cd)
    except Exception as e:
        log('WARN', 'CA Tools: Infoeintrag nicht angelegt (%s)' % e)


def register(ui, owner, version, logo_dir, handlers, log=_nolog):
    """Nach dem Hinzufügen der eigenen Befehle aufrufen."""
    reg = _registry()
    reg.addins[owner] = {'version': version,
                         'ensure_about': lambda: ensure_about(ui, owner, logo_dir, handlers, log)}
    ensure_about(ui, owner, logo_dir, handlers, log)


def unregister(ui, owner, cmd_ids, log=_nolog):
    """In stop(): eigene Befehle aus allen CA-Tools-Panels entfernen, Infoeintrag ggf. übergeben, leere Panels
    löschen. Gibt die Anzahl entfernter Steuerelemente zurück."""
    reg = _registry()
    reg.addins.pop(owner, None)
    n = 0
    for p in _panels(ui):
        for cid in cmd_ids:
            c = _safe(lambda: p.controls.itemById(cid))
            if c is not None:
                _safe(lambda: c.deleteMe())
                n += 1
    if reg.about_owner == owner:
        for p in _panels(ui):
            c = _safe(lambda: p.controls.itemById(ABOUT_CMD))
            if c is not None:
                _safe(lambda: c.deleteMe())
        cd = _safe(lambda: ui.commandDefinitions.itemById(ABOUT_CMD))
        if cd is not None:
            _safe(lambda: cd.deleteMe())
        reg.about_owner = None
        for other in list(reg.addins.values()):     # ein noch laufendes Add-In übernimmt den Infoeintrag
            _safe(other['ensure_about'])
            break
    for p in _panels(ui):
        if not _foreign(p):
            pid = _txt(p, 'id')
            c = _safe(lambda: p.controls.itemById(ABOUT_CMD))
            if c is not None:
                _safe(lambda: c.deleteMe())
            _safe(lambda: p.deleteMe())
            log('INFO', 'CA Tools: Panel %s leer – gelöscht' % pid)
    if not reg.addins and reg.about_owner is None:
        cd = _safe(lambda: ui.commandDefinitions.itemById(ABOUT_CMD))
        if cd is not None:
            _safe(lambda: cd.deleteMe())
    log('INFO', 'CA Tools: %d eigene Steuerelement(e) von %s entfernt' % (n, owner))
    return n
