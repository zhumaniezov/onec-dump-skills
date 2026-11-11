"""Тесты onec_query.py на синтетической выгрузке (без реальных данных)."""
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "skills", "onec-configurator", "scripts"))
sys.path.insert(0, HERE)
import onec_index as ix  # noqa: E402
import onec_query as oq  # noqa: E402
from test_index import NS, attr, w  # noqa: E402


def obj(kind, name, props="", child=""):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject %s version="2.20">\n'
            '<%s uuid="1"><Properties><Name>%s</Name>%s</Properties><ChildObjects>%s</ChildObjects></%s></MetaDataObject>'
            % (NS, kind, name, props, child, kind))


def member(tag, name, typ):
    return '<%s uuid="1"><Properties><Name>%s</Name><Type><v8:Type>%s</v8:Type></Type></Properties></%s>' % (tag, name, typ, tag)


def make(root):
    w(root, "Configuration.xml", obj("Configuration", "Т"))
    ts = ('<TabularSection uuid="1"><Properties><Name>Цены</Name></Properties><ChildObjects>'
          + attr("Цена", "xs:decimal") + '</ChildObjects></TabularSection>')
    w(root, "Catalogs/Товары.xml", obj("Catalog", "Товары", "", attr("Артикул", "xs:string") + attr("Группа", "cfg:CatalogRef.Товары") + ts))
    w(root, "Catalogs/Склады.xml", obj("Catalog", "Склады"))
    w(root, "Documents/Заказ.xml", obj("Document", "Заказ", "", attr("Товар", "cfg:CatalogRef.Товары")))
    w(root, "AccumulationRegisters/Остатки.xml", obj("AccumulationRegister", "Остатки", "<RegisterType>Balance</RegisterType>",
      member("Dimension", "Склад", "cfg:CatalogRef.Склады") + member("Dimension", "Товар", "cfg:CatalogRef.Товары")
      + member("Resource", "Количество", "xs:decimal")))
    w(root, "Reports/Отчет.xml", obj("Report", "Отчет", "", "<Template>Схема</Template>"))
    w(root, "Reports/Отчет/Templates/Схема.xml", obj("Template", "Схема", "<TemplateType>DataCompositionSchema</TemplateType>"))
    w(root, "Reports/Отчет/Templates/Схема/Ext/Template.xml",
      '<?xml version="1.0" encoding="UTF-8"?>\n<DataCompositionSchema xmlns="http://v8.1c.ru/8.1/data-composition-system/schema">\n'
      '<dataSet><name>Набор1</name><query>ВЫБРАТЬ\n\tТ.Артикул,\n\tТ.Цвет\nИЗ\n\tСправочник.Товары КАК Т\nГДЕ Т.Артикул &lt;&gt; ""</query></dataSet>\n'
      '</DataCompositionSchema>')


BSL = '''Процедура П(Список) Экспорт
\tЗапрос = Новый Запрос;
\tЗапрос.Текст = "ВЫБРАТЬ
\t|\tТ.Артикул КАК Артикул,
\t// комментарий внутри многострочной строки
\t|\tТ.Вес КАК Вес,
\t|\tТ.Группа.Артикул КАК А,
\t|\tТ.Группа.Цвет КАК Ц,
\t|\tТ.Цены.Цена КАК Ц1,
\t|\tТ.Цены.Скидка КАК Ц2,
\t|\t""текст"" КАК С
\t|ИЗ
\t|\tСправочник.Товары КАК Т
\t|\tПОЛНОЕ СОЕДИНЕНИЕ Справочник.Нет КАК Н ПО ИСТИНА";
\tЗапрос.Текст = "ВЫБРАТЬ О.КоличествоОстаток ИЗ РегистрНакопления.Остатки.Остатки(, Товар = &Т) КАК О
\t|ГДЕ О.Склад = &С И О.КоличествоОстаток > 0";
\tЗапрос.Текст = "ВЫБРАТЬ ПланОбмена.Ссылка ИЗ Справочник.Склады КАК ПланОбмена
\t|ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ ПланОбмена.Артикул ИЗ Справочник.Товары КАК ПланОбмена";
\tЗапрос.Текст = "ВЫБРАТЬ Х.Особое ИЗ (ВЫБРАТЬ 1 КАК Особое ИЗ Справочник.Товары КАК Х) КАК Х";
\tЗапрос.Текст = "ВЫБРАТЬ Т.ПолеШаблона ИЗ Справочник.Товары КАК Т";
\tЗапрос.Текст = СтрЗаменить(Запрос.Текст, "Т.ПолеШаблона", "Т.Артикул");
\tДля Каждого Э Из Запрос.Выполнить().Выгрузить() Цикл
\t\tР = Запрос.Выполнить();
\t\t// @skip-check query-in-loop
\t\tР = Запрос.Выполнить();
\tКонецЦикла;
\tПока Истина Цикл
\t\tР = Запрос.Выполнить();
\tКонецЦикла;
КонецПроцедуры
'''


