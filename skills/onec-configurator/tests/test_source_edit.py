"""Source-edit tests use synthetic 1C-style metadata, not a compiled configuration."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'source_edit.py'
# Synthetic fixture: namespace, UUID, Cyrillic before target, comments and child metadata.
XML = ('<?xml version="1.0" encoding="UTF-8"?>\r\n'
       '<MetaDataObject xmlns="urn:synthetic:md"><Catalog uuid="unchanged">\r\n'
       '<Properties><!--Свойства--><Name>Справочник</Name><Version>1 &amp; 2</Version></Properties>\r\n'
       '<ChildObjects><Attribute><Properties><Version>child</Version></Properties></Attribute></ChildObjects>'
       '</Catalog></MetaDataObject>\r\n')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class SourceEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.root = self.home / 'project'
        self.root.mkdir()
        self.plan_file = self.home / 'plan.json'
        self.request_file = self.home / 'request.json'
        self.journal = self.home / 'journal'

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode('utf-8') if isinstance(content, str) else content)
        return path

    def run_tool(self, *args, code=0):
        proc = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(proc.returncode, code, proc.stderr + proc.stdout)
        self.assertTrue(proc.stdout.strip(), 'source-edit must emit JSON: ' + proc.stderr)
        return json.loads(proc.stdout)

    def replacement(self, path='Module.bsl', old='Старое', new='Новое', **extra):
        return dict(op='replace-text', path=path, expected_sha256=sha((self.root / path).read_bytes()),
                    old=old, new=new, **extra)

    def make_plan(self, operations, code=0, **extra):
        request = dict(schema=1, operations=operations, **extra)
        self.request_file.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
        return self.run_tool('plan', '--project', self.root, '--request', self.request_file,
                             '--out', self.plan_file, code=code)

    def apply(self, code=0, enabled=True):
        args = ['apply', '--project', self.root, '--plan', self.plan_file, '--journal', self.journal]
        if enabled:
            args.append('--apply')
        return self.run_tool(*args, code=code)

    def load_module(self):
        spec = importlib.util.spec_from_file_location('source_edit', SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', [str(SCRIPT.parent), *sys.path]):
            spec.loader.exec_module(mod)
        return mod

    def test_plan_show_and_dry_apply_never_change_sources(self):
        path = self.put('Module.bsl', b'\xef\xbb\xbf// test\r\n' + 'Старое();\r\n'.encode())
        before = path.read_bytes()
        self.make_plan([self.replacement()])
        shown = self.run_tool('show', '--plan', self.plan_file)
        self.assertIn('-Старое();', shown['changes'][0]['diff'])
        self.assertEqual(path.read_bytes(), before)
        self.apply(enabled=False)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.journal.exists())

    def test_successful_sequential_multi_file_edit_and_create(self):
        first = self.put('Module.bsl', 'Старое();\r\n')
        second = self.put('Catalog.xml', b'\xef\xbb\xbf' + XML.encode())
        ops = [self.replacement(), self.replacement(old='Новое', new='Итог'),
               dict(op='xml-set-property', path='Catalog.xml', expected_sha256=sha(second.read_bytes()),
                    property='Version', expected_old='1 & 2', new='<3> & "x"'),
               dict(op='create-text', path='New.os', content='Привет\nМир\n', encoding='utf-16le-bom', eol='CRLF')]
        self.make_plan(ops)
        result = self.apply()
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(first.read_bytes(), 'Итог();\r\n'.encode())
        expected = b'\xef\xbb\xbf' + XML.replace('1 &amp; 2', '&lt;3&gt; &amp; "x"').encode()
        self.assertEqual(second.read_bytes(), expected)
        self.assertEqual((self.root / 'New.os').read_bytes(), b'\xff\xfe' + 'Привет\r\nМир\r\n'.encode('utf-16le'))
        self.assertEqual(json.loads((self.journal / 'report.json').read_text())['status'], 'applied')
        self.assertTrue(any((self.journal / 'backups').iterdir()))

    def test_preserves_utf16_both_endiannesses(self):
        for encoding, bom in [('utf-16le', b'\xff\xfe'), ('utf-16be', b'\xfe\xff')]:
            with self.subTest(encoding=encoding):
                path = self.put('Module.bsl', bom + 'Старое\r\n'.encode(encoding))
                self.make_plan([self.replacement()])
                self.apply()
                self.assertEqual(path.read_bytes(), bom + 'Новое\r\n'.encode(encoding))
                self.journal.rename(self.home / encoding)
                self.plan_file.unlink()

    def test_xml_comments_scalar_entities_and_utf16_offsets(self):
        xml = XML.replace('encoding="UTF-8"', 'encoding="UTF-16"').replace('1 &amp; 2', '1<!--keep--> &amp; 2')
        path = self.put('Catalog.xml', b'\xfe\xff' + xml.encode('utf-16be'))
        self.make_plan([dict(op='xml-set-property', path='Catalog.xml', expected_sha256=sha(path.read_bytes()),
                             property='Version', expected_old='1 & 2', new='Три & <четыре>')])
        self.apply()
        expected = xml.replace('1<!--keep--> &amp; 2', 'Три &amp; &lt;четыре&gt;<!--keep-->')
        self.assertEqual(path.read_bytes(), b'\xfe\xff' + expected.encode('utf-16be'))

    def test_rejects_ambiguous_missing_and_non_scalar_properties(self):
        for body in ['<Version>1</Version><Version>1</Version>', '<Other>1</Other>',
                     '<Version><item>1</item></Version>']:
            with self.subTest(body=body):
                path = self.put('x.xml', '<MetaDataObject><Catalog><Properties>' + body + '</Properties></Catalog></MetaDataObject>')
                self.make_plan([dict(op='xml-set-property', path='x.xml', expected_sha256=sha(path.read_bytes()),
                                     property='Version', expected_old='1', new='2')], code=2)

    def test_rejects_bad_xml_and_dtd_before_and_after_edit(self):
        for source in ['<x>', '<!DOCTYPE a [<!ENTITY x "hello">]><a>&x;</a>', '<x/>']:
            with self.subTest(source=source):
                self.put('x.xml', source)
                self.make_plan([self.replacement('x.xml', source, '<x>')], code=2)

    def test_request_schema_counts_hashes_suffixes_and_unknown_fields(self):
        self.put('Module.bsl', 'Старое')
        for field, value in [('expected_count', True), ('expected_count', 0), ('expected_count', -1),
                             ('expected_count', 1.5), ('expected_sha256', 'broken'), ('unexpected', 2)]:
            with self.subTest(field=field, value=value):
                op = self.replacement()
                op[field] = value
                self.make_plan([op], code=2)
        self.make_plan([self.replacement(old='absent')], code=2)
        self.make_plan([self.replacement()], code=2, schema_version=3)
        self.request_file.write_text('{"schema":true,"operations":[]}', encoding='utf-8')
        self.run_tool('plan', '--project', self.root, '--request', self.request_file, '--out', self.plan_file, code=2)
        self.put('x.txt', 'Старое')
        self.make_plan([self.replacement('x.txt')], code=2)

    def test_stale_second_file_leaves_first_untouched_and_no_journal(self):
        first = self.put('Module.bsl', 'Старое')
        second = self.put('other.bsl', 'Старое')
        self.make_plan([self.replacement(), self.replacement('other.bsl')])
        second.write_text('external', encoding='utf-8')
        self.apply(code=2)
        self.assertEqual(first.read_text(), 'Старое')
        self.assertFalse(self.journal.exists())

    def test_tampered_payload_diff_hash_or_digest_is_rejected(self):
        self.put('Module.bsl', 'Старое')
        self.make_plan([self.replacement()])
        original = json.loads(self.plan_file.read_text())
        for key, value in [('after_b64', base64.b64encode(b'evil').decode()), ('diff', 'wrong'),
                           ('after_sha256', '0' * 64)]:
            with self.subTest(key=key):
                plan = json.loads(json.dumps(original))
                plan['changes'][0][key] = value
                # Even a refreshed accidental-corruption checksum cannot hide inconsistent fields.
                mod = self.load_module()
                plan['integrity_sha256'] = mod.plan_digest(plan)
                self.plan_file.write_text(json.dumps(plan))
                self.run_tool('show', '--plan', self.plan_file, code=2)
                self.apply(code=2)
        original['integrity_sha256'] = '0' * 64
        self.plan_file.write_text(json.dumps(original))
        self.run_tool('show', '--plan', self.plan_file, code=2)

    def test_paths_outside_windows_git_links_and_case_collisions(self):
        self.put('Module.bsl', 'Старое')
        for name in ['../escape.bsl', '/tmp/escape.bsl', r'C:\temp\x.bsl', 'C:x.bsl',
                     r'\\server\share\x.bsl', 'folder\\x.bsl', '.git/x.bsl', 'a/../x.bsl']:
            with self.subTest(name=name):
                self.make_plan([dict(op='create-text', path=name, content='x', encoding='utf-8', eol='LF')], code=2)
        self.make_plan([dict(op='create-text', path='module.bsl', content='x', encoding='utf-8', eol='LF')], code=2)
        self.make_plan([dict(op='create-text', path=name, content='x', encoding='utf-8', eol='LF')
                        for name in ['A.bsl', 'a.bsl']], code=2)
        (self.root / 'link.bsl').symlink_to(self.root / 'Module.bsl')
        self.make_plan([dict(op='replace-text', path='link.bsl', expected_sha256=sha('Старое'.encode()), old='Старое', new='new')], code=2)
        (self.root / 'linked').symlink_to(self.root, target_is_directory=True)
        self.make_plan([dict(op='create-text', path='linked/new.bsl', content='x', encoding='utf-8', eol='LF')], code=2)

    def test_plan_output_cannot_be_a_source_target(self):
        path = self.put('Module.bsl', 'Старое')
        self.plan_file = path
        self.make_plan([self.replacement()], code=2)
        self.assertEqual(path.read_text(), 'Старое')

    def test_journal_collision_refuses_to_write(self):
        path = self.put('Module.bsl', 'Старое')
        self.make_plan([self.replacement()])
        self.journal.mkdir()
        (self.journal / 'existing').write_text('keep')
        self.apply(code=2)
        self.assertEqual(path.read_text(), 'Старое')
        self.assertEqual((self.journal / 'existing').read_text(), 'keep')

    def test_create_requires_absence_on_plan_and_apply(self):
        self.put('new.bsl', 'exists')
        create = dict(op='create-text', path='new.bsl', content='new', encoding='utf-8', eol='LF')
        self.make_plan([create], code=2)
        (self.root / 'new.bsl').unlink()
        self.make_plan([create])
        (self.root / 'new.bsl').write_text('external')
        self.apply(code=2)
        self.assertEqual((self.root / 'new.bsl').read_text(), 'external')

    def test_project_binding_duplicate_plan_and_limits(self):
        self.put('Module.bsl', 'Старое')
        self.make_plan([self.replacement()])
        other = self.home / 'other'
        other.mkdir()
        self.run_tool('apply', '--project', other, '--plan', self.plan_file, '--journal', self.journal, '--apply', code=2)
        mod = self.load_module()
        plan = json.loads(self.plan_file.read_text())
        plan['changes'].append(plan['changes'][0])
        plan['integrity_sha256'] = mod.plan_digest(plan)
        self.plan_file.write_text(json.dumps(plan))
        self.run_tool('show', '--plan', self.plan_file, code=2)
        self.put('huge.bsl', b'x' * (mod.MAX_FILE_BYTES + 1))
        self.make_plan([self.replacement('huge.bsl', 'x', 'y')], code=2)

    def test_xml_inserted_newlines_keep_document_eol(self):
        path = self.put('x.xml', XML)
        self.make_plan([dict(op='xml-set-property', path='x.xml', expected_sha256=sha(path.read_bytes()),
                             property='Version', expected_old='1 & 2', new='first\nsecond\rthird')])
        self.apply()
        self.assertEqual(path.read_bytes(), XML.replace('1 &amp; 2', 'first\r\nsecond&#13;third').encode())

    def test_git_ancestors_refused_for_project_output_and_journal(self):
        git_root = self.root / '.git' / 'nested'
        git_root.mkdir(parents=True)
        (git_root / 'x.bsl').write_text('old')
        request = dict(schema=1, operations=[dict(op='replace-text', path='x.bsl',
                       expected_sha256=sha(b'old'), old='old', new='new')])
        self.request_file.write_text(json.dumps(request))
        self.run_tool('plan', '--project', git_root, '--request', self.request_file,
                      '--out', self.plan_file, code=2)
        self.put('Module.bsl', 'Старое')
        self.make_plan([self.replacement()])
        self.journal = git_root / 'journal'
        self.apply(code=2)
        self.assertFalse(self.journal.exists())
        self.plan_file.unlink()
        self.plan_file = git_root / 'plan.json'
        self.make_plan([self.replacement()], code=2)
        self.assertFalse(self.plan_file.exists())

    def test_rechecks_target_after_preflight_and_before_publish(self):
        first = self.put('Module.bsl', 'Старое')
        second = self.put('other.bsl', 'Старое')
        self.make_plan([self.replacement(), self.replacement('other.bsl')])
        mod = self.load_module()
        real = mod.os.replace
        calls = []
        def changed_before_publish(source, destination):
            calls.append(str(destination))
            result = real(source, destination)
            if len(calls) == 1:
                second.write_text('external', encoding='utf-8')
            return result
        with patch.object(mod.os, 'replace', side_effect=changed_before_publish):
            result = mod.apply_plan(self.root, json.loads(self.plan_file.read_text()), self.journal, True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(first.read_text(), 'Старое')
        self.assertEqual(second.read_text(), 'external')

    def test_create_publication_never_overwrites_a_late_arrival(self):
        self.make_plan([dict(op='create-text', path='new.bsl', content='new', encoding='utf-8', eol='LF')])
        mod = self.load_module()
        real = mod.os.link
        def race(source, destination):
            Path(destination).write_text('external', encoding='utf-8')
            return real(source, destination)
        with patch.object(mod.os, 'link', side_effect=race):
            result = mod.apply_plan(self.root, json.loads(self.plan_file.read_text()), self.journal, True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual((self.root / 'new.bsl').read_text(), 'external')
        self.assertEqual(result['completed'], [])
        self.assertFalse(list(self.root.glob('.source-edit-*')))

    def test_failed_multi_file_apply_removes_only_own_created_file(self):
        self.put('Module.bsl', 'Старое')
        self.make_plan([dict(op='create-text', path='new.bsl', content='new', encoding='utf-8', eol='LF'),
                        self.replacement()])
        mod = self.load_module()
        with patch.object(mod.os, 'replace', side_effect=OSError('synthetic replace failure')):
            result = mod.apply_plan(self.root, json.loads(self.plan_file.read_text()), self.journal, True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['rolled_back'], ['new.bsl'])
        self.assertFalse((self.root / 'new.bsl').exists())
        self.assertFalse(list(self.root.glob('.source-edit-*')))

    def test_empty_scalar_and_default_namespace_are_preserved(self):
        xml = '<MetaDataObject xmlns="urn:m"><Catalog><Properties><Version /></Properties></Catalog></MetaDataObject>'
        path = self.put('x.xml', xml)
        self.make_plan([dict(op='xml-set-property', path='x.xml', expected_sha256=sha(path.read_bytes()),
                             property='Version', expected_old='', new='1')])
        self.apply()
        self.assertEqual(path.read_text(), xml.replace('<Version />', '<Version >1</Version>'))

    def test_cleanup_failure_after_create_publication_still_rolls_back(self):
        self.make_plan([dict(op='create-text', path='new.bsl', content='new', encoding='utf-8', eol='LF')])
        mod = self.load_module()
        real = Path.unlink
        calls = []
        def fail_cleanup(path, *args, **kwargs):
            if path.name.startswith('.source-edit-') and not calls:
                calls.append(str(path))
                raise OSError('synthetic temp cleanup failure')
            return real(path, *args, **kwargs)
        with patch.object(Path, 'unlink', new=fail_cleanup):
            result = mod.apply_plan(self.root, json.loads(self.plan_file.read_text()), self.journal, True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['rolled_back'], ['new.bsl'])
        self.assertFalse((self.root / 'new.bsl').exists())
        self.assertIn(calls[0], result['temporary_files'])

    def test_json_and_plan_size_limits_fail_without_mutating(self):
        path = self.put('Module.bsl', 'Старое')
        mod = self.load_module()
        self.plan_file.write_bytes(b'{}')
        with self.plan_file.open('r+b') as stream:
            stream.truncate(mod.MAX_PLAN_BYTES + 1)
        self.run_tool('show', '--plan', self.plan_file, code=2)
        self.plan_file.unlink()
        self.request_file.write_text('{"schema":1,"schema":1,"operations":[]}')
        self.run_tool('plan', '--project', self.root, '--request', self.request_file,
                      '--out', self.plan_file, code=2)
        self.request_file.write_text('{"schema":2,"operations":[]}')
        self.run_tool('plan', '--project', self.root, '--request', self.request_file,
                      '--out', self.plan_file, code=2)
        self.run_tool('apply', '--project', self.root, code=2)
        self.assertEqual(path.read_text(), 'Старое')
        self.assertFalse(self.journal.exists())

    def test_utf16_dtd_and_wrong_baseline_hash_are_rejected(self):
        for raw in [b'\xff\xfe' + '<!DOCTYPE x [<!ENTITY e "data">]><x>&e;</x>'.encode('utf-16le'),
                    b'\xfe\xff' + '<!DOCTYPE x [<!ENTITY e "data">]><x>&e;</x>'.encode('utf-16be')]:
            with self.subTest(raw=raw[:2]):
                self.put('x.xml', raw)
                self.make_plan([self.replacement('x.xml', 'data', 'new')], code=2)
        self.put('Module.bsl', 'Старое')
        op = self.replacement()
        op['expected_sha256'] = '0' * 64
        self.make_plan([op], code=2)

    def test_rehashed_after_payload_cannot_disagree_with_operations(self):
        self.put('Module.bsl', 'Старое')
        self.make_plan([self.replacement()])
        mod = self.load_module()
        plan = json.loads(self.plan_file.read_text())
        plan['changes'][0]['after_b64'] = base64.b64encode(b'changed').decode()
        plan['changes'][0]['after_sha256'] = sha(b'changed')
        plan['integrity_sha256'] = mod.plan_digest(plan)
        self.plan_file.write_text(json.dumps(plan))
        self.run_tool('show', '--plan', self.plan_file, code=2)
        self.apply(code=2)

    def test_ordinary_write_failure_rolls_back_own_completed_writes(self):
        first = self.put('Module.bsl', 'Старое')
        second = self.put('other.bsl', 'Старое')
        self.make_plan([self.replacement(), self.replacement('other.bsl')])
        mod = self.load_module()
        real = mod.os.replace
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(str(args[1]))
            if len(calls) == 2:
                raise OSError('synthetic disk failure')
            return real(*args, **kwargs)
        with patch.object(mod.os, 'replace', side_effect=fail_second):
            result = mod.apply_plan(self.root, json.loads(self.plan_file.read_text()), self.journal, True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['rollback_conflicts'], [])
        self.assertEqual(first.read_text(), 'Старое')
        self.assertEqual(second.read_text(), 'Старое')
        self.assertEqual(result['rolled_back'], ['Module.bsl'])

    def test_rollback_preserves_external_changes_and_recovery_backup(self):
        first = self.put('Module.bsl', 'Старое')
        self.put('other.bsl', 'Старое')
        self.make_plan([self.replacement(), self.replacement('other.bsl')])
        mod = self.load_module()
        real = mod.os.replace
        calls = []
        def external_change(*args, **kwargs):
            calls.append(str(args[1]))
            if len(calls) == 2:
                first.write_text('external', encoding='utf-8')
                raise OSError('synthetic disk failure')
            return real(*args, **kwargs)
        with patch.object(mod.os, 'replace', side_effect=external_change):
            result = mod.apply_plan(self.root, json.loads(self.plan_file.read_text()), self.journal, True)
        self.assertEqual(first.read_text(), 'external')
        self.assertEqual(result['rollback_conflicts'][0]['path'], 'Module.bsl')
        self.assertTrue(Path(result['rollback_conflicts'][0]['backup']).is_file())
        self.assertEqual(json.loads((self.journal / 'report.json').read_text())['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
