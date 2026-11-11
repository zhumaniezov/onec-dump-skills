"""Свойства безопасности onec_gen.py для КАЖДОЙ команды, которая меняет файлы.

На богатой выгрузке (объекты с формами, макетами, модулями, подсистема, роль, пакет XDTO,
расширение, внешние обработка и отчёт) для каждой команды проверяется:
  1. --dry-run не меняет ни одного файла проекта (сравнение хешей всего дерева);
  2. настоящий запуск: каждый изменённый, добавленный и удалённый файл есть в журнале,
     у каждого изменённого и удалённого есть исходная копия, вне корня команды ничего не менялось;
  3. undo возвращает дерево байт в байт (файлы и каталоги);
  4. после чужой правки undo отказывается и не трогает ни одного файла.
Плюс: движок, пишущий в обход охраны, — запуск помечается как неотменяемый, undo отказывается;
запись вне корня команды запрещена.
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(os.path.dirname(HERE), "skills", "onec-configurator")
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import onec_gen as og  # noqa: E402

LXML = og.has_lxml()

XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="http://example.org/bank"
           xmlns="http://example.org/bank" elementFormDefault="qualified">
  <xs:complexType name="Платёж">
    <xs:sequence>
      <xs:element name="Сумма" type="xs:decimal"/>
      <xs:element name="Назначение" type="xs:string" minOccurs="0"/>
    </xs:sequence>
  </xs:complexType>
</xs:schema>
"""


def run(*argv, cwd):
    o = io.StringIO()
    old = os.getcwd()
    try:
        os.chdir(cwd)
        with redirect_stdout(o), redirect_stderr(o):
            rc = og.main(list(argv))
    finally:
        os.chdir(old)
    return rc, o.getvalue()


