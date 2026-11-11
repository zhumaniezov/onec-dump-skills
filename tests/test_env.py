"""Тесты onec_env.py: поиск платформ и баз, настройка проекта, профили (без установленной 1С)."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "onec-configurator", "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)
import onec_env as oe  # noqa: E402
import pipeline  # noqa: E402
from pathlib import Path  # noqa: E402
from test_extensions import md  # noqa: E402
from test_index import w  # noqa: E402

V8I = ('﻿[Разработка]\r\nID=1\r\nFolder=/\r\n'
       '[Альфа тест]\r\nConnect=File="%s";\r\nID=2\r\nFolder=/Разработка\r\nVersion=8.3\r\n'
       '[Альфа рабочая]\r\nConnect=Srvr="srv1c:1541";Ref="alfa_prod";\r\nID=3\r\n'
       '[Портал]\r\nConnect=ws="https://example.invalid/base";\r\nID=4\r\n')


def touch(path, data=b""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def run(*argv):
    o, e = io.StringIO(), io.StringIO()
    with redirect_stdout(o), redirect_stderr(e):
        rc = oe.main(list(argv))
    return rc, o.getvalue() + e.getvalue()


class EnvCase(unittest.TestCase):
    """Проект: cf (формат 2.20), расширение, внешняя обработка; две платформы; список баз."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.proj = os.path.join(t, "alfa")
        self.cf = os.path.join(self.proj, "cf")
        w(self.cf, "Configuration.xml", md("Configuration", "Альфа", "<DefaultRunMode>ManagedApplication</DefaultRunMode>"))
        w(self.cf, "Catalogs/Товары.xml", md("Catalog", "Товары"))
        w(self.cf, "Catalogs/Товары/Ext/ObjectModule.bsl", "Процедура П()\nКонецПроцедуры\n")
        w(os.path.join(self.proj, "ext", "Локализация"), "Configuration.xml", md(
            "Configuration", "Локализация", "<ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose>"))
        w(os.path.join(self.proj, "epf", "Загрузка"), "Загрузка.xml", md("ExternalDataProcessor", "ЗагрузкаДанных"))
        w(os.path.join(self.proj, "erf", "Продажи"), "Продажи.xml", md("ExternalReport", "Продажи"))
        self.plat = os.path.join(t, "1cv8")
        for v in ("8.3.24.1691", "8.3.27.1859"):
            touch(os.path.join(self.plat, v, "bin", "1cv8.exe"))
            touch(os.path.join(self.plat, v, "bin", "1cv8c.exe"))
        os.makedirs(os.path.join(self.plat, "common"))
        self.base = os.path.join(t, "bases", "alfa_test")
        touch(os.path.join(self.base, "1Cv8.1CD"))
        self.v8i = os.path.join(t, "ibases.v8i")
        with open(self.v8i, "w", encoding="utf-8", newline="") as f:
            f.write(V8I % self.base)
        self.common = ["--project", self.proj, "--platform-dir", self.plat, "--ibases", self.v8i]
        # только заданные в тесте каталоги: настройки машины, где идёт прогон, не участвуют
        for name, fn in (("platform_roots", lambda extra=None, estart=None: list(extra or [])),
                         ("ibases_files", lambda extra=None, estart=None: list(extra or []))):
            p = mock.patch.object(oe, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def setup_ok(self, *extra):
        rc, out = run("setup", *self.common, "--base", "альфа тест", *extra)
        self.assertEqual(rc, 0, out)
        return out

    def profile(self, *args):
        rc, out = run("profile", *args, *self.common)
        self.assertEqual(rc, 0, out)
        path = [ln.split(": ", 1)[1] for ln in out.splitlines() if ln.startswith("Профиль: ")][0]
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # профиль принимает сам pipeline.py
        run_dir = Path(self.proj) / "build" / "check-run"
        pipeline.load_profile(Path(path), run_dir.absolute(), Path(self.proj))
        return data, out


class DiscoverTest(EnvCase):
    def test_platforms_sorted_and_marked(self):
        found = oe.find_platforms([self.plat, os.path.join(self.tmp.name, "нет")])
        self.assertEqual([p["version"] for p in found], ["8.3.27.1859", "8.3.24.1691"])
        self.assertTrue(found[0]["client"].endswith("1cv8c.exe"))
        rc, out = run("discover", *self.common)
        self.assertEqual(rc, 0)
        self.assertIn("формат выгрузки 2.20", out)
        self.assertIn("платформа по формату выгрузки: 8.3.27", out)
        line = [ln for ln in out.splitlines() if "8.3.27.1859" in ln][0]
        self.assertIn("<- по формату выгрузки", line)
        self.assertNotIn("<-", [ln for ln in out.splitlines() if "8.3.24.1691" in ln][0])
        self.assertIn("Расширения (1):", out)
        self.assertIn("Внешние обработки и отчёты (2):", out)
        self.assertIn("Настройка проекта: не задана", out)

    def test_linux_layout(self):
        root = os.path.join(self.tmp.name, "opt")
        touch(os.path.join(root, "8.3.27.1900", "1cv8"))
        self.assertEqual(oe.find_platforms([root])[0]["version"], "8.3.27.1900")

    def test_bases(self):
        bases = oe.find_bases([self.v8i])
        self.assertEqual([(b["name"], b["kind"]) for b in bases],
                         [("Альфа тест", "file"), ("Альфа рабочая", "server"), ("Портал", "web")])
        self.assertEqual(oe.base_target(bases[1]), "srv1c:1541\\alfa_prod")
        self.assertTrue(oe.looks_test(bases[0]))
        self.assertFalse(oe.looks_test(bases[1]))

    def test_estart_cfg_utf16(self):
        d = os.path.join(self.tmp.name, "estart")
        os.makedirs(d)
        with open(os.path.join(d, "1CEStart.cfg"), "w", encoding="utf-16") as f:
            f.write("DefaultVersion=8.3\nInstalledLocation=%s\nCommonInfoBases=%s\n" % (self.plat, self.v8i))
        self.assertEqual(oe.estart_values("InstalledLocation", [d]), [self.plat])
        self.assertEqual(oe.estart_values("commoninfobases", [d]), [self.v8i])

    def test_decode(self):
        self.assertEqual(oe.decode_any("Ошибка".encode("cp1251")), "Ошибка")
        self.assertEqual(oe.decode_any("Ошибка".encode("utf-16")), "Ошибка")
        self.assertEqual(oe.decode_any(b"\xef\xbb\xbf" + "Ошибка".encode("utf-8")), "Ошибка")


class SetupTest(EnvCase):
    def test_setup_picks_platform_by_format(self):
        out = self.setup_ok()
        env = oe.load_env(self.proj)
        self.assertEqual(env["platform"]["version"], "8.3.27.1859")
        self.assertEqual(env["test_base"], {"name": "Альфа тест", "kind": "file", "path": self.base})
        self.assertNotIn("ВНИМАНИЕ", out)
        self.assertIn("Строка для CLAUDE.md", out)

    def test_base_is_never_chosen_automatically(self):
        rc, out = run("setup", *self.common)
        self.assertEqual(rc, 2)
        self.assertIn("Скрипт базу не выбирает", out)
        self.assertIsNone(oe.load_env(self.proj))

    def test_unknown_and_web_base(self):
        rc, out = run("setup", *self.common, "--base", "Нет такой")
        self.assertEqual(rc, 2)
        self.assertIn("Альфа тест", out)
        rc, out = run("setup", *self.common, "--base", "Портал")
        self.assertEqual(rc, 2)

    def test_ambiguous_platform_requires_choice(self):
        touch(os.path.join(self.plat, "8.3.27.2000", "bin", "1cv8.exe"))
        rc, out = run("setup", *self.common, "--base", "Альфа тест")
        self.assertEqual(rc, 2)
        self.assertIn("8.3.27.2000", out)
        self.assertIn("8.3.27.1859", out)
        self.setup_ok("--platform", "8.3.27.2000")
        self.assertEqual(oe.load_env(self.proj)["platform"]["version"], "8.3.27.2000")

    def test_server_base_warns_when_not_test(self):
        rc, out = run("setup", *self.common, "--base-server", "srv1c/alfa_prod", "--user", "Админ", "--password-env", "ONEC_PW")
        self.assertEqual(rc, 0, out)
        self.assertIn("ВНИМАНИЕ", out)
        env = oe.load_env(self.proj)
        self.assertEqual(env["password_env"], "ONEC_PW")
        self.assertEqual(oe.conn_args(env), ["/S", "srv1c\\alfa_prod", "/N", "Админ", "/P", "${env:ONEC_PW}"])

    def test_file_base_must_exist(self):
        rc, _out = run("setup", *self.common, "--base-file", os.path.join(self.tmp.name, "пусто"))
        self.assertEqual(rc, 2)


class ProfileTest(EnvCase):
    def test_requires_setup(self):
        rc, out = run("profile", "cf-check", *self.common)
        self.assertEqual(rc, 2)
        self.assertIn("setup", out)

    def test_cf_check_and_build(self):
        self.setup_ok()
        data, out = self.profile("cf-check")
        self.assertEqual([s["id"] for s in data["steps"]], ["load-cf", "check-cf"])
        load = data["steps"][0]["command"]
        self.assertEqual(load[:4], ["${onec}", "DESIGNER", "/F", self.base])
        self.assertIn("/LoadConfigFromFiles", load)
        self.assertNotIn("/UpdateDBCfg", json.dumps(data))
        self.assertIn("-ThinClient", data["steps"][1]["command"])
        self.assertIn("--run-dir", out)
        data, _ = self.profile("cf-build", "--update-db")
        self.assertEqual([s["id"] for s in data["steps"]], ["load-cf", "check-cf", "update-db", "dump-cf"])
        self.assertTrue(data["steps"][-1]["outputs"][-1]["path"].endswith("Альфа.cf"))
        self.assertEqual(data["variables"]["onec"], oe.load_env(self.proj)["platform"]["exe"])

    def test_ordinary_application_modes(self):
        w(self.cf, "Configuration.xml", md("Configuration", "Альфа", "<DefaultRunMode>OrdinaryApplication</DefaultRunMode>"))
        self.setup_ok()
        data, _ = self.profile("cf-check")
        self.assertIn("-ThickClientOrdinaryApplication", data["steps"][1]["command"])
        self.assertNotIn("-ThinClient", data["steps"][1]["command"])

    def test_partial_load_and_password(self):
        self.setup_ok("--user", "Админ", "--password-env", "ONEC_PW")
        lst = os.path.join(self.proj, "changed.txt")
        touch(lst, b"Catalogs/x.xml\n")
        data, out = self.profile("cf-check", "--list-file", lst)
        load = data["steps"][0]["command"]
        i = load.index("-listFile")
        self.assertEqual(load[i:i + 5], ["-listFile", lst, "-Format", "Hierarchical", "-partial"])
        self.assertIn("${env:ONEC_PW}", load)                # в профиле имя переменной, а не пароль
        self.assertIn("ONEC_PW не задана", out)

    def test_cfe_epf_yaxunit(self):
        self.setup_ok()
        data, _ = self.profile("cfe-build")
        self.assertEqual([s["id"] for s in data["steps"]], ["load-cfe", "check-cfe", "dump-cfe"])
        self.assertEqual(data["variables"]["extension"], "Локализация")
        data, _ = self.profile("epf-build")
        outs = sorted(os.path.basename(s["outputs"][-1]["path"]) for s in data["steps"])
        self.assertEqual(outs, ["Загрузка.epf", "Продажи.erf"])
        data, _ = self.profile("epf-build", "--object", "Продажи")
        self.assertEqual(len(data["steps"]), 1)
        rc, _out = run("profile", "yaxunit", *self.common)
        self.assertEqual(rc, 2)
        data, _ = self.profile("yaxunit", "--extension", "tests")
        self.assertEqual(data["steps"][0]["kind"], "test")
        self.assertTrue(data["variables"]["client"].endswith("1cv8c.exe"))

    def test_next_run_dir(self):
        os.makedirs(os.path.join(self.proj, "build", "cf-check-001"))
        self.assertTrue(oe.next_run_dir(self.proj, "cf-check").endswith("cf-check-002"))


class ChangedAndLogTest(EnvCase):
    def test_newer_than(self):
        ref = os.path.join(self.proj, "ref")
        touch(ref)
        past = time.time() - 100
        for dp, _dn, fns in os.walk(self.cf):
            for fn in fns:
                os.utime(os.path.join(dp, fn), (past, past))
        os.utime(ref, (past + 50, past + 50))
        rc, out = run("changed", "--root", self.cf, "--newer-than", ref)
        self.assertEqual(rc, 3)                              # изменений нет — список не пишется
        w(self.cf, "Catalogs/Товары/Ext/ObjectModule.bsl", "Процедура П()\n\t// правка\nКонецПроцедуры\n")
        w(self.cf, "ConfigDumpInfo.xml", "<x/>")
        rc, out = run("changed", "--root", self.cf, "--newer-than", ref)
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.proj, ".onec", "tmp", "changed.txt"), "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw[3:].decode("utf-8").split(), ["Catalogs/Товары/Ext/ObjectModule.bsl"])

    @unittest.skipUnless(shutil.which("git"), "нет git")
    def test_git(self):
        def git(*a):
            subprocess.run(["git", "-C", self.proj, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "-c", "core.autocrlf=false", *a], check=True, capture_output=True)
        git("init", "-q")
        git("add", "-A")
        git("commit", "-q", "-m", "x")
        w(self.cf, "Catalogs/Товары/Ext/ObjectModule.bsl", "Процедура П()\n\t// правка\nКонецПроцедуры\n")
        w(self.cf, "Catalogs/Новый.xml", md("Catalog", "Новый"))
        os.remove(os.path.join(self.cf, "Catalogs", "Товары.xml"))
        out_file = os.path.join(self.tmp.name, "list.txt")
        rc, out = run("changed", "--root", self.cf, "--git", "--out", out_file)
        self.assertEqual(rc, 0, out)
        self.assertIn("Удалённых файлов: 1", out)
        with open(out_file, encoding="utf-8-sig") as f:
            self.assertEqual(f.read().split(), ["Catalogs/Новый.xml", "Catalogs/Товары/Ext/ObjectModule.bsl"])

    def test_log_tail_cp1251(self):
        p = os.path.join(self.tmp.name, "load.log")
        touch(p, "\r\n".join("строка %d" % i for i in range(1, 101)).encode("cp1251"))
        rc, out = run("log", p, "--tail", "3")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), ["... пропущено строк: 97", "строка 98", "строка 99", "строка 100"])


