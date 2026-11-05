# -*- coding: utf-8 -*-
"""CA Tools (ca_core) – Fusion-Farbschema (hell/dunkel) ermitteln.

GeneralPreferences.activeUserInterfaceTheme (bei „Gerät“ das tatsächlich verwendete Schema), sonst
userInterfaceTheme. Werte (adsk.core.UserInterfaceThemes): 0 Classic, 1 LightGray, 2 DarkBlue, 3 DarkGray,
4 Device. Unbekannt / API fehlt -> 'light'."""
DARK_VALUES = (2, 3)
LIGHT_VALUES = (0, 1)


def from_value(v):
    try:
        iv = int(v)
    except Exception:
        s = str(v or '').lower()
        return 'dark' if 'dark' in s else 'light'
    return 'dark' if iv in DARK_VALUES else 'light'


def detect(app, log=None):
    """-> ('light'|'dark', Quelle)."""
    try:
        gp = app.preferences.generalPreferences
    except Exception as e:
        if log:
            log('Theme: Einstellungen nicht lesbar (%s) – hell' % e)
        return 'light', 'Rückfall'
    for attr in ('activeUserInterfaceTheme', 'userInterfaceTheme'):
        try:
            v = getattr(gp, attr)
        except Exception:
            continue
        if v is None:
            continue
        try:
            if int(v) == 4:              # Gerät – nur activeUserInterfaceTheme kennt das Ergebnis
                continue
        except Exception:
            pass
        return from_value(v), '%s=%s' % (attr, v)
    return 'light', 'Rückfall'
