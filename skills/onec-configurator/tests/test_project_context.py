"""Behavioral tests against synthetic Designer sources, not platform tests."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

XML = '''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" version="2.20">
<Configuration uuid="11111111-1111-1111-1111-111111111111"><Properties>
<Name>Учебная</Name><Version>9.0</Version><CompatibilityMode>Version8_3_12</CompatibilityMode>
</Properties><ChildObjects><CommonModule>Расчеты</CommonModule></ChildObjects></Configuration>
</MetaDataObject>'''


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'Configuration.xml').write_text(XML, encoding='utf-8')

    def run_cli(self, *args):
        run = subprocess.run([sys.executable, str(SCRIPTS / 'project_context.py'), *args],
                             capture_output=True, text=True)
        self.assertTrue(run.stdout.strip(), run.stderr)
        return run.returncode, json.loads(run.stdout)

    def write_bsl(self, text):
        p = self.root / 'CommonModules' / 'Расчеты' / 'Ext' / 'Module.bsl'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
        return p

    def test_index_keeps_three_versions_distinct(self):
        code, d = self.run_cli('index', str(self.root))
        self.assertEqual(code, 0)
        self.assertEqual(d['objects'][0]['format_version'], '2.20')
        self.assertEqual(d['objects'][0]['properties']['Version'], '9.0')
        self.assertEqual(d['objects'][0]['properties']['CompatibilityMode'], 'Version8_3_12')
        self.assertEqual(d['platform_validation'], 'not_run')

    def test_nested_names_are_not_top_level_names(self):
        (self.root / 'Configuration.xml').write_text(XML.replace('<Version>9.0</Version>',
            '<Version>9.0</Version><Synonym><item><Name>Wrong</Name></item></Synonym>'), encoding='utf-8')
        _, d = self.run_cli('index', str(self.root))
        self.assertEqual(d['objects'][0]['name'], 'Учебная')

    def test_index_collects_object_children(self):
        content = XML.replace('Configuration', 'Catalog').replace('<CommonModule>Расчеты</CommonModule>',
            '<Attribute uuid="22222222-2222-2222-2222-222222222222"><Properties><Name>ИНН</Name></Properties></Attribute>')
        (self.root / 'Catalog.xml').write_text(content, encoding='utf-8')
        _, d = self.run_cli('index', str(self.root))
        obj = next(x for x in d['objects'] if x['kind'] == 'Catalog')
        self.assertEqual(obj['children'][0]['name'], 'ИНН')

    def test_namespace_mismatch_is_not_designer_object(self):
        (self.root / 'Configuration.xml').write_text(XML.replace('http://v8.1c.ru/8.3/MDClasses', 'urn:foreign'), encoding='utf-8')
        _, d = self.run_cli('index', str(self.root))
        self.assertEqual(d['objects'], [])

    def test_malformed_input_is_partial_and_nonzero(self):
        (self.root / 'broken.xml').write_text('<broken>', encoding='utf-8')
        code, d = self.run_cli('index', str(self.root))
        self.assertNotEqual(code, 0)
        self.assertEqual(d['status'], 'partial')
        self.assertTrue(d['findings'])

    def test_empty_tree_not_a_successful_index(self):
        (self.root / 'Configuration.xml').unlink()
        _, d = self.run_cli('index', str(self.root))
        self.assertEqual(d['status'], 'no_input')

    def test_api_multiline_export_and_real_signature(self):
        self.write_bsl('// комментарий\n&НаСервере\nФункция Рассчитать(\n Сумма,\n Вид = "Цена)") Экспорт\n Возврат Сумма;\nКонецФункции\n')
        _, d = self.run_cli('api', str(self.root), '--name', 'рассчитать', '--exported-only')
        self.assertEqual(len(d['definitions']), 1)
        self.assertEqual(d['definitions'][0]['line'], 3)
        self.assertIn('Цена)', d['definitions'][0]['signature'])
        self.assertEqual(d['definitions'][0]['directives'], ['&НаСервере'])

    def test_api_ignores_strings_and_comments(self):
        self.write_bsl('// Функция Фальшивая() Экспорт\nТекст = "Функция Мнимая() Экспорт\n|КонецФункции";\nПроцедура Реальная() Экспорт\nКонецПроцедуры\n')
        _, d = self.run_cli('api', str(self.root))
        self.assertEqual([x['name'] for x in d['definitions']], ['Реальная'])

    def test_api_private_and_english(self):
        self.write_bsl('Procedure Private()\nEndProcedure\nFunction Public(Val Value) Export\nReturn Value;\nEndFunction\n')
        _, d = self.run_cli('api', str(self.root), '--exported-only')
        self.assertEqual([x['name'] for x in d['definitions']], ['Public'])

    def test_extension_directive_keeps_intercepted_name(self):
        self.write_bsl('&После("ИсходныйМетод")\nПроцедура Перехват() Экспорт\nКонецПроцедуры\n')
        _, d = self.run_cli('api', str(self.root))
        self.assertEqual(d['definitions'][0]['directives'], ['&После("ИсходныйМетод")'])

    def test_references_are_case_insensitive_code_only(self):
        self.write_bsl('Модуль.Метод();\n// Модуль.Метод();\nТекст = "Модуль.Метод()";\nДругойМодуль.Метод();\n')
        _, d = self.run_cli('references', str(self.root), '--symbol', 'модуль.метод')
        self.assertEqual([x['line'] for x in d['matches']], [1])
        self.assertEqual(d['analysis'], 'lexical_not_semantic')

    def test_reference_text_mode_includes_query_strings(self):
        self.write_bsl('Текст = "ВЫБРАТЬ Справочник.Товары";\n')
        _, d = self.run_cli('references', str(self.root), '--symbol', 'Справочник.Товары', '--include-text')
        self.assertEqual(len(d['matches']), 1)

    def test_limit_reports_truncation(self):
        self.write_bsl('Процедура А() Экспорт\nКонецПроцедуры\nПроцедура Б() Экспорт\nКонецПроцедуры\n')
        _, d = self.run_cli('api', str(self.root), '--limit', '1')
        self.assertTrue(d['truncated'])
        self.assertEqual(d['total'], 2)

    def test_unknown_encoding_does_not_silently_drop_file(self):
        self.write_bsl('x').write_bytes('Процедура А()'.encode('cp1251'))
        code, d = self.run_cli('api', str(self.root))
        self.assertNotEqual(code, 0)
        self.assertEqual(d['status'], 'partial')

    def test_doctor_does_not_execute_selected_program(self):
        fake = self.root / 'fake-1cv8'
        marker = self.root / 'executed'
        fake.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
        fake.chmod(0o755)
        profile = self.root / 'profile.json'
        profile.write_text(json.dumps({'schema': 1, 'platform': {'path': str(fake), 'version': '8.3.27.2342'}}))
        _, d = self.run_cli('doctor', str(self.root), '--profile', str(profile))
        self.assertFalse(marker.exists())
        self.assertEqual(d['platform']['version_declared'], '8.3.27.2342')
        self.assertEqual(d['platform']['version_verification'], 'not_run')
        self.assertEqual(d['configurations'][0]['properties']['Version'], '9.0')

    def test_doctor_missing_required_platform_is_nonzero(self):
        code, d = self.run_cli('doctor', str(self.root), '--require', 'platform')
        self.assertNotEqual(code, 0)
        self.assertIn('platform', d['missing_required'])

    def test_doctor_unknown_profile_fields_fail_without_echoing_value(self):
        p = self.root / 'p.json'; p.write_text('{"schema":1,"password":"never-echo-this"}')
        code, d = self.run_cli('doctor', str(self.root), '--profile', str(p))
        self.assertNotEqual(code, 0)
        self.assertNotIn('never-echo-this', json.dumps(d))

    def test_inspection_preserves_all_source_bytes(self):
        p = self.write_bsl('Процедура А() Экспорт\r\nКонецПроцедуры\r\n')
        original = p.read_bytes()
        for action in ['index', 'api', 'doctor']:
            self.run_cli(action, str(self.root))
        self.assertEqual(p.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