FAKE_1CV8 = """#!%s
# Заглушка 1cv8 для теста: пишет код результата, журнал и запрошенные файлы.
import json, os, sys
a = sys.argv[1:]
def after(key):
    return a[a.index(key) + 1] if key in a else None
with open(os.environ["FAKE_1CV8_CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps(a, ensure_ascii=False) + "\\n")
with open(after("/Out"), "w", encoding="cp1251") as f:
    f.write("Готово")
with open(after("/DumpResult"), "w", encoding="utf-16") as f:
    f.write("0")
for key in ("/DumpCfg",):
    if after(key):
        open(after(key), "wb").write(b"cf")
if "/LoadExternalDataProcessorOrReportFromFiles" in a:
    open(a[a.index("/LoadExternalDataProcessorOrReportFromFiles") + 2], "wb").write(b"epf")
"""


@unittest.skipIf(os.name == "nt", "заглушка платформы — исполняемый скрипт, на Windows так не запускается")
class FakePlatformRunTest(EnvCase):
    """Сгенерированный профиль проходит через pipeline.py с заглушкой вместо 1cv8."""

    def apply(self, profile_args, env=None):
        rc, out = run("profile", *profile_args, *self.common)
        self.assertEqual(rc, 0, out)
        cmd = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("python ")][0]
        import shlex
        argv = [sys.executable] + shlex.split(cmd)[1:] + ["--apply"]
        calls = os.path.join(self.tmp.name, "calls.jsonl")
        e = dict(os.environ, FAKE_1CV8_CALLS=calls, PYTHONUTF8="1", **(env or {}))
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", env=e)
        with open(calls, encoding="utf-8") as f:
            return r, [json.loads(x) for x in f.read().splitlines()], argv[argv.index("--run-dir") + 1]

    def setUp(self):
        super().setUp()
        for v in ("8.3.24.1691", "8.3.27.1859"):
            exe = os.path.join(self.plat, v, "bin", "1cv8.exe")
            with open(exe, "w", encoding="utf-8") as f:
                f.write(FAKE_1CV8 % sys.executable)
            os.chmod(exe, 0o755)

    def test_cf_build_run(self):
        self.setup_ok("--user", "Админ", "--password-env", "ONEC_PW")
        r, calls, run_dir = self.apply(["cf-build", "--update-db"], {"ONEC_PW": "пароль 1"})
        self.assertEqual(r.returncode, 0, r.stdout)
        report = json.loads(r.stdout)
        self.assertEqual([(s["id"], s["status"]) for s in report["steps"]],
                         [("load-cf", "passed"), ("check-cf", "passed"), ("update-db", "passed"), ("dump-cf", "passed")])
        self.assertEqual([c[0] for c in calls], ["DESIGNER"] * 4)
        self.assertEqual(calls[0][1:7], ["/F", self.base, "/N", "Админ", "/P", "пароль 1"])
        self.assertEqual(calls[0][calls[0].index("/LoadConfigFromFiles") + 1], self.cf)
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "Альфа.cf")))
        self.assertNotIn("пароль 1", r.stdout)
        rc, out = run("log", os.path.join(run_dir, "load-cf.log"))
        self.assertEqual(out.strip(), "Готово")

    def test_epf_build_run(self):
        self.setup_ok()
        r, calls, run_dir = self.apply(["epf-build"])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(sorted(os.listdir(os.path.join(run_dir, "out"))), ["Загрузка.epf", "Продажи.erf"])


