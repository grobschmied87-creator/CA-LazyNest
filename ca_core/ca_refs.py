# -*- coding: utf-8 -*-
"""CA Tools (ca_core) – veraltete Verknüpfungen (Document references) VOR jeder Neuberechnung erkennen.

Hintergrund (KMD-PC-03, 12.10.2026): design.computeAll() blieb bei 4 veralteten Referenzen > 2,5 min hängen.
Deshalb prüfen beide Add-Ins vorher Document.documentReferences (DocumentReference.isOutOfDate) und brechen
mit einer klaren Meldung ab. Reine Duck-Typing-Logik (ohne adsk-Import) -> mit Attrappen testbar."""


def _g(fn, default=None):
    try:
        v = fn()
        return default if v is None else v
    except Exception:
        return default


def _items(coll):
    n = _g(lambda: coll.count, 0)
    out = []
    for i in range(n if isinstance(n, int) else 0):
        it = _g(lambda: coll.item(i))
        if it is not None:
            out.append(it)
    return out


def outdated(doc_or_design):
    """-> (liste [{'name', 'version', 'latest'}], geprüft: bool). geprüft=False, wenn die API fehlt."""
    doc = _g(lambda: doc_or_design.parentDocument) or doc_or_design
    refs = _g(lambda: doc.documentReferences)
    if refs is None:
        return [], False
    out, seen = [], set()
    for r in _items(refs):
        if not _g(lambda: r.isOutOfDate, False):
            continue
        df = _g(lambda: r.dataFile)
        name = _g(lambda: df.name, '') if df is not None else ''
        name = name or _g(lambda: r.referencedDocument.name, '') or '?'
        if name in seen:
            continue
        seen.add(name)
        out.append({'name': name, 'version': _g(lambda: r.version),
                    'latest': _g(lambda: df.latestVersionNumber) if df is not None else None})
    return out, True


def message(items, what='Neuberechnung'):
    """Deutsche Abbruchmeldung (leer, wenn nichts veraltet)."""
    if not items:
        return ''
    names = ', '.join('%s%s' % (i['name'], (' (v%s → v%s)' % (i['version'], i['latest'])
                                            if i.get('version') and i.get('latest') else '')) for i in items[:6])
    more = ' …' if len(items) > 6 else ''
    return ('%d verknüpfte Komponente(n) sind veraltet: %s%s.\n\nVor der %s bitte in Fusion die Verknüpfungen '
            'aktualisieren (Hinweis „Referenzen sind veraltet“ oben rechts → „Alle aktualisieren“) und danach in '
            'CA LazyNest „Kopien aktualisieren“ ausführen. Sonst kann die Neuberechnung Fusion minutenlang '
            'blockieren.\n\nEs wurde nichts geändert.' % (len(items), names, more, what))
