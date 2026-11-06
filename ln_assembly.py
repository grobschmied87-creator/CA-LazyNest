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
                     'paths': [], 'linked': link_info(lk) if lk is not None else None, 'solids': nb}
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

def is_circular_dependency(err):
    """True, wenn die Meldung Fusion CIRCULAR_DEPENDENCY / zirkuläre Abhängigkeit enthält."""
    if err is None:
        return False
    s = str(err).upper()
    return 'CIRCULAR_DEPENDENCY' in s or 'CIRCULAR DEPENDENCY' in s or 'ZIRKUL' in s


def copy_needed(group, is_root=False):
    """Asm-Kopien nur wenn nötig: verknüpft, kein parent-comp, Körper in der Wurzel, oder Mehrkörper.
    Lokale 1-Körper-Teile werden als Original genestet (wie ContourNest / Non-Asm).
    is_root: parent ist die Wurzelkomponente (comp ist dann gesetzt, im Unterschied zu comp is None)."""
    if not isinstance(group, dict):
        return False
    if group.get('linked'):
        return True
    if group.get('comp') is None:
        return True
    if is_root:
        return True
    try:
        return int(group.get('solids') or 1) > 1
    except Exception:
        return False


def convert_blocks_part(res, convert_on=False):
    """convert_reason blockt nur ohne parent-comp (Wurzelkörper) bzw. needs_convert.
    Mehrkörper mit zugeordneter Komponente werden ohne Convert mitgenommen."""
    if not res or not res.get('convert_reason') or convert_on:
        return False
    if res.get('needs_convert'):
        return True
    return res.get('comp') is None


def fallback_dedupe_key(group):
    """Stabiler Schlüssel zum Deduplizieren von Original-Fallbacks derselben Komponente."""
    if not isinstance(group, dict):
        return ''
    comp = group.get('comp')
    if comp is not None:
        return comp_key(comp) or (group.get('key') or '')
    return group.get('key') or ''


def copy_fallback_action(group, copy_comp, copy_body, err=None, seen_keys=None):
    """Nach make_copies: 'copy' (ok) | 'fallback' (Original) | 'dup' (gleiche Comp schon da) | 'skip'."""
    if copy_comp is not None and copy_body is not None:
        return 'copy'
    if not isinstance(group, dict):
        return 'skip'
    if group.get('comp') is None or group.get('body') is None:
        return 'skip'
    key = fallback_dedupe_key(group)
    if seen_keys is not None and key and key in seen_keys:
        return 'dup'
    return 'fallback'
