"""Тесты индексатора на синтетической мини-выгрузке (без реальных данных).

Запуск: python3 -I -m unittest discover -s tests   (из корня репозитория)
Необязательно: ONEC_TEST_DUMP=<путь к выгрузке БСП> - дополнительная проверка на реальной выгрузке.
"""
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "skills", "onec-configurator", "scripts"))
import onec_index as ix  # noqa: E402
import v8container  # noqa: E402

NS = ('xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:v8="http://v8.1c.ru/8.1/data/core" '
      'xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" xmlns:xs="http://www.w3.org/2001/XMLSchema" '
      'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config"')


def w(root, rel, text, bom=True):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write((b"\xef\xbb\xbf" if bom else b"") + text.replace("\n", "\r\n").encode("utf-8"))


def obj(kind, name, props="", child=""):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject %s version="2.20">\n'
            '<%s uuid="00000000-0000-0000-0000-000000000001"><Properties><Name>%s</Name>'
            '<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>%s</v8:content></v8:item></Synonym>%s</Properties>'
            '<ChildObjects>%s</ChildObjects></%s></MetaDataObject>') % (NS, kind, name, name, props, child, kind)


def attr(name, typ, extra=""):
    return ('<Attribute uuid="1"><Properties><Name>%s</Name><Type><v8:Type>%s</v8:Type>%s</Type></Properties></Attribute>'
            % (name, typ, extra))


def make_dump(root):
    w(root, "Configuration.xml", obj("Configuration", "Тест", "<CompatibilityMode>Version8_3_24</CompatibilityMode>"))
    w(root, "Catalogs/Товары.xml", obj("Catalog", "Товары", "", attr("Артикул", "xs:string",
      "<v8:StringQualifiers><v8:Length>25</v8:Length></v8:StringQualifiers>") + attr("Группа", "cfg:CatalogRef.Товары")
      + "<Form>ФормаЭлемента</Form>"))
    w(root, "Catalogs/Товары/Forms/ФормаЭлемента.xml", obj("Form", "ФормаЭлемента", "<FormType>Managed</FormType>"))
    w(root, "Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form.xml", '<Form><Attributes><Attribute><Type><v8:Type>cfg:CatalogRef.Контрагенты</v8:Type></Type></Attribute></Attributes></Form>')
    w(root, "Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form/Module.bsl",
      "&НаСервере\nПроцедура Заполнить()\n\tВыборка = Справочники.Контрагенты.Выбрать();\nКонецПроцедуры\n")
    w(root, "Catalogs/Контрагенты.xml", obj("Catalog", "Контрагенты"))
    w(root, "Documents/Заказ.xml", obj("Document", "Заказ", "<RegisterRecords><xr:Item xsi:type=\"xr:MDObjectRef\">AccumulationRegister.Продажи</xr:Item></RegisterRecords>",
      attr("Клиент", "cfg:CatalogRef.Контрагенты") + attr("Товар", "cfg:CatalogRef.Товары")))
    w(root, "Documents/Заказ/Ext/ObjectModule.bsl",
      "// Справочники.Товары в комментарии не считается\nПроцедура ОбработкаПроведения(Отказ)\n"
      "\tУтилиты.Округлить(1);\n\tЗапрос.Текст = \"ВЫБРАТЬ * ИЗ Справочник.Товары\";\n"
      "\tВаршавянка.Дата = 1;\nКонецПроцедуры\n")
    w(root, "AccumulationRegisters/Продажи.xml", obj("AccumulationRegister", "Продажи"))
    w(root, "CommonModules/Утилиты.xml", obj("CommonModule", "Утилиты", "<Server>true</Server>"))
    w(root, "CommonModules/Утилиты/Ext/Module.bsl",
      "Функция Округлить(Знач Число) Экспорт\n\tВозврат Число;\nКонецФункции\nПроцедура Скрытая()\nКонецПроцедуры\n")
    w(root, "Roles/Продавец.xml", obj("Role", "Продавец"))
    w(root, "Roles/Продавец/Ext/Rights.xml",
      '<Rights><object><name>Catalog.Товары</name><right><name>Read</name><value>true</value></right>'
      '<right><name>Update</name><value>false</value></right></object></Rights>')
    w(root, "Subsystems/Продажи.xml", obj("Subsystem", "Продажи",
      "<Content><xr:Item xsi:type=\"xr:MDObjectRef\">Catalog.Товары</xr:Item></Content>"))


class IndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = cls.tmp.name
        make_dump(cls.root)
        cls.db = os.path.join(cls.root, ".onec", "index.sqlite")
        ix.cmd_build(cls.root, cls.db)
        cls.conn = sqlite3.connect(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def refs(self, kind, name, how=None):
        q = "SELECT src_kind,src_name,src_where,how,member FROM refs WHERE dst_kind=? AND dst_lc=?"
        a = [kind, name.lower()]
        if how:
            q += " AND how=?"
            a.append(how)
        return self.conn.execute(q, a).fetchall()

    def test_objects_and_types(self):
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0], 7)
        t = dict((r[0], r[1]) for r in self.conn.execute("SELECT name,types FROM members WHERE mkind='Attribute'"))
        self.assertEqual(t["Артикул"], "String(25)")
        self.assertEqual(t["Группа"], "CatalogRef.Товары")

    def test_type_refs(self):
        src = {(r[0], r[1]) for r in self.refs("Catalog", "Товары", "Type")}
        self.assertEqual(src, {("Catalog", "Товары"), ("Document", "Заказ")})

    def test_code_refs_ignore_comments_and_non_objects(self):
        code = self.refs("Catalog", "Товары", "code")
        self.assertEqual(len(code), 1)           # запрос в ObjectModule, комментарий не в счёт
        self.assertEqual(code[0][1], "Заказ")
        cm = self.refs("CommonModule", "Утилиты", "code")
        self.assertEqual(cm[0][4], "Округлить")
        self.assertEqual(self.refs("CommonModule", "Варшавянка"), [])

    def test_form_xml_and_module_refs(self):
        kinds = {(r[2], r[3]) for r in self.refs("Catalog", "Контрагенты")}
        self.assertIn(("Form:ФормаЭлемента", "FormXml"), kinds)
        self.assertIn(("Form:ФормаЭлемента", "code"), kinds)

    def test_subsystem_role_register(self):
        self.assertEqual([r[1] for r in self.refs("Catalog", "Товары", "Content")], ["Продажи"])
        rights = self.refs("Catalog", "Товары", "rights")
        self.assertEqual(rights[0][1], "Продавец")
        self.assertEqual(rights[0][2], "Read")    # Update=false не попадает
        self.assertTrue(self.refs("AccumulationRegister", "Продажи", "RegisterRecords"))

    def test_procs(self):
        r = self.conn.execute("SELECT name,export,is_func FROM procs WHERE owner_name='Утилиты' ORDER BY line").fetchall()
        self.assertEqual(r, [("Округлить", 1, 1), ("Скрытая", 0, 0)])

    def test_incremental(self):
        with tempfile.TemporaryDirectory() as d:
            make_dump(d)
            db = os.path.join(d, ".onec", "index.sqlite")
            ix.cmd_build(d, db)
            p = os.path.join(d, "CommonModules", "Утилиты", "Ext", "Module.bsl")
            with open(p, "ab") as f:
                f.write("Процедура Новая() Экспорт\nКонецПроцедуры\n".encode("utf-8"))
            ix.cmd_build(d, db)
            c = sqlite3.connect(db)
            n = c.execute("SELECT COUNT(*) FROM procs WHERE owner_name='Утилиты'").fetchone()[0]
            self.assertEqual(n, 3)
            os.remove(os.path.join(d, "Catalogs", "Контрагенты.xml"))
            ix.cmd_build(d, db)
            n = c.execute("SELECT COUNT(*) FROM objects WHERE name='Контрагенты'").fetchone()[0]
            c.close()
            self.assertEqual(n, 0)

    def test_compact_storage(self):
        """Строки хранятся один раз в strs; частичная сборка = полной, без сирот в словаре."""
        q_refs = "SELECT unit,src_kind,src_name,src_where,how,dst_kind,dst_name,dst_lc,member,mem_lc,cnt,lines,file FROM refs"
        q_procs = "SELECT unit,owner_kind,owner_name,mod,name,params,line,file FROM procs"
        with tempfile.TemporaryDirectory() as d:
            make_dump(d)
            db, full = os.path.join(d, "inc.sqlite"), os.path.join(d, "full.sqlite")
            ix.cmd_build(d, db)
            p = os.path.join(d, "Documents", "Заказ", "Ext", "ObjectModule.bsl")
            with open(p, "ab") as f:
                f.write("Процедура Другая()\n\tУтилиты.Скрытая();\nКонецПроцедуры\n".encode("utf-8"))
            os.remove(os.path.join(d, "CommonModules", "Утилиты", "Ext", "Module.bsl"))
            ix.cmd_build(d, db)
            ix.cmd_build(d, full, full=True)
            a, b = sqlite3.connect(db), sqlite3.connect(full)
            try:
                for q in (q_refs, q_procs):
                    self.assertEqual(sorted(a.execute(q).fetchall()), sorted(b.execute(q).fetchall()))
                orphans = a.execute(
                    "SELECT COUNT(*) FROM strs WHERE id NOT IN (SELECT uid FROM refs_t UNION SELECT src_nid FROM refs_t "
                    "UNION SELECT where_id FROM refs_t UNION SELECT dst_nid FROM refs_t UNION SELECT mem_nid FROM refs_t "
                    "UNION SELECT fid FROM refs_t UNION SELECT uid FROM procs_t UNION SELECT owner_nid FROM procs_t "
                    "UNION SELECT fid FROM procs_t)").fetchone()[0]
                self.assertEqual(orphans, 0)
                self.assertTrue(a.execute("SELECT 1 FROM refs WHERE dst_lc='утилиты' AND mem_lc='скрытая'").fetchone())
            finally:
                a.close()
                b.close()