FAKE_OSCRIPT = """#!%s
# Заглушка oscript + vanessa-runner: пишет вызов и создаёт результаты, которые обещает команда.
import json, os, sys
a = sys.argv[2:]                                  # sys.argv[1] — путь к main.os
def after(key):
    return a[a.index(key) + 1] if key in a else None
with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "pwd": os.environ.get("VRUNNER_DBPWD"),
                        "user": os.environ.get("VRUNNER_DBUSER"), "stray": os.environ.get("VRUNNER_IBCONNECTION")},
                       ensure_ascii=False) + "\\n")
cmd = " ".join(a[:2])
if cmd in ("validate syntax-check", "test yaxunit"):
    case = "" if cmd == "validate syntax-check" else '<testcase name="t1"/>'
    with open(after("--report-path"), "w", encoding="utf-8") as f:
        f.write('<testsuite tests="%%d">%%s</testsuite>' %% (1 if case else 0, case))
elif cmd in ("cf unload", "cfe unload"):
    open(a[-1], "wb").write(b"bin")
elif cmd == "epf compile":
    src = a[-1]
    xml = [n for n in os.listdir(src) if n.endswith(".xml")][0]
    os.makedirs(after("--out"), exist_ok=True)
    ext = ".erf" if b"ExternalReport" in open(os.path.join(src, xml), "rb").read() else ".epf"
    open(os.path.join(after("--out"), xml[:-4] + ext), "wb").write(b"bin")
print("vrunner: ok")
"""