def state(root):
    """Хеши всех файлов и список каталогов проекта (без журналов .onec)."""
    files, dirs = {}, set()
    for dp, dn, fns in os.walk(root):
        dn[:] = [d for d in dn if d != ".onec"]
        for d in dn:
            dirs.add(os.path.relpath(os.path.join(dp, d), root).replace(os.sep, "/"))
        for fn in fns:
            p = os.path.join(dp, fn)
            with open(p, "rb") as f:
                files[os.path.relpath(p, root).replace(os.sep, "/")] = hashlib.sha256(f.read()).hexdigest()
    return files, dirs


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# (название, аргументы команды). Пути — относительно папки проекта; файлы описаний лежат в in/.
CASES = [
    ("object new", ["object", "new", "-JsonPath", "in/reg.json", "-OutputDir", "cf"]),
    ("object edit", ["object", "edit", "-ObjectPath", "cf/Catalogs/Товары.xml", "-Operation", "add-attribute",
                     "-Value", "Вес: Число(10,3)"]),
    ("object remove", ["object", "remove", "-ConfigDir", "cf", "-Object", "Catalog.Склады"]),
    ("object remove report", ["object", "remove", "-ConfigDir", "cf", "-Object", "Report.Продажи"]),
    ("object draft", ["object", "draft", "-ObjectPath", "cf/Catalogs/Товары.xml", "-OutputPath", "out/товары.json"]),
    ("config edit", ["config", "edit", "-ConfigPath", "cf", "-Operation", "modify-property", "-Value", "Version=1.0.0.7"]),
    ("config new", ["config", "new", "-Name", "Вторая", "-OutputDir", "cf2", "-FormatVersion", "2.20",
                    "-CompatibilityMode", "Version8_3_24"]),
    ("ext new", ["ext", "new", "-Name", "Второе", "-OutputDir", "ext/Второе", "-ConfigPath", "cf"]),
    ("ext borrow", ["ext", "borrow", "-ExtensionPath", "ext/Доработки", "-ConfigPath", "cf", "-Object", "Catalog.Товары"]),
    ("ext intercept", ["ext", "intercept", "-ExtensionPath", "ext/Доработки", "-ConfigPath", "cf",
                       "-ModulePath", "Catalog.Товары.ObjectModule", "-MethodName", "ПередЗаписью",
                       "-InterceptorType", "Before"]),
    ("epf new", ["epf", "new", "-Name", "Загрузка", "-SrcDir", "epf"]),
    ("erf new", ["erf", "new", "-Name", "Остатки", "-SrcDir", "erf", "-WithSKD"]),
    ("form new", ["form", "new", "-ObjectPath", "cf/Documents/Заказ.xml", "-FormName", "ФормаДокумента",
                  "-Purpose", "Object"]),
    ("form build", ["form", "build", "-JsonPath", "in/form.json",
                    "-OutputPath", "cf/Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form.xml"]),
    ("form edit", ["form", "edit", "-FormPath", "cf/Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form.xml",
                   "-JsonPath", "in/form-edit.json"]),
    ("form remove", ["form", "remove", "-ObjectName", "Товары", "-FormName", "ФормаЭлемента",
                     "-SrcDir", "cf/Catalogs"]),
    ("form draft", ["form", "draft", "-FormPath", "cf/Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form.xml",
                    "-OutputPath", "out/форма.json"]),
    ("dcs build", ["dcs", "build", "-DefinitionFile", "in/dcs2.json",
                   "-OutputPath", "cf/Reports/Продажи/Templates/Схема/Ext/Template.xml"]),
    ("dcs edit", ["dcs", "edit", "-TemplatePath", "cf/Reports/Продажи/Templates/Схема/Ext/Template.xml",
                  "-Operation", "add-field", "-Value", "Сумма: decimal(15,2)"]),
    ("dcs draft", ["dcs", "draft", "-TemplatePath", "cf/Reports/Продажи/Templates/Схема/Ext/Template.xml",
                   "-OutputPath", "out/скд.json"]),
    ("role new", ["role", "new", "-JsonPath", "in/role.json", "-OutputDir", "cf"]),
    ("role edit", ["role", "edit", "-RolePath", "cf/Roles/Менеджер", "-Operation", "add-rights",
                   "-Value", "Report.Продажи: @view"]),
    ("sheet build", ["sheet", "build", "-JsonPath", "in/sheet2.json",
                     "-OutputPath", "cf/Catalogs/Товары/Templates/Печать/Ext/Template.xml"]),
    ("sheet draft", ["sheet", "draft", "-TemplatePath", "cf/Catalogs/Товары/Templates/Печать/Ext/Template.xml",
                     "-OutputPath", "out/макет.json"]),
    ("template add", ["template", "add", "-ObjectName", "Заказ", "-TemplateName", "Бланк",
                      "-TemplateType", "SpreadsheetDocument", "-SrcDir", "cf/Documents"]),
    ("template remove", ["template", "remove", "-ObjectName", "Продажи", "-TemplateName", "Схема",
                         "-SrcDir", "cf/Reports"]),
    ("subsystem new", ["subsystem", "new", "-Value", '{"name": "Склад", "content": ["Catalog.Товары"]}',
                       "-OutputDir", "cf"]),
    ("subsystem edit", ["subsystem", "edit", "-SubsystemPath", "cf/Subsystems/Продажи.xml",
                        "-Operation", "add-content", "-Value", "Report.Продажи"]),
    ("cmdui edit", ["cmdui", "edit", "-CIPath", "cf/Subsystems/Продажи/Ext/CommandInterface.xml",
                    "-Operation", "hide", "-Value", "Catalog.Товары.StandardCommand.OpenList", "-CreateIfMissing"]),
    ("xdto new", ["xdto", "new", "-XsdPath", "in/bank.xsd", "-Name", "Казначейство", "-OutputDir", "cf"]),
    ("xdto edit", ["xdto", "edit", "-PackagePath", "cf/XDTOPackages/Банк", "-Operation", "add-property",
                   "-Target", "Платёж", "-Value", '<xs:element name="Комментарий" type="xs:string" minOccurs="0"/>']),
    ("xdto edit rename", ["xdto", "edit", "-PackagePath", "cf/XDTOPackages/Банк", "-Operation", "rename",
                          "-Value", "ОбменСБанком"]),
    ("xdto draft", ["xdto", "draft", "-PackagePath", "cf/XDTOPackages/Банк", "-OutFile", "out/банк.xsd"]),
    ("help add", ["help", "add", "-ObjectName", "Заказ", "-SrcDir", "cf/Documents"]),
    ("support set", ["support", "set", "-Path", "cf/Catalogs/Валюты.xml", "-Set", "editable"]),
]