class IncrementalConsistencyTest(unittest.TestCase):
    """Частичная сборка должна давать то же, что полная (замечания внешнего ревью)."""

    Q_REFS = "SELECT unit,src_kind,src_name,src_where,how,dst_kind,dst_name,member,cnt,lines,file,src_source FROM refs"
    Q_MODS = "SELECT unit,owner_kind,owner_name,mod,file,lines,procs,exports FROM modules"
    Q_OBJS = "SELECT kind,name,source FROM objects"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, "cf")
        self.db = os.path.join(self.tmp.name, "inc.sqlite")
        w(self.d, "Configuration.xml", obj("Configuration", "Т"))
        w(self.d, "Catalogs/Товары.xml", obj("Catalog", "Товары"))
        w(self.d, "Catalogs/Товары/Ext/ObjectModule.bsl",
          "Процедура П()\n\tНовыйМодуль.Сделать();\n\tА = Справочники.Склады.ПустаяСсылка();\nКонецПроцедуры\n")
        self.build()

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, **kw):
        with redirect_stdout(io.StringIO()):
            ix.cmd_build(self.d, self.db, **kw)

    def assert_same_as_full(self):
        full = os.path.join(self.tmp.name, "full-%d.sqlite" % len(os.listdir(self.tmp.name)))
        with redirect_stdout(io.StringIO()):
            ix.cmd_build(self.d, full, full=True)
        a, b = sqlite3.connect(self.db), sqlite3.connect(full)
        try:
            for q in (self.Q_REFS, self.Q_MODS, self.Q_OBJS):
                self.assertEqual(sorted(a.execute(q).fetchall()), sorted(b.execute(q).fetchall()), q)
        finally:
            a.close()
            b.close()

    def refs_to(self, name):
        c = sqlite3.connect(self.db)
        try:
            return c.execute("SELECT src_name,member FROM refs WHERE dst_name=? AND how='code'", (name,)).fetchall()
        finally:
            c.close()

    def test_added_common_module_picks_up_old_callers(self):
        self.assertEqual(self.refs_to("НовыйМодуль"), [])
        w(self.d, "CommonModules/НовыйМодуль.xml", obj("CommonModule", "НовыйМодуль"))
        w(self.d, "CommonModules/НовыйМодуль/Ext/Module.bsl", "Процедура Сделать() Экспорт\nКонецПроцедуры\n")
        self.build()
        self.assertEqual(self.refs_to("НовыйМодуль"), [("Товары", "Сделать")])
        self.assert_same_as_full()

    def test_added_and_removed_catalog(self):
        w(self.d, "Catalogs/Склады.xml", obj("Catalog", "Склады"))
        self.build()
        self.assertEqual(self.refs_to("Склады"), [("Товары", "ПустаяСсылка")])
        self.assert_same_as_full()
        os.remove(os.path.join(self.d, "Catalogs", "Склады.xml"))
        self.build()
        self.assertEqual(self.refs_to("Склады"), [])
        self.assert_same_as_full()

    def test_renamed_module_file_detected(self):
        ext = os.path.join(self.d, "Catalogs", "Товары", "Ext")
        os.rename(os.path.join(ext, "ObjectModule.bsl"), os.path.join(ext, "ManagerModule.bsl"))   # размер и дата те же
        self.build()
        c = sqlite3.connect(self.db)
        try:
            self.assertEqual(c.execute("SELECT mod FROM modules WHERE owner_name='Товары'").fetchall(), [("ManagerModule",)])
        finally:
            c.close()
        self.assert_same_as_full()


