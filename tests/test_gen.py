"""Тесты onec_gen.py: наши команды поверх движка генераторов, резервная копия, пробный запуск, отмена.

Движку нужна библиотека lxml; без неё тесты, запускающие движок, пропускаются (в CI она ставится).
"""
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(os.path.dirname(HERE), "skills", "onec-configurator")
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import onec_gen as og  # noqa: E402

LXML = og.has_lxml()


def run(*argv, cwd=None):
    o = io.StringIO()
    old = os.getcwd()
    try:
        if cwd:
            os.chdir(cwd)
        with redirect_stdout(o), redirect_stderr(o):
            rc = og.main(list(argv))
    finally:
        os.chdir(old)
    return rc, o.getvalue()


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class EngineIntegrityTest(unittest.TestCase):
    def test_manifest_matches_files(self):
        m = og.load_manifest()
        self.assertEqual(m["license"], "MIT")
        for rel, h in m["files"].items():
            self.assertEqual(sha(os.path.join(og.ENGINE_DIR, *rel.split("/"))), h, rel)
        for e in og.ENGINES:
            self.assertIn(e, m["entries"])
            self.assertTrue(os.path.isfile(og.engine_script(e)), e)
        self.assertTrue(os.path.isfile(os.path.join(og.ENGINE_DIR, "LICENSE")))

    def test_every_command_has_reference(self):
        for key in og.COMMANDS:
            self.assertTrue(os.path.isfile(os.path.join(SKILL, "references", "gen", key.replace(" ", "-") + ".md")), key)

    def test_references_use_our_names(self):
        """В справочниках — только наши команды, без имён и способов вызова источника."""
        names = sorted({v[0] for v in og.COMMANDS.values()} | {"epf-build", "erf-build", "db-load-xml", "db-update"},
                       key=len, reverse=True)
        rx = re.compile(r"\.ps1\b|powershell|\.claude/skills|SKILL\.md|(?<![-\w/.])(?<!profile )(%s)\b(?![-\w])(?!\.\w)"
                        % "|".join(map(re.escape, names)))
        bad = []
        for dp, _dn, fns in os.walk(os.path.join(SKILL, "references", "gen")):
            for fn in fns:
                with open(os.path.join(dp, fn), encoding="utf-8") as f:
                    for n, line in enumerate(f, 1):
                        if rx.search(line):
                            bad.append("%s:%d: %s" % (fn, n, line.strip()[:100]))
        self.assertEqual(bad, [])

    def test_unknown_command_and_list(self):
        rc, out = run("object", "fly")
        self.assertEqual(rc, 2)
        rc, out = run("list")
        self.assertEqual(rc, 0)
        self.assertIn("ext intercept", out)

    def test_lxml_missing_message(self):
        with mock.patch.object(og, "has_lxml", return_value=False):
            rc, out = run("object", "info", "-ObjectPath", "x")
        self.assertEqual(rc, 2)
        self.assertIn("pip install lxml", out)


@unittest.skipUnless(LXML, "нет lxml")
class GenScenarioTest(unittest.TestCase):
    """Новая конфигурация -> объект -> реквизит -> расширение -> заимствование -> перехватчик."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.p)
        rc, out = run("config", "new", "-Name", "Тест", "-OutputDir", "cf", "-FormatVersion", "2.20",
                      "-CompatibilityMode", "Version8_3_24", cwd=self.p)
        self.assertEqual(rc, 0, out)
        js = os.path.join(self.tmp.name, "cat.json")
        with open(js, "w", encoding="utf-8") as f:
            json.dump({"type": "Catalog", "name": "Товары", "attributes": ["Артикул: Строка(25)"]}, f, ensure_ascii=False)
        rc, out = run("object", "new", "-JsonPath", js, "-OutputDir", "cf", cwd=self.p)
        self.assertEqual(rc, 0, out)
        self.assertIn("добавлен  Catalogs/Товары.xml", out)
        self.cat = os.path.join(self.p, "cf", "Catalogs", "Товары.xml")

    def tearDown(self):
        self.tmp.cleanup()

    def gen(self, *argv):
        return run(*argv, cwd=self.p)

    def test_dry_run_changes_nothing(self):
        before = sha(self.cat)
        rc, out = self.gen("object", "edit", "-ObjectPath", "cf/Catalogs/Товары.xml", "-Operation", "add-attribute",
                           "-Value", "Вес: Число(10,3)", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("+", out)
        self.assertIn("Вес", out)
        self.assertIn("исходники не изменены", out)
        self.assertEqual(sha(self.cat), before)

    def test_edit_and_undo(self):
        before = sha(self.cat)
        rc, out = self.gen("object", "edit", "-ObjectPath", "cf/Catalogs/Товары.xml", "-Operation", "add-attribute",
                           "-Value", "Вес: Число(10,3)")
        self.assertEqual(rc, 0, out)
        self.assertIn("изменён  Catalogs/Товары.xml", out)
        self.assertNotEqual(sha(self.cat), before)
        rc, out = self.gen("undo")
        self.assertEqual(rc, 0, out)
        self.assertEqual(sha(self.cat), before)
        rc, out = self.gen("undo")                    # отменённое повторно не отменяется; остаётся object new
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.cat))   # новый объект убран, каталог тоже
        self.assertFalse(os.path.exists(os.path.join(self.p, "cf", "Catalogs")))

    def test_undo_refuses_after_later_edit(self):
        self.gen("object", "edit", "-ObjectPath", "cf/Catalogs/Товары.xml", "-Operation", "add-attribute", "-Value", "Вес: Число(10,3)")
        with open(self.cat, "ab") as f:
            f.write(b" ")
        rc, out = self.gen("undo")
        self.assertEqual(rc, 2)
        self.assertIn("изменён после запуска", out)

    def test_extension_borrow_and_intercept(self):
        mod = os.path.join(self.p, "cf", "Catalogs", "Товары", "Ext", "ObjectModule.bsl")
        with open(mod, "wb") as f:
            f.write("﻿Процедура ПередЗаписью(Отказ)\r\nКонецПроцедуры\r\n".encode("utf-8"))
        rc, out = self.gen("ext", "new", "-Name", "Доработки", "-OutputDir", "ext/Доработки", "-ConfigPath", "cf")
        self.assertEqual(rc, 0, out)
        rc, out = self.gen("ext", "borrow", "-ExtensionPath", "ext/Доработки", "-ConfigPath", "cf", "-Object", "Catalog.Товары")
        self.assertEqual(rc, 0, out)
        self.assertIn("добавлен  Catalogs/Товары.xml", out)
        rc, out = self.gen("ext", "intercept", "-ExtensionPath", "ext/Доработки", "-ConfigPath", "cf",
                           "-ModulePath", "Catalog.Товары.ObjectModule", "-MethodName", "ПередЗаписью", "-InterceptorType", "Before")
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.p, "ext", "Доработки", "Catalogs", "Товары", "Ext", "ObjectModule.bsl"), encoding="utf-8-sig") as f:
            self.assertIn('&Перед("ПередЗаписью")', f.read())
        self.assertTrue(os.path.isdir(os.path.join(self.p, ".onec", "gen")))   # журнал — в папке проекта

    def test_read_command(self):
        rc, out = self.gen("object", "info", "-ObjectPath", "cf/Catalogs/Товары.xml")
        self.assertEqual(rc, 0, out)
        self.assertIn("Артикул", out)
        self.assertFalse(os.path.isdir(os.path.join(self.p, ".onec", "gen", "x")))


if __name__ == "__main__":
    unittest.main()
