"""Тесты ordinary_form.py на синтетической обычной форме (без реальных данных)."""
import hashlib
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "skills", "onec-configurator", "scripts"))
import ordinary_form as of  # noqa: E402
import v8container  # noqa: E402

BTN = "6ff79819-710e-4145-97cd-1618da79e3e2"
PANEL = "09ccdc77-ea1a-4a6d-ab1c-3435eada2433"
FLAG = "35af3d93-d7c7-4a2e-a8eb-bac87a1a3f26"
LAYOUT = "{8,8,8,100,20,1,{0},{0}}"

INNER = ('{' + PANEL + ',3,{1,{{10,1},0},{2,'
         '{' + BTN + ',1,{3,{1,1,{"ru","Выполнить"}},{0,"Кнопка1Нажатие",{1,1,{"ru","Нажатие"}}}},' + LAYOUT + ',{14,"Кнопка1",4294967295,0,0,0},{0}},'
         '{' + FLAG + ',2,{3,{1,1,{"ru","Признак"}}},' + LAYOUT + ',{14,"Признак",4294967295,0,0,0},{0}}'
         '}},' + LAYOUT + ',{14,"Панель1",4294967295,0,0,0},{0}}')
FORM = ('\ufeff{23,\r\n{16,{{1,0},4,4294967295},\r\n'
        '{' + PANEL + ',{1,{{10,1},0},{1,' + INNER + '}}}'
        '},\r\n{{-1},2,{2,{{1},1,1,"Признак",{"Pattern",{"B"}}},{{2},0,1,"Сумма",{"Pattern",{"N",15,2,0}}}}},'
        '{00000000-0000-0000-0000-000000000000,0},'
        '{1,{70001,e1692cc2-605b-4535-84dd-28440238746c,{3,"ПриОткрытии",{1,"ПриОткрытии"}}}}}')
MODULE = ('﻿Процедура ПриОткрытии()\r\nКонецПроцедуры\r\n\r\n'
          'Процедура Кнопка1Нажатие(Элемент)\r\nКонецПроцедуры\r\n\r\n'
          'Процедура Служебная()\r\nКонецПроцедуры\r\n')


def make_bin(path):
    ts = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    data = v8container.build([("form", ts, ts, FORM.encode("utf-8")), ("module", ts, ts, MODULE.encode("utf-8"))])
    with open(path, "wb") as f:
        f.write(data)
    return hashlib.sha256(data).hexdigest()


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = of.main(list(argv))
    return rc, buf.getvalue()


class OrdinaryFormTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, ".onec"))          # папка проекта: сюда уходят резервные копии
        os.makedirs(os.path.join(self.tmp.name, "cf", "Forms"))
        self.bin = os.path.join(self.tmp.name, "cf", "Forms", "Form.bin")
        self.sha = make_bin(self.bin)

    def tearDown(self):
        self.tmp.cleanup()

    def test_info(self):
        rc, out = run("info", self.bin)
        self.assertEqual(rc, 0)
        self.assertIn("Панель Панель1", out)
        self.assertIn("  Кнопка Кнопка1  «Выполнить»  обработчики: Кнопка1Нажатие", out)
        self.assertIn("Флажок Признак", out)
        self.assertNotIn("«Нажатие»", out)          # подпись события не выдаётся за подпись элемента
        self.assertIn("Сумма: Число(15,2)", out)
        self.assertIn("70001 -> ПриОткрытии", out)
        self.assertIn("без привязки к событиям формы/элементов (1): Служебная", out)

    def test_nesting(self):
        tree = of.parse_brace(FORM)
        els = of.elements(tree, {"кнопка1нажатие"})
        self.assertEqual([(d, n) for d, _c, n, _cap, _h in els], [(0, "Панель1"), (1, "Кнопка1"), (1, "Признак")])

    def test_set_module_dry_run_and_apply(self):
        new = os.path.join(self.tmp.name, "new.bsl")
        with open(new, "w", encoding="utf-8", newline="\n") as f:
            f.write(MODULE.lstrip("﻿").replace("\r\n", "\n") + "// добавлено\n")
        rc, out = run("set-module", self.bin, new, "--expected-sha256", self.sha)
        self.assertEqual(rc, 0)
        self.assertIn("+// добавлено", out)
        self.assertEqual(_sha(self.bin), self.sha)  # не записано
        rc, out = run("set-module", self.bin, new, "--expected-sha256", self.sha, "--apply")
        baks = [os.path.join(dp, f) for dp, _d, fs in os.walk(os.path.join(self.tmp.name, ".onec", "backups")) for f in fs]
        self.assertEqual(len(baks), 1)                              # исходный файл сохранён до записи
        self.assertEqual(_sha(baks[0]), self.sha)
        self.assertTrue(baks[0].endswith(os.path.join("cf", "Forms", "Form.bin")))
        self.assertIn("Исходный файл сохранён", out)
        self.assertEqual(rc, 0)
        with open(self.bin, "rb") as f:
            ents = {e[0]: e for e in v8container.parse(f.read())}
        self.assertEqual(ents["form"][3], FORM.encode("utf-8"))
        body = ents["module"][3]
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(body.count(b"\n"), body.count(b"\r\n"))
        self.assertEqual(ents["module"][1], b"\x01\x02\x03\x04\x05\x06\x07\x08")

    def test_stale_hash_refused(self):
        new = os.path.join(self.tmp.name, "new.bsl")
        with open(new, "w", encoding="utf-8") as f:
            f.write("// x\n")
        rc, out = run("set-module", self.bin, new, "--expected-sha256", "0" * 64, "--apply")
        self.assertEqual(rc, 4)
        self.assertEqual(_sha(self.bin), self.sha)

    def test_verify_detects_compressed(self):
        import zlib
        ts = b"\0" * 8
        comp = zlib.compress(FORM.encode("utf-8"))[2:-4]
        bad = os.path.join(self.tmp.name, "bad.bin")
        with open(bad, "wb") as f:
            f.write(v8container.build([("form", ts, ts, comp), ("module", ts, ts, b"")]))
        rc, out = run("verify", bad)
        self.assertNotEqual(rc, 0)
        self.assertIn("похожа на сжатую", out)

    def test_verify_ok(self):
        rc, out = run("verify", self.bin)
        self.assertEqual(rc, 0)
        self.assertIn("элементов 3, реквизитов 2, событий 1", out)


if __name__ == "__main__":
    unittest.main()
