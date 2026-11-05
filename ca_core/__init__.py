# -*- coding: utf-8 -*-
"""CA Tools – gemeinsamer Kern für CA LazyNest und CA LazySetup (identischer Code in beiden Add-Ins).

Laden NUR über load_core() des jeweiligen Add-Ins (eigener Modulname je Add-In, z. B. 'lazynest_ca_core'),
damit sich die beiden Add-Ins im gemeinsamen Fusion-Python nicht gegenseitig die Module überschreiben.
Inhalt: ca_theme, ca_refs, ca_diag, ca_migrate, ca_compute, ca_i18n; web/ca_shell.js + web/ca_core.css
(Infobox mit Spendenhinweis, Theme). OSS ohne Lizenzmodul."""
CORE_VERSION = '1.1.0'

from . import ca_theme, ca_refs, ca_diag, ca_migrate, ca_compute   # noqa: E402,F401
__all__ = ['CORE_VERSION', 'ca_theme', 'ca_refs', 'ca_diag', 'ca_migrate', 'ca_compute']
from . import ca_i18n  # noqa: F401
