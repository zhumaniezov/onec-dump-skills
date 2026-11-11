"""Расширения и внешние обработки/отчёты в индексе и проверке запросов (синтетические данные)."""
import io
import os
import sqlite3
from unittest import mock
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "skills", "onec-configurator", "scripts"))
sys.path.insert(0, HERE)
import onec_index as ix  # noqa: E402
import onec_query as oq  # noqa: E402
from test_index import NS, attr, w  # noqa: E402


def md(kind, name, props="", child="", version="2.20"):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject %s version="%s">\n<%s uuid="1"><Properties>%s<Name>%s</Name>'
            '</Properties><ChildObjects>%s</ChildObjects></%s></MetaDataObject>') % (NS, version, kind, props, name, child, kind)


ADOPTED = "<ObjectBelonging>Adopted</ObjectBelonging>"


def make(base):
    main = os.path.join(base, "alfa")
    ext = os.path.join(base, "alfa_extensions", "Локализация")
    rep = os.path.join(base, "alfa_externalreports")
    w(main, "Configuration.xml", md("Configuration", "Альфа"))
    w(main, "Catalogs/Товары.xml", md("Catalog", "Товары", "", attr("Артикул", "xs:string")))
    w(main, "Catalogs/Товары/Ext/ObjectModule.bsl", "Процедура ПередЗаписью(Отказ)\nКонецПроцедуры\n")
    w(main, "CommonModules/Модуль.xml", md("CommonModule", "Модуль"))
    w(main, "CommonModules/Модуль/Ext/Module.bsl",
      'Процедура П() Экспорт\n\tЗапрос = Новый Запрос("ВЫБРАТЬ Т.ext_Поле ИЗ Справочник.Товары КАК Т");\nКонецПроцедуры\n')
    w(ext, "Configuration.xml", md("Configuration", "Локализация",
      ADOPTED.replace("<ObjectBelonging>Adopted</ObjectBelonging>", "") +
      "<ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose><NamePrefix>ext_</NamePrefix>"))
    w(ext, "Catalogs/Товары.xml", md("Catalog", "Товары", ADOPTED,
      '<Attribute uuid="2"><Properties>' + ADOPTED + '<Name>Артикул</Name><Type><v8:Type>xs:string</v8:Type></Type></Properties></Attribute>'
      + attr("ext_Поле", "xs:string")))
    w(ext, "Catalogs/Товары/Ext/ObjectModule.bsl",
      '&После("ПередЗаписью")\nПроцедура ext_ПередЗаписью(Отказ)\n\text_Общий.Проверить();\nКонецПроцедуры\n')
    w(ext, "CommonModules/ext_Общий.xml", md("CommonModule", "ext_Общий"))
    w(ext, "CommonModules/ext_Общий/Ext/Module.bsl",
      "Процедура Проверить() Экспорт\n\tВ = Справочники.Товары.Выбрать();\nКонецПроцедуры\n")
    w(rep, "Отчет1.xml", md("ExternalReport", "ПродажиПоТоварам"))
    w(rep, "Отчет1/Ext/ObjectModule.bsl", "Процедура Х()\n\tМодуль.П();\nКонецПроцедуры\n")
    w(rep, "Отчет1/Templates/Схема/Ext/Template.xml",
      '<?xml version="1.0"?>\n<DataCompositionSchema xmlns="http://v8.1c.ru/8.1/data-composition-system/schema">\n'
      '<dataSet><name>Н</name><query>ВЫБРАТЬ Т.ext_Поле, Т.Нет ИЗ Справочник.Товары КАК Т</query></dataSet></DataCompositionSchema>')
    w(rep, "Отчет1/Templates/Схема.xml", md("Template", "Схема"))
    return main, os.path.dirname(ext), rep


