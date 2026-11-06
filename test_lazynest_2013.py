# -*- coding: utf-8 -*-
"""2.0.13: Mehrkörper warn-include + CIRCULAR-Fallback auf Originale (ohne Fusion)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE if os.path.isfile(os.path.join(HERE, 'ln_assembly.py')) else os.path.abspath(os.path.join(HERE, '..', 'LazyNest'))
sys.path.insert(0, ROOT)
import ln_assembly as A  # noqa: E402
from ca_core import ca_i18n  # noqa: E402


class Comp:
    def __init__(self, name, cid=None):
        self.name = name
        self.id = cid or ('id-' + name)
        self.entityToken = 'tok-' + name


class Body:
    def __init__(self, name):
        self.name = name


class TestCircular(unittest.TestCase):
    MSG = ('3 : Failed to create component: CIRCULAR_DEPENDENCY - Zirkuläre Abhängigkeit gefunden: '
           'von R-Anordnung1 zu Kombinieren1.')

    def test_detects_fusion_message(self):
        self.assertTrue(A.is_circular_dependency(self.MSG))
        self.assertTrue(A.is_circular_dependency(RuntimeError(self.MSG)))
        self.assertFalse(A.is_circular_dependency(None))
        self.assertFalse(A.is_circular_dependency('TemporaryBRepManager.copy lieferte nichts'))


class TestCopyNeeded(unittest.TestCase):
    def test_local_one_body(self):
        g = {'comp': Comp('K7'), 'body': Body('K7'), 'linked': None, 'solids': 1}
        self.assertFalse(A.copy_needed(g))
        self.assertFalse(A.copy_needed(g, is_root=False))

    def test_linked(self):
        g = {'comp': Comp('X'), 'body': Body('X'), 'linked': {'component': 'Lib'}, 'solids': 1}
        self.assertTrue(A.copy_needed(g))

    def test_no_comp(self):
        g = {'comp': None, 'body': Body('RootBody'), 'linked': None, 'solids': 1}
        self.assertTrue(A.copy_needed(g))

    def test_root_flag(self):
        g = {'comp': Comp('Root'), 'body': Body('B'), 'linked': None, 'solids': 1}
        self.assertFalse(A.copy_needed(g))
        self.assertTrue(A.copy_needed(g, is_root=True))

    def test_multibody(self):
        g = {'comp': Comp('S2'), 'body': Body('S2 DG'), 'linked': None, 'solids': 2}
        self.assertTrue(A.copy_needed(g))

    def test_missing_solids_defaults_one(self):
        g = {'comp': Comp('K3'), 'body': Body('K3')}
        self.assertFalse(A.copy_needed(g))


class TestConvertBlocks(unittest.TestCase):
    def test_root_blocks_without_convert(self):
        res = {'convert_reason': 'Körper liegt in der Wurzelkomponente', 'needs_convert': True,
               'comp': None, 'body': Body('x')}
        self.assertTrue(A.convert_blocks_part(res, False))
        self.assertFalse(A.convert_blocks_part(res, True))

    def test_multibody_does_not_block(self):
        res = {'convert_reason': 'Komponente „S2“ enthält 2 Körper', 'comp': Comp('S2'),
               'body': Body('S2 DG'), 'multibody': 2}
        self.assertFalse(A.convert_blocks_part(res, False))
        self.assertFalse(A.convert_blocks_part(res, True))

    def test_no_reason(self):
        self.assertFalse(A.convert_blocks_part({'comp': Comp('K')}, False))

    def test_reason_without_comp_blocks(self):
        res = {'convert_reason': 'Körper liegt in der Wurzelkomponente', 'comp': None, 'body': Body('x')}
        self.assertTrue(A.convert_blocks_part(res, False))


class TestFallback(unittest.TestCase):
    def test_copy_ok(self):
        g = {'comp': Comp('K7'), 'body': Body('K7')}
        self.assertEqual(A.copy_fallback_action(g, Comp('Copy'), Body('C'), ''), 'copy')

    def test_fallback_original(self):
        g = {'comp': Comp('K7'), 'body': Body('K7'), 'key': 'A:id-K7/K7'}
        self.assertEqual(A.copy_fallback_action(g, None, None, 'CIRCULAR_DEPENDENCY'), 'fallback')

    def test_skip_no_original(self):
        g = {'comp': None, 'body': None, 'key': 'A:x'}
        self.assertEqual(A.copy_fallback_action(g, None, None, 'fail'), 'skip')

    def test_dedup_same_comp(self):
        c = Comp('S2', 'id-S2')
        g1 = {'comp': c, 'body': Body('S2'), 'key': 'A:id-S2/S2'}
        g2 = {'comp': c, 'body': Body('S2 DG'), 'key': 'A:id-S2/S2 DG'}
        seen = set()
        self.assertEqual(A.copy_fallback_action(g1, None, None, 'CIRCULAR', seen), 'fallback')
        seen.add(A.fallback_dedupe_key(g1))
        self.assertEqual(A.copy_fallback_action(g2, None, None, 'CIRCULAR', seen), 'dup')
        self.assertEqual(A.fallback_dedupe_key(g1), A.fallback_dedupe_key(g2))


class TestPartUsablePolicy(unittest.TestCase):
    """Spiegelt LazyNest._part_usable ohne Fusion-Import."""

    def usable(self, res, convert=False):
        if res.get('error') or 'outer' not in res:
            return False
        if A.convert_blocks_part(res, convert):
            return False
        return True

    def test_s2_included(self):
        res = {'convert_reason': 'Komponente „S2“ enthält 2 Körper', 'comp': Comp('S2'),
               'body': Body('S2 DG'), 'outer': [[0, 0], [1, 0], [1, 1], [0, 1]], 'error': ''}
        self.assertTrue(self.usable(res, False))
        self.assertTrue(self.usable(res, True))

    def test_root_skipped_until_convert(self):
        res = {'convert_reason': 'Körper liegt in der Wurzelkomponente', 'needs_convert': True,
               'comp': None, 'body': Body('x'), 'outer': [[0, 0], [1, 0], [1, 1], [0, 1]], 'error': ''}
        self.assertFalse(self.usable(res, False))
        self.assertTrue(self.usable(res, True))

    def test_error_still_blocks(self):
        res = {'error': 'kein Volumenkörper', 'outer': [[0, 0]]}
        self.assertFalse(self.usable(res, False))

    def test_missing_outer_blocks(self):
        res = {'comp': Comp('K7'), 'body': Body('K7')}
        self.assertFalse(self.usable(res, False))


class TestI18n(unittest.TestCase):
    KEYS = ('hint.convert_bodies', 'warn.multibody_include', 'warn.copy_fallback',
            'title.convert_bodies', 'label.convert_bodies')

    def test_keys_all_langs(self):
        for lang in ('de', 'en', 'es', 'fr'):
            for key in self.KEYS:
                s = ca_i18n.t(key, lang)
                self.assertNotEqual(s, key, msg='%s %s' % (lang, key))
                self.assertTrue(s)
            fb = ca_i18n.t('warn.copy_fallback', lang, err='CIRCULAR_DEPENDENCY')
            self.assertIn('CIRCULAR_DEPENDENCY', fb)
        self.assertIn('mitgenestet', ca_i18n.t('hint.convert_bodies', 'de'))
        self.assertIn('Original', ca_i18n.t('warn.copy_fallback', 'de', err='x'))


if __name__ == '__main__':
    unittest.main()