class SameNamedExternalsTest(unittest.TestCase):
    def test_two_with_dirs_keep_both_reports(self):
        with tempfile.TemporaryDirectory() as d:
            w(os.path.join(d, "cf"), "Configuration.xml", obj("Configuration", "Т"))
            for folder, name in (("a/erf", "ОтчетА"), ("b/erf", "ОтчетБ")):
                w(os.path.join(d, folder), "Отчет.xml", obj("ExternalReport", name))
            db = os.path.join(d, "i.sqlite")
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                ix.cmd_build(os.path.join(d, "cf"), db, extras=[os.path.join(d, "a", "erf"), os.path.join(d, "b", "erf")])
            c = sqlite3.connect(db)
            try:
                rows = sorted(c.execute("SELECT name,source FROM objects WHERE kind='ExternalReport'").fetchall())
            finally:
                c.close()
            self.assertEqual(rows, [("ОтчетА", "erf:erf/Отчет"), ("ОтчетБ", "erf:erf~2/Отчет")])

    def test_duplicate_extension_warned(self):
        with tempfile.TemporaryDirectory() as d:
            w(os.path.join(d, "cf"), "Configuration.xml", obj("Configuration", "Т"))
            ext = obj("Configuration", "Лок", "<ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose>")
            w(os.path.join(d, "x", "Лок"), "Configuration.xml", ext)
            w(os.path.join(d, "y", "Лок"), "Configuration.xml", ext)
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                ix.cmd_build(os.path.join(d, "cf"), os.path.join(d, "i.sqlite"), extras=[os.path.join(d, "x"), os.path.join(d, "y")])
            self.assertIn("найдено дважды", err.getvalue())


class ContainerTest(unittest.TestCase):
    def test_roundtrip(self):
        ents = [("form", b"\0" * 8, b"\0" * 8, "﻿{26,\r\n{16}}".encode("utf-8")),
                ("module", b"\0" * 8, b"\0" * 8, b"x" * 3000)]
        back = v8container.parse(v8container.build(ents))
        self.assertEqual([(a[0], a[3]) for a in back], [(a[0], a[3]) for a in ents])


@unittest.skipUnless(os.environ.get("ONEC_TEST_DUMP"), "ONEC_TEST_DUMP не задан")
class RealDumpTest(unittest.TestCase):
    def test_build_and_stats(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ix.cmd_build(os.environ["ONEC_TEST_DUMP"], os.path.join(d, "i.sqlite")), 0)


if __name__ == "__main__":
    unittest.main()
