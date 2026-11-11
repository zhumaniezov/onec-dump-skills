import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'dump_tool.py'


class DumpToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, data):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
        return p

    def run_tool(self, *args, code=0):
        p = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                           capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(p.returncode, code, p.stderr + p.stdout)
        return json.loads(p.stdout)

    def test_inventory_reports_real_config_and_edt_marker(self):
        self.put('src/cf/Configuration.xml', '<MetaDataObject xmlns="urn:md"><Configuration uuid="123"><Properties><Name>Тест</Name><CompatibilityMode>Version8_3_12</CompatibilityMode></Properties></Configuration></MetaDataObject>')
        self.put('src/edt/Configuration.mdo', '<test/>')
        r = self.run_tool('inventory', self.root)
        self.assertEqual(r['configs'][0]['properties']['Name'], 'Тест')
        self.assertEqual(r['configs'][0]['properties']['CompatibilityMode'], 'Version8_3_12')
        self.assertTrue(r['edt_markers'])
        self.assertEqual(r['platform_validation'], 'not_run')

    def test_inspect_handles_namespaces_and_bounded_output(self):
        p = self.put('Catalogs/Тест.xml', '<MetaDataObject xmlns="urn:md"><Catalog uuid="abc"><Properties><Name>Тест</Name></Properties><ChildObjects><Attribute uuid="def"><Properties><Name>Поле</Name><Type><Type>xs:string</Type></Type></Properties></Attribute></ChildObjects></Catalog></MetaDataObject>')
        r = self.run_tool('inspect', p, '--limit', 4)
        self.assertEqual(r['root'], 'MetaDataObject')
        self.assertEqual(len(r['nodes']), 4)
        self.assertTrue(r['truncated'])

    def test_audit_rejects_broken_xml(self):
        self.put('a.xml', '<a>')
        r = self.run_tool('audit', self.root, code=1)
        self.assertEqual(r['counts']['errors'], 1)
        self.assertTrue(any(x['kind'] == 'xml_parse' for x in r['findings']))

    def test_audit_rejects_dtd_without_expansion(self):
        self.put('a.xml', '<!DOCTYPE a [<!ENTITY x "hello">]><a>&x;</a>')
        r = self.run_tool('audit', self.root, code=1)
        self.assertTrue(any(x['kind'] == 'xml_parse' for x in r['findings']))

    def test_utf16_xml_with_dtd_is_rejected(self):
        self.put('a.xml', '<!DOCTYPE a [<!ENTITY x "hello">]><a>&x;</a>'.encode('utf-16'))
        self.run_tool('audit', self.root, code=1)

    def test_audit_detects_conflict_markers(self):
        self.put('Module.bsl', '<<<<<<< HEAD\nА = 1;\n=======\nА = 2;\n>>>>>>> feature\n')
        r = self.run_tool('audit', self.root, code=1)
        self.assertTrue(any(x['kind'] == 'merge_conflict' for x in r['findings']))

    def test_audit_reports_encoding_and_mixed_newlines(self):
        self.put('Module.bsl', b'\xef\xbb\xbfA = 1;\r\nB = 2;\n')
        r = self.run_tool('audit', self.root)
        self.assertTrue(any(x['kind'] == 'mixed_eol' for x in r['findings']))
        self.assertEqual(r['status'], 'warnings')

    def test_cp1251_is_reported_not_guessed(self):
        self.put('Module.bsl', 'Процедура Тест()'.encode('cp1251'))
        r = self.run_tool('audit', self.root, code=1)
        self.assertTrue(any(x['kind'] == 'encoding' for x in r['findings']))

    def test_checks_do_not_modify_source_bytes(self):
        p = self.put('x.xml', b'\xef\xbb\xbf<a z="2" a="1">\r\n <b />\r\n</a>')
        before = p.read_bytes()
        self.run_tool('audit', self.root)
        self.run_tool('inspect', p)
        self.run_tool('snapshot', self.root)
        self.assertEqual(p.read_bytes(), before)

    def test_snapshot_compare_reports_identity_and_eol_changes(self):
        p = self.put('Object.xml', '<a uuid="00000000-0000-0000-0000-000000000001">\r\n</a>')
        before = self.run_tool('snapshot', self.root)
        p.write_bytes(b'<a uuid="00000000-0000-0000-0000-000000000002">\n</a>')  # байты: на Windows текстовый режим вернул бы CRLF
        after = self.run_tool('snapshot', self.root)
        b = self.put('before.json', json.dumps(before))
        a = self.put('after.json', json.dumps(after))
        r = self.run_tool('compare', b, a)
        self.assertIn('xml_ids', r['changes'][0]['review'])
        self.assertIn('eol', r['changes'][0]['review'])
        self.assertEqual(r['changes'][0]['kind'], 'modified')

    def test_snapshot_compare_reports_add_delete(self):
        p = self.put('old.bsl', 'A = 1;')
        before = self.run_tool('snapshot', self.root)
        p.unlink()
        self.put('new.bsl', 'A = 1;')
        after = self.run_tool('snapshot', self.root)
        b = self.put('b.json', json.dumps(before)); a = self.put('a.json', json.dumps(after))
        r = self.run_tool('compare', b, a)
        self.assertEqual({x['kind'] for x in r['changes']}, {'added', 'deleted'})

    def test_binary_files_and_git_are_not_read(self):
        self.put('base.cf', b'\x00\xff')
        self.put('.git/broken.xml', '<')
        r = self.run_tool('audit', self.root)
        self.assertEqual(r['counts']['checked'], 0)
        self.assertEqual(r['status'], 'no_input')

    def test_symlink_is_skipped(self):
        target = self.put('real.bsl', 'A = 1;')
        try:
            (self.root / 'link.bsl').symlink_to(target)
        except OSError:
            self.skipTest('symlinks unavailable')
        r = self.run_tool('audit', self.root)
        self.assertEqual(r['counts']['checked'], 1)
        self.assertTrue(any(x['kind'] == 'symlink_skipped' for x in r['findings']))

    def test_size_limit_is_not_a_successful_check(self):
        self.put('huge.bsl', 'A' * (1024 * 1024 + 1))
        r = self.run_tool('audit', self.root, '--max-mib', 1)
        self.assertEqual(r['counts']['skipped'], 1)
        self.assertEqual(r['status'], 'partial')

    def test_missing_root_is_explicit_error(self):
        r = self.run_tool('audit', self.root / 'absent', code=2)
        self.assertEqual(r['status'], 'error')

    def test_invalid_manifest_is_explicit_error(self):
        p = self.put('bad.json', '{}')
        self.run_tool('compare', p, p, code=2)

    def test_windows_reparse_point_is_skipped_without_is_junction(self):
        spec = importlib.util.spec_from_file_location('dump_tool', SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        class Entry:
            st_file_attributes = 1024
        class FakePath:
            def is_symlink(self):
                return False
        with patch.object(mod.os, 'lstat', return_value=Entry()):
            self.assertTrue(mod.is_link(FakePath()))


if __name__ == '__main__':
    unittest.main()
