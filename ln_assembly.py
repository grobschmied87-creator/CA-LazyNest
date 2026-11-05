# -*- coding: utf-8 -*-
"""CA LazyNest – Baugruppen und verknüpfte Komponenten (reine Logik über Occurrence-Objekte, ohne adsk-Import,
damit sie mit Attrappen getestet werden kann).

linked_parent(ent):  erste (innerste) verknüpfte Occurrence in der Kette Entity -> assemblyContext -> … (oder None)
occ_path(ent):       Kontextpfad „A:1+B:1“ (fullPathName)
collect(occs, …):    alle Volumenkörper rekursiv aus gewählten Baugruppen-Occurrences, gruppiert je
                     (Komponente, Körper) -> Stückzahl = Anzahl Instanzen."""


def _g(fn, default=None):
    try:
        v = fn()
        return default if v is None else v
    except Exception:
        return default


def _ctx(ent):
    """Occurrence, in deren Kontext ent liegt (Occurrence selbst oder assemblyContext)."""
    if _g(lambda: ent.objectType, '') == 'adsk::fusion::Occurrence':
        return ent
    return _g(lambda: ent.assemblyContext)


def chain(ent):
    """Occurrence-Kette von innen nach außen."""
    out, o, n = [], _ctx(ent), 0
    while o is not None and n < 64:
        out.append(o)
        o = _g(lambda: o.assemblyContext)
        n += 1
    return out


def linked_parent(ent):
    for o in chain(ent):
        if _g(lambda: o.isReferencedComponent, False):
            return o
    return None


def occ_path(ent):
    c = _ctx(ent)
    return _g(lambda: c.fullPathName, '') if c is not None else ''


def parent_names(ent):
    return [_g(lambda: o.name, '?') for o in reversed(chain(ent))]


def link_info(occ):
    """Quelle einer verknüpften Occurrence: Dokument/Version, veraltet?"""
    if occ is None:
        return None
    ref = _g(lambda: occ.documentReference)
    df = _g(lambda: ref.dataFile) if ref is not None else None
    return {'occurrence': _g(lambda: occ.fullPathName, ''), 'component': _g(lambda: occ.component.name, ''),
            'document': _g(lambda: df.name, '') if df is not None else '',
            'file_id': _g(lambda: df.id, '') if df is not None else '',
            'version': _g(lambda: ref.version, None) if ref is not None else None,
            'latest_version': _g(lambda: df.latestVersionNumber, None) if df is not None else None,
            'out_of_date': bool(_g(lambda: ref.isOutOfDate, False)) if ref is not None else False}


def _items(coll):
    n = _g(lambda: coll.count, 0)
    return [coll.item(i) for i in range(n if isinstance(n, int) else 0)]


def comp_key(comp):
    return _g(lambda: comp.id, '') or _g(lambda: comp.entityToken, '') or _g(lambda: comp.name, '?')


def collect(occs, include_hidden=False, max_depth=32):
    """occs: gewählte Occurrences (Proxys im Wurzelkontext). Liefert (groups, stats):
    groups = [{'key', 'comp', 'body' (nativ), 'body_name', 'name', 'count', 'paths': [...], 'linked': info|None}]"""
    groups, order = {}, []
    stats = {'occurrences': 0, 'bodies': 0, 'hidden_skipped': 0, 'non_solid_skipped': 0, 'linked': 0}
    seen = set()

    def visit(o, depth):
        path = _g(lambda: o.fullPathName, '') or str(id(o))
        if path in seen or depth > max_depth:
            return
        seen.add(path)
        stats['occurrences'] += 1
        comp = _g(lambda: o.component)
        if comp is None:
            return
        lk = linked_parent(o)
        for b in _items(_g(lambda: comp.bRepBodies)):
            if not _g(lambda: b.isSolid, False):
                stats['non_solid_skipped'] += 1
                continue
            if not include_hidden and _g(lambda: b.isLightBulbOn, True) is False:
                stats['hidden_skipped'] += 1
                continue
            bname = _g(lambda: b.name, 'Körper')
            key = 'A:%s/%s' % (comp_key(comp), bname)
            g = groups.get(key)
            if g is None:
                nb = sum(1 for x in _items(_g(lambda: comp.bRepBodies)) if _g(lambda: x.isSolid, False))
                name = _g(lambda: comp.name, '?') + ('' if nb <= 1 else ' – ' + bname)
                g = {'key': key, 'comp': comp, 'body': b, 'body_name': bname, 'name': name, 'count': 0,
                     'paths': [], 'linked': link_info(lk) if lk is not None else None}
                groups[key] = g
                order.append(key)
                if lk is not None:
                    stats['linked'] += 1
            g['count'] += 1
            g['paths'].append(path)
            stats['bodies'] += 1
        for ch in _items(_g(lambda: o.childOccurrences)):
            visit(ch, depth + 1)

    for o in occs:
        visit(o, 0)
    return [groups[k] for k in order], stats