class VrunnerBase(EnvCase):
    """Подделка установки OneScript с глобальным vanessa-runner 3.0.2."""

    def setUp(self):
        super().setUp()
        root = os.path.join(self.tmp.name, "OneScript")
        self.oscript = os.path.join(root, "bin", "oscript")
        touch(self.oscript)
        self.main_os = os.path.join(root, "lib", "vanessa-runner", "src", "main.os")
        touch(self.main_os)
        touch(os.path.join(root, "lib", "vanessa-runner", "packagedef"),
              '#Использовать 1commands\nОписание.Имя("vanessa-runner")\n\t\t.Версия("3.0.2")\n'.encode("utf-8"))
        # платформы теста считаются установленными в стандартный каталог, где их ищет vanessa-runner
        p = mock.patch.object(oe, "standard_platform_roots", lambda: [self.plat])
        p.start()
        self.addCleanup(p.stop)

    def setup_vr(self, *extra):
        return self.setup_ok("--runner", "vrunner", "--oscript", self.oscript, *extra)


class VrunnerPlatformTest(VrunnerBase):
    """vanessa-runner выбирает платформу по полной сборке и только в стандартных каталогах."""

    def test_platform_by_path_takes_discovered_build(self):
        exe = os.path.join(self.plat, "8.3.27.1859", "bin", "1cv8.exe")
        self.setup_vr("--platform", exe)
        env = oe.load_env(self.proj)
        self.assertEqual(env["platform"]["version"], "8.3.27.1859")
        data, _ = self.profile("cf-check", "--runner", "vrunner")
        cmd = data["steps"][0]["command"]
        self.assertEqual(cmd[cmd.index("--v8version") + 1], "8.3.27.1859")

    def test_custom_location_refused_for_vrunner(self):
        other = os.path.join(self.tmp.name, "свой", "bin", "1cv8.exe")
        touch(other)
        rc, out = run("setup", *self.common, "--base", "альфа тест", "--runner", "vrunner",
                      "--oscript", self.oscript, "--platform", other)
        self.assertEqual(rc, 2, out)
        self.assertIn("сборка платформы не определена", out)
        custom = os.path.join(self.tmp.name, "свой", "8.3.27.1859", "bin", "1cv8.exe")
        touch(custom)
        rc, out = run("setup", *self.common, "--base", "альфа тест", "--runner", "vrunner",
                      "--oscript", self.oscript, "--platform", custom)
        self.assertEqual(rc, 2, out)
        self.assertIn("вне стандартного каталога", out)
        # с исполнителем 1cv8 та же платформа годится; профиль vanessa-runner — отказ, а не пустой --v8version
        rc, out = run("setup", *self.common, "--base", "альфа тест", "--oscript", self.oscript, "--platform", other)
        self.assertEqual(rc, 0, out)
        self.assertIn("через vanessa-runner с этой платформой не строятся", out)
        rc, out = run("profile", "cf-check", "--runner", "vrunner", *self.common)
        self.assertEqual(rc, 2, out)
        self.assertIn("сборка платформы не определена", out)
        self.profile("cf-check")                                        # 1cv8 работает


