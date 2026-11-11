"""Тесты индексатора на синтетической мини-выгрузке (без реальных данных).

Запуск: python3 -I -m unittest discover -s tests   (из корня репозитория)
Необязательно: ONEC_TEST_DUMP=<путь к выгрузке БСП> - дополнительная проверка на реальной выгрузке.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

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