@unittest.skipUnless(LXML, "нет lxml")
class GenSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = os.path.join(cls.tmp.name, "base")
        os.makedirs(cls.base)
        cls._build(cls.base)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def _build(cls, p):
        def j(name, data):
            return write(os.path.join(p, "in", name), json.dumps(data, ensure_ascii=False))

        j("cat.json", {"type": "Catalog", "name": "Товары", "attributes": ["Артикул: Строка(25)"],
                       "tabularSections": {"Цены": ["Цена: Число(15,2)"]}})
        j("doc.json", {"type": "Document", "name": "Заказ", "attributes": ["Товар: CatalogRef.Товары"]})
        j("rep.json", {"type": "Report", "name": "Продажи"})
        j("cur.json", {"type": "Catalog", "name": "Валюты"})
        j("wh.json", {"type": "Catalog", "name": "Склады", "attributes": ["Адрес: Строка(100)"]})
        j("reg.json", {"type": "InformationRegister", "name": "Цены", "dimensions": ["Товар: CatalogRef.Товары"],
                       "resources": ["Цена: Число(15,2)"]})
        j("role.json", {"name": "Кладовщик", "objects": ["Catalog.Товары: @view"]})
        j("role0.json", {"name": "Менеджер", "objects": ["Catalog.Товары: @view", "Document.Заказ: @edit"]})
        j("form.json", {"title": "Товар", "elements": [{"input": "Наименование", "path": "Объект.Description"},
                                                        {"input": "Артикул", "path": "Объект.Артикул"}]})
        j("form-edit.json", {"elements": [{"check": "Срочно", "path": "Срочно"}],
                             "attributes": [{"name": "Срочно", "type": "boolean"}]})
        j("dcs.json", {"dataSets": [{"name": "Продажи", "query": "ВЫБРАТЬ 1 КАК Количество",
                                     "fields": ["Количество: decimal(15,3)"]}]})
        j("sheet.json", {"columns": 3, "areas": [{"name": "Шапка", "rows": [{"cells": [{"col": 1, "text": "Товар"}]}]}]})
        j("dcs2.json", {"dataSets": [{"name": "Остатки", "query": "ВЫБРАТЬ 2 КАК Остаток",
                                      "fields": ["Остаток: decimal(15,3)"]}]})
        j("sheet2.json", {"columns": 2, "areas": [{"name": "Подвал", "rows": [{"cells": [{"col": 1, "text": "Итого"}]}]}]})
        write(os.path.join(p, "in", "bank.xsd"), XSD)
        steps = [
            ["config", "new", "-Name", "Тест", "-OutputDir", "cf", "-FormatVersion", "2.20",
             "-CompatibilityMode", "Version8_3_24"],
            ["object", "new", "-JsonPath", "in/cat.json", "-OutputDir", "cf"],
            ["object", "new", "-JsonPath", "in/doc.json", "-OutputDir", "cf"],
            ["object", "new", "-JsonPath", "in/rep.json", "-OutputDir", "cf"],
            ["object", "new", "-JsonPath", "in/wh.json", "-OutputDir", "cf"],
            ["object", "new", "-JsonPath", "in/cur.json", "-OutputDir", "cf"],
            ["form", "new", "-ObjectPath", "cf/Catalogs/Склады.xml", "-FormName", "ФормаСписка", "-Purpose", "List"],
            ["subsystem", "new", "-Value",
             '{"name": "Продажи", "content": ["Catalog.Товары", "Document.Заказ", "Catalog.Склады"]}',
             "-OutputDir", "cf"],
            ["form", "new", "-ObjectPath", "cf/Catalogs/Товары.xml", "-FormName", "ФормаЭлемента", "-Purpose", "Object"],
            ["template", "add", "-ObjectName", "Продажи", "-TemplateName", "Схема", "-TemplateType",
             "DataCompositionSchema", "-SrcDir", "cf/Reports"],
            ["template", "add", "-ObjectName", "Товары", "-TemplateName", "Печать", "-TemplateType",
             "SpreadsheetDocument", "-SrcDir", "cf/Catalogs"],
            ["dcs", "build", "-DefinitionFile", "in/dcs.json",
             "-OutputPath", "cf/Reports/Продажи/Templates/Схема/Ext/Template.xml"],
            ["sheet", "build", "-JsonPath", "in/sheet.json",
             "-OutputPath", "cf/Catalogs/Товары/Templates/Печать/Ext/Template.xml"],
            ["role", "new", "-JsonPath", "in/role0.json", "-OutputDir", "cf"],
            ["xdto", "new", "-XsdPath", "in/bank.xsd", "-Name", "Банк", "-OutputDir", "cf"],
            ["ext", "new", "-Name", "Доработки", "-OutputDir", "ext/Доработки", "-ConfigPath", "cf"],
        ]
        for argv in steps:
            rc, out = run(*argv, cwd=p)
            if rc:
                raise AssertionError("подготовка: %s\n%s" % (" ".join(argv[:2]), out[-1500:]))
        write(os.path.join(p, "cf", "Catalogs", "Товары", "Ext", "ObjectModule.bsl"),
              "﻿Процедура ПередЗаписью(Отказ)\r\nКонецПроцедуры\r\n")
        # конфигурация «на поддержке»: справочник Валюты на замке (запись вида 0,0,<uuid>)
        import re
        with open(os.path.join(p, "cf", "Catalogs", "Валюты.xml"), encoding="utf-8-sig") as f:
            cat_uuid = re.search(r'<Catalog uuid="([0-9a-f-]{36})"', f.read()).group(1)
        with open(os.path.join(p, "cf", "Ext", "ParentConfigurations.bin"), "wb") as f:
            f.write(b"\xef\xbb\xbf" + ("{6,0,1,\n{0,0,%s},\n{0,0,%s}\n}" % (cat_uuid, cat_uuid)).encode("utf-8"))
        write(os.path.join(p, "cf", "ConfigDumpInfo.xml"), '<?xml version="1.0" encoding="UTF-8"?>\n<ConfigDumpInfo/>\n')
        shutil.rmtree(os.path.join(p, ".onec"), ignore_errors=True)

    def setUp(self):
        self.case_dir = tempfile.mkdtemp(dir=self.tmp.name)
        self.p = os.path.join(self.case_dir, "proj")
        shutil.copytree(self.base, self.p)

    def tearDown(self):
        shutil.rmtree(self.case_dir, ignore_errors=True)

    def journal(self):
        base = os.path.join(self.p, ".onec", "gen")
        self.assertTrue(os.path.isdir(base), "нет журнала запуска")
        runs = sorted(d for d in os.listdir(base) if os.path.isfile(os.path.join(base, d, "journal.json")))
        self.assertEqual(len(runs), 1, runs)
        jdir = os.path.join(base, runs[0])
        with open(os.path.join(jdir, "journal.json"), encoding="utf-8") as f:
            return jdir, json.load(f)

    def check_case(self, name, argv):
        p = self.p
        files0, dirs0 = state(p)
        # 1. пробный запуск ничего не меняет
        rc, out = run(*(argv + ["--dry-run"]), cwd=p)
        self.assertEqual(rc, 0, out)
        self.assertIn("исходники не изменены", out)
        self.assertEqual(state(p), (files0, dirs0), "dry-run изменил файлы")
        self.assertFalse(os.path.exists(os.path.join(p, ".onec", "gen")) and
                         os.listdir(os.path.join(p, ".onec", "gen")), "dry-run оставил журнал")
        # 2. настоящий запуск: все изменения в журнале, исходные копии есть
        rc, out = run(*argv, cwd=p)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("ОШИБКА", out)
        files1, _dirs1 = state(p)
        jdir, j = self.journal()
        self.assertEqual(j["unsaved"], [])
        root = os.path.relpath(j["root"], p).replace(os.sep, "/")
        prefix = "" if root == "." else root + "/"
        changed = {r for r in set(files0) | set(files1) if files0.get(r) != files1.get(r)}
        self.assertTrue(changed, "команда ничего не изменила")
        listed = {prefix + r for r in list(j["modified"]) + list(j["added"]) + list(j["removed"])}
        self.assertEqual(changed, listed)
        for r, h in j["modified"].items():
            self.assertEqual(h["before"], files0[prefix + r])
            self.assertTrue(os.path.isfile(os.path.join(jdir, "backup", *r.split("/"))), r)
        for r, h in j["removed"].items():
            self.assertEqual(h, files0[prefix + r])
        self.assertEqual(files1.get("cf/ConfigDumpInfo.xml"), files0["cf/ConfigDumpInfo.xml"])
        # 4. чужая правка после запуска: undo отказывается и ничего не трогает
        if j["modified"] or j["added"]:
            victim = next(iter(j["modified"]), None) or next(iter(j["added"]))
            vp = os.path.join(j["root"], *victim.split("/"))
            with open(vp, "rb") as f:
                saved = f.read()
            with open(vp, "ab") as f:
                f.write("<!-- чужая правка -->".encode("utf-8"))
            snap = state(p)
            rc, out = run("undo", cwd=p)
            self.assertEqual(rc, 2, out)
            self.assertIn("файлы не тронуты", out)
            self.assertEqual(state(p), snap, "отказавшая отмена изменила файлы")
            with open(vp, "wb") as f:
                f.write(saved)
        if j["removed"]:
            victim = next(iter(j["removed"]))
            vp = os.path.join(j["root"], *victim.split("/"))
            write(vp, "НОВАЯ ПРАВКА")
            snap = state(p)
            rc, out = run("undo", cwd=p)
            self.assertEqual(rc, 2, out)
            self.assertIn("создан заново", out)
            self.assertEqual(state(p), snap)
            os.remove(vp)
        # 3. отмена — байт в байт
        rc, out = run("undo", cwd=p)
        self.assertEqual(rc, 0, out)
        files2, dirs2 = state(p)
        self.assertEqual(files2, files0)
        self.assertEqual(dirs2, dirs0)

    def test_every_mutating_command(self):
        kinds = {k for k, v in og.COMMANDS.items() if v[1] != "read"}
        covered = {" ".join(argv[:2]) for _n, argv in CASES}
        self.assertEqual(kinds - covered, set(), "команды без проверки безопасности")
        for name, argv in CASES:
            with self.subTest(name):
                self.setUp()
                try:
                    self.check_case(name, list(argv))
                finally:
                    self.tearDown()

    def test_large_dump_does_not_matter(self):
        """Охрана не зависит от размера выгрузки: удаление объекта в «большой» выгрузке отменяется."""
        write(os.path.join(self.p, "cf", "CommonModules", "Большой", "Ext", "Module.bsl"), "﻿" + "// x\r\n" * 200000)
        self.check_case("object remove", ["object", "remove", "-ConfigDir", "cf", "-Object", "Catalog.Склады"])

    def test_write_outside_root_is_denied(self):
        """Движок не может писать вне корня команды: и в пробном, и в настоящем запуске."""
        fake = write(os.path.join(self.case_dir, "fake.py"), textwrap.dedent("""
            import sys
            with open(sys.argv[-1], "w", encoding="utf-8") as f:
                f.write("взлом")
        """))
        victim = os.path.join(self.p, "in", "cat.json")
        files0 = state(self.p)
        with mock.patch.object(og, "engine_script", return_value=fake):
            for extra in (["--dry-run"], []):
                rc, out = run("object", "edit", "-ObjectPath", "cf/Catalogs/Товары.xml", *extra, victim, cwd=self.p)
                self.assertNotEqual(rc, 0, out)
                self.assertIn("PermissionError", out)
            rc, out = run("object", "info", "-ObjectPath", "cf/Catalogs/Товары.xml", victim, cwd=self.p)
            self.assertNotEqual(rc, 0, out)
        self.assertEqual(state(self.p), files0)

    def test_unguarded_write_is_unrecoverable_and_undo_refuses(self):
        """Запись в обход охраны (низкоуровневый _io.FileIO) обнаруживается: ошибка, undo отказывается."""
        fake = write(os.path.join(self.case_dir, "fake.py"), textwrap.dedent("""
            import _io, sys
            f = _io.FileIO(sys.argv[-1], "w")
            f.write("в обход".encode("utf-8"))
            f.close()
        """))
        target = os.path.join(self.p, "cf", "Catalogs", "Товары.xml")
        with mock.patch.object(og, "engine_script", return_value=fake):
            rc, out = run("object", "edit", "-ObjectPath", "cf/Catalogs/Товары.xml", target, cwd=self.p)
        self.assertEqual(rc, 3, out)
        self.assertIn("отмена этого запуска невозможна", out)
        snap = state(self.p)
        rc, out = run("undo", cwd=self.p)
        self.assertEqual(rc, 2, out)
        self.assertIn("без резервной копии", out)
        self.assertEqual(state(self.p), snap)

    def test_undo_refuses_with_damaged_backup(self):
        """Нет или испорчена исходная копия — undo отказывается до первой записи."""
        rc, out = run("object", "remove", "-ConfigDir", "cf", "-Object", "Catalog.Склады", cwd=self.p)
        self.assertEqual(rc, 0, out)
        jdir, j = self.journal()
        removed = sorted(j["removed"])
        self.assertIn("Catalogs/Склады.xml", removed)
        bak = os.path.join(jdir, "backup", "Catalogs", "Склады.xml")
        with open(bak, "ab") as f:                                  # испорчена
            f.write(b"x")
        snap = state(self.p)
        rc, out = run("undo", cwd=self.p)
        self.assertEqual(rc, 2, out)
        self.assertIn("повреждена резервная копия: Catalogs/Склады.xml", out)
        self.assertEqual(state(self.p), snap)
        os.remove(bak)                                              # пропала
        rc, out = run("undo", cwd=self.p)
        self.assertEqual(rc, 2, out)
        self.assertEqual(state(self.p), snap)

    def test_config_dump_info_restored(self):
        fake = write(os.path.join(self.case_dir, "fake.py"), textwrap.dedent("""
            import os, sys
            root = os.path.dirname(os.path.dirname(sys.argv[-1]))
            with open(os.path.join(root, "ConfigDumpInfo.xml"), "a", encoding="utf-8") as f:
                f.write("<!-- изменено -->")
            with open(sys.argv[-1], "a", encoding="utf-8") as f:
                f.write("<!-- правка -->")
        """))
        files0, _ = state(self.p)
        target = os.path.join(self.p, "cf", "Catalogs", "Товары.xml")
        with mock.patch.object(og, "engine_script", return_value=fake):
            rc, out = run("object", "edit", "-ObjectPath", "cf/Catalogs/Товары.xml", target, cwd=self.p)
        self.assertEqual(rc, 0, out)
        self.assertIn("ConfigDumpInfo.xml восстановлен", out)
        files1, _ = state(self.p)
        self.assertEqual(files1["cf/ConfigDumpInfo.xml"], files0["cf/ConfigDumpInfo.xml"])
        self.assertNotEqual(files1["cf/Catalogs/Товары.xml"], files0["cf/Catalogs/Товары.xml"])

    def test_engine_uses_only_guarded_write_apis(self):
        """В коде движка нет способов записи, которые охрана не перехватывает."""
        import re
        bad_rx = re.compile(r"\bFileIO\b|\b_io\.|os\.write\(|os\.truncate|os\.link\(|os\.symlink|write_text\(|"
                            r"write_bytes\(|codecs\.open|\bctypes\b|dir_fd|os\.system\(|os\.popen\(|os\.spawn|os\.exec")
        write_rx = re.compile(r"\b([A-Za-z_][\w.]*)\.write\(")
        bad = []
        for dp, _dn, fns in os.walk(og.ENGINE_DIR):
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dp, fn)
                with open(path, encoding="utf-8") as f:
                    for n, line in enumerate(f, 1):
                        code = line.split("#", 1)[0]
                        if bad_rx.search(code):
                            bad.append("%s:%d: %s" % (fn, n, line.strip()))
                        for m in write_rx.finditer(code):
                            if m.group(1) not in ("f", "fh", "fo", "sys.stdout", "sys.stderr"):
                                bad.append("%s:%d: %s" % (fn, n, line.strip()))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
