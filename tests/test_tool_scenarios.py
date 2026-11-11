"""Сценарии на стыке инструментов: ошибки чаще всего живут между ними, а не внутри одного.

  генератор -> doctor -> индекс -> undo: служебная папка .onec (журналы и резервные копии
      генератора) не видна как исходники ни одному инструменту; индекс видит новое и забывает
      отменённое;
  discover -> profile -> проверка профиля: одноимённые расширения не склеиваются, профиль
      принимает pipeline.py.
"""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "onec-configurator", "scripts")
sys.path.insert(0, SCRIPTS)
import onec_env as oe  # noqa: E402
import onec_gen as og  # noqa: E402
import onec_index as ix  # noqa: E402
import pipeline  # noqa: E402


def call(fn, *argv, cwd=None):
    o = io.StringIO()
    old = os.getcwd()
    try:
        if cwd:
            os.chdir(cwd)
        with redirect_stdout(o), redirect_stderr(o):
            try:
                rc = fn(list(argv))
            except SystemExit as e:
                rc = e.code
    finally:
        os.chdir(old)
    return rc, o.getvalue()


def doctor(project):
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "project_context.py"), "doctor", project],
                       capture_output=True, env=dict(os.environ, PYTHONUTF8="1"))
    return json.loads(r.stdout.decode("utf-8"))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


@unittest.skipUnless(og.has_lxml(), "нет lxml")
class GeneratorDoctorIndexUndoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.p)

    def tearDown(self):
        self.tmp.cleanup()

    def gen(self, *argv):
        rc, out = call(og.main, *argv, cwd=self.p)
        self.assertEqual(rc, 0, out)
        return out

    def test_scenario(self):
        self.gen("config", "new", "-Name", "Тест", "-OutputDir", "cf", "-FormatVersion", "2.20",
                 "-CompatibilityMode", "Version8_3_24")
        self.gen("config", "edit", "-ConfigPath", "cf", "-Operation", "modify-property", "-Value", "Version=1.0.0.2")
        write(os.path.join(self.p, "in", "cat.json"), json.dumps({"type": "Catalog", "name": "Товары"}, ensure_ascii=False))
        self.gen("object", "new", "-JsonPath", "in/cat.json", "-OutputDir", "cf")
        self.gen("ext", "new", "-Name", "Доработки", "-OutputDir", "ext/Доработки", "-ConfigPath", "cf")
        self.gen("ext", "borrow", "-ExtensionPath", "ext/Доработки", "-ConfigPath", "cf", "-Object", "Catalog.Товары")
        self.assertTrue(os.listdir(os.path.join(self.p, ".onec", "gen")))   # журналы и копии есть

        # doctor: одна конфигурация (резервные копии и метки не считаются исходниками), без ошибок разбора
        d = doctor(self.p)
        paths = [c["path"] for c in d["configurations"]]
        self.assertFalse([x for x in paths if ".onec" in x], paths)
        self.assertFalse([f for f in d["findings"] if ".onec" in json.dumps(f, ensure_ascii=False)], d["findings"])

        # discover: расширение одно, без копий из журнала генератора
        src = oe.find_sources(self.p, os.path.join(self.p, "cf"))
        self.assertEqual([e["dir"] for e in src["extensions"]], [os.path.join(self.p, "ext", "Доработки")])

        # индекс видит новый объект и заимствование
        db = os.path.join(self.p, ".onec", "index.sqlite")
        rc, out = call(ix.main, "build", "--root", os.path.join(self.p, "cf"), "--db", db,
                       "--with", os.path.join(self.p, "ext"))
        self.assertEqual(rc, 0, out)
        c = sqlite3.connect(db)
        try:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM objects WHERE kind='Catalog' AND name='Товары'").fetchone()[0], 2)
        finally:
            c.close()

        # undo заимствования -> индекс после пересборки его не видит, doctor по-прежнему чист
        rc, out = call(og.main, "undo", cwd=self.p)
        self.assertEqual(rc, 0, out)
        self.assertIn("cfe-borrow", out)
        rc, out = call(ix.main, "build", "--root", os.path.join(self.p, "cf"), "--db", db)
        self.assertEqual(rc, 0, out)
        c = sqlite3.connect(db)
        try:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM objects WHERE kind='Catalog' AND name='Товары'").fetchone()[0], 1)
        finally:
            c.close()
        d = doctor(self.p)
        self.assertEqual(d["configurations_total"], 2, d["configurations"])      # основная и расширение
        self.assertFalse([c for c in d["configurations"] if ".onec" in c["path"]])


