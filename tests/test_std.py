"""Тесты sync_standards.py и onec_std.py: синтетический источник + целостность офлайн-копии."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.join(os.path.dirname(HERE), "skills", "onec-configurator")
sys.path.insert(0, os.path.join(SK, "scripts"))
import onec_std  # noqa: E402
import sync_standards  # noqa: E402

STD = '''---
llms:
  ignore: true
---

###### #std900

# Тестовый стандарт про запросы

###### 1.

Не выполняйте запросы в цикле.

<!-- diagnostic-backlinks:start clause=1 -->
<div class="diagnostic-links"><a class="diagnostic-chip" href="../diagnostics/bslls/X.md">bslls:CreateQueryInCycle</a></div>
<!-- diagnostic-backlinks:end clause=1 -->

<!-- fix-backlinks:start clause=1 -->
<div class="fix-links"><a class="diagnostic-chip diagnostic-chip--fix" href="x">autoformat</a></div>
<!-- fix-backlinks:end clause=1 -->

###### 2.

Второй пункт. ![Схема](900.png){ width="10" }

###### 2.1.

Подпункт.

###### 3.

Третий.
'''


def run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = onec_std.main(list(argv))
    return rc, buf.getvalue()


class SyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        src = os.path.join(cls.tmp.name, "v8std")
        os.makedirs(os.path.join(src, "docs", "std"))
        os.makedirs(os.path.join(src, "docs", "diagnostics", "bslls"))
        with open(os.path.join(src, "docs", "std", "900.md"), "w", encoding="utf-8") as f:
            f.write(STD)
        with open(os.path.join(src, "docs", "diagnostics", "bslls", "CreateQueryInCycle.md"), "w", encoding="utf-8") as f:
            f.write("###### bslls:CreateQueryInCycle\n\n# Выполнение запроса в цикле\n")
        cls.out = os.path.join(cls.tmp.name, "standards")
        with redirect_stdout(io.StringIO()):
            sync_standards.build(src, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_snapshot(self):
        with open(os.path.join(self.out, "std", "900.md"), encoding="utf-8") as f:
            t = f.read()
        self.assertTrue(t.startswith("> Стандарт #std900. Первоисточник: [ИТС](https://its.1c.ru/db/v8std#content:900)"))
        self.assertIn("фирма «1С»", t.split("\n")[0])
        self.assertIn("Проверки: `bslls:CreateQueryInCycle`", t)
        self.assertNotIn("<div", t)
        self.assertNotIn("autoformat", t)
        self.assertIn("[рисунок «Схема» — см. ИТС]", t)
        with open(os.path.join(self.out, "index.json"), encoding="utf-8") as f:
            idx = json.load(f)
        s = idx["standards"][0]
        self.assertEqual((s["num"], s["clauses"], s["llms_ignore"]), (900, {"1": ["bslls:CreateQueryInCycle"]}, True))
        self.assertEqual(idx["diagnostics"]["bslls:CreateQueryInCycle"], "Выполнение запроса в цикле")

    def test_show_clause_with_subclauses(self):
        rc, o = run("--dir", self.out, "show", "#std900", "--clause", "2")
        self.assertEqual(rc, 0)
        self.assertIn("Второй пункт", o)
        self.assertIn("Подпункт", o)
        self.assertNotIn("Третий", o)

    def test_find_and_diag(self):
        rc, o = run("--dir", self.out, "find", "запросы", "цикле")
        self.assertIn("#std900", o)
        rc, o = run("--dir", self.out, "diag", "CreateQueryInCycle")
        self.assertIn("#std900 Тестовый стандарт про запросы — пункт 1", o)
        rc, o = run("--dir", self.out, "show", "901")
        self.assertEqual(rc, 3)


class SnapshotIntegrityTest(unittest.TestCase):
    """Офлайн-копия в репозитории: у каждого стандарта источник, ИТС и правообладатель."""

    def test_headers(self):
        base = os.path.join(SK, "standards")
        with open(os.path.join(base, "index.json"), encoding="utf-8") as f:
            idx = json.load(f)
        self.assertGreater(len(idx["standards"]), 300)
        for s in idx["standards"]:
            with open(os.path.join(base, "std", "%d.md" % s["num"]), encoding="utf-8") as f:
                first = f.readline()
            self.assertRegex(first, r"^> Стандарт #std%d\. Первоисточник: \[ИТС\]\(https://its\.1c\.ru/db/v8std#content:%d\)" % (s["num"], s["num"]))
            self.assertIn("фирма «1С»", first)
        with open(os.path.join(base, "README.md"), encoding="utf-8") as f:
            r = f.read()
        self.assertIn("Права на тексты принадлежат", r)
        self.assertIn("будет удалён", r)


if __name__ == "__main__":
    unittest.main()