class VrunnerProfileTest(VrunnerBase):
    def test_discover_and_setup(self):
        self.assertEqual(oe.find_vrunner(self.proj, self.oscript), self.main_os)
        self.assertEqual(oe.vrunner_version(self.main_os), "3.0.2")
        rc, out = run("setup", *self.common, "--base", "Альфа тест", "--runner", "vrunner",
                      "--oscript", os.path.join(self.tmp.name, "нет"))
        self.assertEqual(rc, 2)
        self.assertIn("нет OneScript", out)
        out = self.setup_vr()
        self.assertIn("vanessa-runner 3.0.2", out)
        env = oe.load_env(self.proj)
        self.assertEqual((env["runner"], env["vrunner"]["main"]), ("vrunner", self.main_os))

    def test_local_install_preferred(self):
        local = os.path.join(self.proj, "oscript_modules", "vanessa-runner", "src", "main.os")
        touch(local)
        self.assertEqual(oe.find_vrunner(self.proj, self.oscript), local)

    def test_cf_profile_commands(self):
        self.setup_vr("--user", "Админ", "--password-env", "ONEC_PW")
        data, out = self.profile("cf-build", "--update-db")
        self.assertIn("исполнитель: vanessa-runner 3.0.2", out)
        self.assertEqual([s["id"] for s in data["steps"]], ["load-cf", "check-cf", "update-db", "dump-cf"])
        load = data["steps"][0]["command"]
        self.assertEqual(load[:5], ["${oscript}", "${vrunner}", "cf", "load", "--src-format"])
        self.assertEqual(load[-1], "${cf}")                                 # путь — последним, после всех опций
        self.assertIn("--no-update-db", load)
        self.assertEqual(load[load.index("--ibconnection") + 1], "/F" + self.base)
        self.assertEqual(load[load.index("--v8version") + 1], "8.3.27.1859")
        flat = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("--db-pwd", flat)                                  # пароль не в командной строке
        self.assertEqual(data["steps"][0]["env"], {"VRUNNER_DBUSER": "Админ", "VRUNNER_DBPWD": "${env:ONEC_PW}"})
        self.assertEqual(data["env_unset_prefixes"], ["VRUNNER_", "RUNNER_"])
        check = data["steps"][1]["command"]
        self.assertEqual(check[2:6], ["validate", "syntax-check", "--target", "main"])
        self.assertIn("ThinClient", check)
        self.assertEqual(data["steps"][1]["outputs"][0]["kind"], "junit")
        self.assertEqual(data["steps"][3]["command"][-1], data["steps"][3]["outputs"][0]["path"])
        for st in data["steps"]:
            self.assertEqual(st["cwd"], "${run}")                           # autumn-properties.json проекта не читается

    def test_runner_override_and_increment(self):
        self.setup_ok()                                                     # по умолчанию 1cv8
        rc, out = run("profile", "cf-check", "--increment", *self.common)
        self.assertEqual(rc, 2)
        self.assertIn("только для --runner vrunner", out)
        rc, out = run("profile", "cf-check", "--runner", "vrunner", *self.common)
        self.assertEqual(rc, 2)                                             # vrunner не найден при setup
        self.setup_vr()
        data, _ = self.profile("cf-check", "--increment")
        self.assertIn("--increment", data["steps"][0]["command"])
        data, _ = self.profile("cf-check", "--runner", "1cv8")
        self.assertEqual(data["steps"][0]["command"][0], "${onec}")

    def test_cfe_epf_yaxunit(self):
        self.setup_vr()
        data, _ = self.profile("cfe-build")
        load = data["steps"][0]["command"]
        self.assertEqual(load[load.index("--extension-name") + 1], "${extension}")
        self.assertEqual(load[-1], "${cfe}")
        self.assertEqual(data["steps"][1]["command"][data["steps"][1]["command"].index("--target") + 1], "${extension}")
        data, _ = self.profile("epf-build")
        cmds = [s["command"] for s in data["steps"]]
        self.assertTrue(all(c[2:4] == ["epf", "compile"] for c in cmds))
        self.assertTrue(all("--ibconnection" not in c for c in cmds))      # временная база, тестовая не нужна
        w(os.path.join(self.proj, "erf", "Продажи"), "Второй.xml", md("ExternalReport", "Второй"))
        data, _ = self.profile("epf-build", "--object", "Продажи")
        self.assertEqual(data["steps"][0]["command"][:2], ["${onec}", "DESIGNER"])   # каталог с двумя объектами
        data, _ = self.profile("yaxunit", "--extension", "tests")
        y = data["steps"][0]["command"]
        self.assertEqual(y[2:6], ["test", "yaxunit", "--ext", "${tests_extension}"])