def run(fn, *argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(list(argv))
    return buf.getvalue()


class ExtensionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.main, cls.ext, cls.rep = make(cls.tmp.name)
        cls.db = os.path.join(cls.main, ".onec", "index.sqlite")
        cls.A = ["--root", cls.main, "--db", cls.db]
        run(ix.main, "build", *cls.A, "--with", cls.ext, "--with", cls.rep)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_sources_and_relative_paths(self):
        o = run(ix.main, "stats", *self.A)
        self.assertIn("расширение Локализация: объектов 2, заимствовано 1", o)
        self.assertIn("ПродажиПоТоварам — внешний отчёт, файл alfa_externalreports/Отчет1.xml", o)
        c = sqlite3.connect(self.db)
        f = c.execute("SELECT file FROM objects WHERE name='ext_Общий'").fetchone()[0]
        c.close()
        self.assertEqual(f, "../alfa_extensions/Локализация/CommonModules/ext_Общий.xml")

    def test_info_main_object_with_extension_part(self):
        o = run(ix.main, "info", "Справочник.Товары", *self.A)
        self.assertTrue(o.startswith("Catalog.Товары  «»  (Catalogs/Товары.xml)\n"))      # основная конфигурация
        self.assertIn("Расширение Локализация: реквизиты и прочее: ext_Поле: String", o)
        self.assertIn("перехваты: ObjectModule &После(«ПередЗаписью»)", o)
        self.assertNotIn("Артикул: String  [adopted]", o.split("Расширение")[1])

    def test_refs_with_source_labels(self):
        o = run(ix.main, "refs", "Справочник.Товары", *self.A)
        self.assertIn("ext_Общий  [расширение Локализация] — код: Module x1", o)
        self.assertIn("ПродажиПоТоварам  [внешний отчёт, файл alfa_externalreports/Отчет1.xml] — запросы", o)
        self.assertIn("Товары  [расширение Локализация] — перехват: ObjectModule &После(«ПередЗаписью»)", o)
        o = run(ix.main, "refs", "ОбщийМодуль.Модуль", "--member", "П", *self.A)
        self.assertIn("[внешний отчёт, файл alfa_externalreports/Отчет1.xml]", o)

    def test_extras_remembered_and_removable(self):
        run(ix.main, "build", *self.A)                               # без --with: подключения сохраняются
        c = sqlite3.connect(self.db)
        n = c.execute("SELECT COUNT(*) FROM objects WHERE source<>'' AND kind<>'Configuration'").fetchone()[0]
        c.close()
        self.assertEqual(n, 3)
        with tempfile.TemporaryDirectory() as d:
            db2 = os.path.join(d, "i.sqlite")
            run(ix.main, "build", "--root", self.main, "--db", db2, "--with", self.ext)
            run(ix.main, "build", "--root", self.main, "--db", db2, "--no-extras")
            c = sqlite3.connect(db2)
            n = c.execute("SELECT COUNT(*) FROM objects WHERE source<>'' AND kind<>'Configuration'").fetchone()[0]
            c.close()
            self.assertEqual(n, 0)

    def test_query_fields_added_by_extension(self):
        o = run(oq.main, "scan", self.main, "--root", self.main, "--db", self.db)
        self.assertNotIn("нет поля ext_Поле", o)
        o = run(oq.main, "scan", self.rep, "--root", self.main, "--db", self.db)
        self.assertIn("нет поля Нет", o)
        self.assertNotIn("нет поля ext_Поле", o)

    def test_extension_root_warning(self):
        o = run(oq.main, "scan", os.path.join(self.ext, "Локализация", "CommonModules"))
        self.assertIn("укажите --root основной конфигурации", o)


if __name__ == "__main__":
    unittest.main()


class SchemaUpgradeTest(unittest.TestCase):
    """Переход на новый формат индекса не теряет подключённые каталоги (--with)."""

    def dump(self, db):
        c = sqlite3.connect(db)
        try:
            return (sorted(c.execute("SELECT kind,name,source FROM objects")),
                    sorted(c.execute("SELECT unit,src_kind,src_name,dst_kind,dst_lc,how FROM refs")),
                    c.execute("SELECT value FROM meta WHERE key='extras'").fetchone())
        finally:
            c.close()

    def test_upgrade_keeps_extras(self):
        with tempfile.TemporaryDirectory() as d:
            main, ext, rep = make(d)
            old = os.path.join(d, "old.sqlite")
            with mock.patch.object(ix, "SCHEMA_VERSION", "7"):              # индекс прежней версии
                run(ix.main, "build", "--root", main, "--db", old, "--with", ext, "--with", rep)
            err = io.StringIO()
            with redirect_stderr(err):
                run(ix.main, "build", "--root", main, "--db", old)           # обычный build новой версии
            out = err.getvalue()
            self.assertIn("подключённые каталоги сохранены", out)
            fresh = os.path.join(d, "fresh.sqlite")
            run(ix.main, "build", "--root", main, "--db", fresh, "--with", ext, "--with", rep)
            got, want = self.dump(old), self.dump(fresh)
            self.assertTrue([o for o in got[0] if o[2]], "расширения пропали из индекса")
            self.assertEqual(got, want)


class PathsAndRedumpTest(unittest.TestCase):
    """Индекс вне выгрузки, поиск корня в подпапке cf, перевыгрузка без изменений."""

    def test_default_location_subfolder_root_and_redump(self):
        import onec_paths
        with tempfile.TemporaryDirectory() as d:
            os.environ.pop("ONEC_INDEX_DIR", None)
            try:
                main, _ext, _rep = make(d)
                work = os.path.join(d, "proj")
                os.rename(main, os.path.join(d, "cf_tmp"))
                os.makedirs(work)
                os.rename(os.path.join(d, "cf_tmp"), os.path.join(work, "cf"))
                cf = os.path.join(work, "cf")
                self.assertEqual(onec_paths.find_root(work), cf)          # корень найден в подпапке cf
                o = run(ix.main, "where", "--root", work)
                db = os.path.join(work, ".onec", "index.sqlite")            # индекс в папке проекта, над cf
                self.assertIn(db, o)
                run(ix.main, "build", "--root", work)
                self.assertEqual(onec_paths.default_db(cf), db)
                self.assertTrue(os.path.isfile(db))
                self.assertFalse(os.path.exists(os.path.join(cf, ".onec")))  # в выгрузке ничего не создаётся
                # «перевыгрузка»: те же файлы с новыми датами
                for dp, _dn, fns in os.walk(cf):
                    for fn in fns:
                        p = os.path.join(dp, fn)
                        st = os.stat(p)
                        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 10))
                o = run(ix.main, "build", "--root", work)
                self.assertIn("пересобрано: 0 из", o)
                self.assertIn("Без изменений содержимого (только новые даты файлов): 3", o)
                with open(os.path.join(cf, "CommonModules", "Модуль", "Ext", "Module.bsl"), "a", encoding="utf-8") as f:
                    f.write("// правка\n")
                o = run(ix.main, "build", "--root", work)
                self.assertIn("пересобрано: 1 из", o)
            finally:
                os.environ.pop("ONEC_INDEX_DIR", None)