def run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        oq.main(list(argv))
    return buf.getvalue()


class QueryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = cls.tmp.name
        make(cls.root)
        w(cls.root, "CommonModules/М/Ext/Module.bsl", BSL)
        w(cls.root, "CommonModules/М.xml", obj("CommonModule", "М"))
        cls.db = os.path.join(cls.root, ".onec", "index.sqlite")
        with redirect_stdout(io.StringIO()):
            ix.cmd_build(cls.root, cls.db)
        cls.out = run("scan", cls.root, "--root", cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def lines(self, rule):
        return [l for l in self.out.splitlines() if "[%s]" % rule in l]

    def test_extraction_and_lines(self):
        qs, _ = oq.bsl_queries(BSL)
        self.assertEqual(qs[0][0], 3)
        self.assertIn('"текст"', qs[0][1])
        self.assertIn("СКД 1", self.out)

    def test_unknown_fields(self):
        msgs = " | ".join(self.lines("unknown-field"))
        self.assertIn("М/Ext/Module.bsl:6", msgs)        # Т.Вес, номер строки с учётом строки-комментария
        self.assertIn("нет поля Вес", msgs)
        self.assertIn("нет поля Цвет у значения Т.Группа", msgs)
        self.assertIn("нет колонки Скидка", msgs)
        self.assertIn("Схема/Ext/Template.xml:5", msgs)   # поле в СКД (строка Т.Цвет)
        for ok in ("Артикул у", "ПланОбмена", "Особое", "ПолеШаблона", "Цена"):
            self.assertNotIn("нет поля " + ok, msgs)
            self.assertNotIn("нет колонки " + ok, msgs)

    def test_unknown_table(self):
        t = self.lines("unknown-table")
        self.assertEqual(len(t), 1)
        self.assertIn("Справочник.Нет", t[0])

    def test_vt_where_dimension_only(self):
        v = self.lines("vt-where")
        self.assertEqual(len(v), 1)
        self.assertIn("Склад", v[0])

    def test_full_join(self):
        self.assertEqual(len(self.lines("full-join")), 1)

    def test_query_in_loop(self):
        q = self.lines("query-in-loop")
        self.assertEqual(len(q), 1)                         # заголовок, @skip-check и Пока не считаются
        self.assertIn("Module.bsl:23", q[0])

    def test_index_refs_from_dcs(self):
        c = sqlite3.connect(self.db)
        r = c.execute("SELECT src_name, src_where FROM refs WHERE how='query' AND dst_lc='товары'").fetchall()
        c.close()
        self.assertEqual(r[0][0], "Отчет")
        self.assertIn("Template:Схема", r[0][1])

    def test_without_index(self):
        out = run("scan", os.path.join(self.root, "CommonModules"), "--db", os.path.join(self.root, "nope.sqlite"))
        self.assertIn("Индекс не найден", out)
        self.assertNotIn("[unknown-field]", out)
        self.assertIn("[query-in-loop]", out)


if __name__ == "__main__":
    unittest.main()