@unittest.skipIf(os.name == "nt", "заглушка — исполняемый скрипт, на Windows так не запускается")
class VrunnerRunTest(VrunnerBase):
    """Профиль vanessa-runner проходит через pipeline.py с заглушкой oscript."""

    def setUp(self):
        super().setUp()
        with open(self.oscript, "w", encoding="utf-8") as f:
            f.write(FAKE_OSCRIPT % sys.executable)
        os.chmod(self.oscript, 0o755)

    def apply(self, *profile_args, env=None):
        rc, out = run("profile", *profile_args, *self.common)
        self.assertEqual(rc, 0, out)
        import shlex
        cmd = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("python ")][0]
        argv = [sys.executable] + shlex.split(cmd)[1:] + ["--apply"]
        calls = os.path.join(self.tmp.name, "calls.jsonl")
        e = dict(os.environ, FAKE_CALLS=calls, PYTHONUTF8="1", VRUNNER_IBCONNECTION="/Fрабочая", **(env or {}))
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", env=e)
        with open(calls, encoding="utf-8") as f:
            return r, [json.loads(x) for x in f.read().splitlines()], argv[argv.index("--run-dir") + 1]

    def test_cf_build_run(self):
        self.setup_vr("--user", "Админ", "--password-env", "ONEC_PW")
        r, calls, run_dir = self.apply("cf-build", "--update-db", env={"ONEC_PW": "пароль 1"})
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual([" ".join(c["argv"][1:3]) for c in calls],
                         ["cf load", "validate syntax-check", "infobase update", "cf unload"])
        self.assertTrue(all(c["pwd"] == "пароль 1" and c["user"] == "Админ" for c in calls))
        self.assertTrue(all(c["stray"] is None for c in calls))            # чужое подключение из окружения убрано
        self.assertTrue(all("пароль 1" not in " ".join(c["argv"]) for c in calls))
        self.assertNotIn("пароль 1", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "Альфа.cf")))
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "load-cf.stdout.log")))

    def test_epf_and_yaxunit_run(self):
        self.setup_vr()
        r, calls, run_dir = self.apply("epf-build")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(sorted(os.listdir(os.path.join(run_dir, "out"))), ["Загрузка.epf", "Продажи.erf"])
        r, calls, run_dir = self.apply("yaxunit", "--extension", "tests")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads(r.stdout)["steps"][0]["evidence"][0]["executed"], 1)