class DiscoverProfileCheckTest(unittest.TestCase):
    """discover -> setup -> profile -> pipeline.py принимает профиль; одноимённые расширения различаются."""

    MD = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
          'version="2.20"><%s uuid="00000000-0000-0000-0000-000000000001"><Properties><Name>%s</Name>%s'
          '</Properties></%s></MetaDataObject>')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.p = os.path.join(t, "proj")
        write(os.path.join(self.p, "cf", "Configuration.xml"), self.MD % ("Configuration", "Альфа", "", "Configuration"))
        ext = "<ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose>"
        for sub in ("archive", "current"):
            write(os.path.join(self.p, "ext", sub, "Доп", "Configuration.xml"), self.MD % ("Configuration", "Доп", ext, "Configuration"))
        for sub, name in (("a", "3_X"), ("b", "X"), ("c", "X")):
            write(os.path.join(self.p, "epf", sub, name + ".xml"), self.MD % ("ExternalDataProcessor", name, "", "ExternalDataProcessor"))
        self.plat = os.path.join(t, "1cv8")
        for exe in ("1cv8.exe", "1cv8c.exe"):
            write(os.path.join(self.plat, "8.3.27.1859", "bin", exe), "")
        self.base = os.path.join(t, "bases", "test")
        write(os.path.join(self.base, "1Cv8.1CD"), "")
        self.v8i = os.path.join(t, "ibases.v8i")
        with open(self.v8i, "w", encoding="utf-8") as f:
            f.write("[Тест]\nConnect=File=\"%s\";\n" % self.base)
        self.common = ["--project", self.p, "--platform-dir", self.plat, "--ibases", self.v8i]
        for name in ("platform_roots", "ibases_files"):
            m = mock.patch.object(oe, name, lambda extra=None, estart=None: list(extra or []))
            m.start()
            self.addCleanup(m.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def profile(self, *args):
        rc, out = call(oe.main, "profile", *args, *self.common)
        if rc:
            return rc, out, None
        path = [ln.split(": ", 1)[1] for ln in out.splitlines() if ln.startswith("Профиль: ")][0]
        pipeline.load_profile(Path(path), Path(self.p) / "build" / "check", Path(self.p))
        with open(path, encoding="utf-8") as f:
            return rc, out, json.load(f)

    def test_scenario(self):
        rc, out = call(oe.main, "discover", *self.common)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("одноимённое"), 2, out)
        rc, out = call(oe.main, "setup", *self.common, "--base", "Тест")
        self.assertEqual(rc, 0, out)
        rc, out, _ = self.profile("cfe-build", "--extension", "Доп")
        self.assertEqual(rc, 2, out)
        self.assertIn("найдено в нескольких каталогах", out)
        current = os.path.join(self.p, "ext", "current", "Доп")
        rc, out, data = self.profile("cfe-build", "--extension", current)
        self.assertEqual(rc, 0, out)
        self.assertEqual(data["variables"]["cfe"], current)
        rc, out, data = self.profile("epf-build")
        self.assertEqual(rc, 0, out)
        names = [os.path.basename(o["path"].replace("\\", "/")) for s in data["steps"] for o in s.get("outputs", [])
                 if o["path"].lower().endswith(".epf")]
        self.assertEqual(sorted(n.lower() for n in names), sorted({n.lower() for n in names}))
        self.assertEqual(len(names), 3)


if __name__ == "__main__":
    unittest.main()