class PipelineEnvTokenTest(unittest.TestCase):
    """${env:ИМЯ} подставляется только при запуске и не попадает в план и отчёт."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = os.path.join(self.tmp.name, "run")
        code = "import sys; open(sys.argv[1], 'w', encoding='utf-8').write(sys.argv[2])"
        self.profile = os.path.join(self.tmp.name, "p.json")
        with open(self.profile, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "steps": [{
                "id": "s", "kind": "run", "cwd": "${run}",
                "command": [sys.executable, "-c", code, "${run}/got.txt", "${env:ONEC_TEST_SECRET}"],
                "outputs": [{"path": "${run}/got.txt", "kind": "file"}]}]}, f)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, env, *extra):
        e = dict(os.environ, **env)
        e.pop("ONEC_TEST_SECRET", None) if "ONEC_TEST_SECRET" not in env else None
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "pipeline.py"), "--config", self.profile,
                               "--run-dir", self.run_dir, *extra], capture_output=True, text=True, encoding="utf-8", env=e)

    def test_plan_keeps_token(self):
        r = self.call({"ONEC_TEST_SECRET": "s3cret-значение"})
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("${env:ONEC_TEST_SECRET}", r.stdout)
        self.assertNotIn("s3cret", r.stdout)

    def test_apply_substitutes_and_hides(self):
        r = self.call({"ONEC_TEST_SECRET": "s3cret-значение"}, "--apply")
        self.assertEqual(r.returncode, 0, r.stdout)
        with open(os.path.join(self.run_dir, "got.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "s3cret-значение")
        self.assertNotIn("s3cret", r.stdout)
        with open(os.path.join(self.run_dir, "pipeline-report.json"), encoding="utf-8") as f:
            self.assertNotIn("s3cret", f.read())

    def test_missing_variable_stops_before_run(self):
        r = self.call({}, "--apply")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ONEC_TEST_SECRET", r.stdout)
        self.assertFalse(os.path.exists(self.run_dir))


if __name__ == "__main__":
    unittest.main()
