#!/usr/bin/env python3
"""Окружение 1С для проверки и сборки: что установлено, какая база тестовая, готовые профили.

Команды:
  discover                     установленные платформы, базы из списка 1С, инструменты, источники проекта
  setup --base ИМЯ | --base-file ПУТЬ | --base-server СЕРВЕР/БАЗА
        [--platform ВЕРСИЯ|ПУТЬ] [--user ИМЯ] [--password-env ПЕРЕМЕННАЯ]
        [--runner 1cv8|vrunner] [--vrunner ПУТЬ-К-main.os] [--oscript ПУТЬ]
                               записать выбор в <проект>/.onec/env.json
  profile ВИД [--extension ИМЯ] [--object ИМЯ] [--update-db] [--list-file ФАЙЛ]
              [--runner 1cv8|vrunner] [--increment]
                               создать профиль для pipeline.py в <проект>/.onec/pipelines/
        исполнитель: 1cv8 — платформа напрямую (по умолчанию), vrunner — vanessa-runner 3.x
        (нужны OneScript и vanessa-runner; --increment — его инкрементальная загрузка)
        виды: cf-check   загрузка выгрузки в тестовую базу и проверка Конфигуратором
              cf-build   то же и выгрузка .cf
              cfe-build  загрузка, проверка и выгрузка .cfe одного расширения
              epf-build  сборка .epf/.erf из выгрузок внешних обработок и отчётов
              yaxunit    запуск тестов YAxUnit в тестовой базе
  changed --root КОРЕНЬ (--git [РЕВИЗИЯ] | --newer-than ФАЙЛ) [--out ФАЙЛ]
                               список изменённых файлов для частичной загрузки
  log ФАЙЛ [--tail N]          показать конец журнала платформы (кодировка определяется)

Общие ключи: --project КАТАЛОГ (по умолчанию текущий), --platform-dir КАТАЛОГ, --ibases ФАЙЛ.

Скрипт только ищет и записывает настройки: найденные программы не запускает, в базы не пишет.
Тестовую базу сам не выбирает. Пароли не хранит: в профиль попадает имя переменной окружения.
Python 3.10+, только стандартная библиотека.
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import onec_index  # noqa: E402
import onec_paths  # noqa: E402

VER_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
# Версия формата выгрузки -> версия платформы, которая её пишет.
FORMAT_PLATFORM = {"2.16": "8.3.23", "2.17": "8.3.24", "2.18": "8.3.25", "2.19": "8.3.26",
                   "2.20": "8.3.27", "2.21": "8.5.1"}
TEST_MARKS = ("test", "тест", "copy", "копи", "dev", "demo", "демо", "разраб", "sandbox", "песочниц")
KINDS = ("cf-check", "cf-build", "cfe-build", "epf-build", "yaxunit")
SKIP_DIRS = {".onec", ".git", "build", "node_modules", "__pycache__"}
SEP = os.sep


def out(text=""):
    print(text)


def fail(text, code=2):
    print(text, file=sys.stderr)
    return code


# --------------------------------------------------------------------------------------
# Чтение файлов 1С
# --------------------------------------------------------------------------------------
def decode_any(raw):
    """Журналы и списки 1С бывают в UTF-8, UTF-16 и системной кодировке Windows."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", "replace")


def read_any(path, limit=8 * 1024 * 1024):
    try:
        with open(path, "rb") as f:
            return decode_any(f.read(limit))
    except OSError:
        return ""


def ver_key(v):
    return tuple(int(x) for x in v.split("."))


# --------------------------------------------------------------------------------------
# Поиск платформ, баз и инструментов
# --------------------------------------------------------------------------------------
def estart_dirs():
    """Каталоги настроек стартера 1С для текущего пользователя и общие."""
    dirs = []
    if os.name == "nt":
        for var in ("APPDATA", "ALLUSERSPROFILE"):
            v = os.environ.get(var)
            if v:
                dirs.append(os.path.join(v, "1C", "1CEStart"))
    else:
        dirs.append(os.path.join(os.path.expanduser("~"), ".1C", "1cestart"))
    return dirs


def estart_values(key, dirs=None):
    """Значения ключа из 1CEStart.cfg (InstalledLocation, CommonInfoBases)."""
    vals = []
    for d in (estart_dirs() if dirs is None else dirs):
        # на Windows оба имени указывают на один файл: читаем первый найденный
        cfg = next((p for p in (os.path.join(d, n) for n in ("1CEStart.cfg", "1cestart.cfg")) if os.path.isfile(p)), None)
        for line in read_any(cfg).splitlines() if cfg else ():
            k, _, v = line.partition("=")
            if k.strip().lower() == key.lower() and v.strip() and v.strip() not in vals:
                vals.append(v.strip())
    return vals


def platform_roots(extra=None, estart=None):
    roots = list(extra or [])
    env = os.environ.get("ONEC_PLATFORM_DIRS")
    if env:
        roots += [p for p in env.split(os.pathsep) if p]
    if os.name == "nt":
        for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            v = os.environ.get(var)
            if v:
                roots.append(os.path.join(v, "1cv8"))
    else:
        roots += ["/opt/1cv8/x86_64", "/opt/1cv8/aarch64", "/opt/1cv8/i386", "/opt/1cv8"]
    roots += estart_values("InstalledLocation", estart)
    seen, res = set(), []
    for r in roots:
        k = os.path.normcase(os.path.abspath(r))
        if k not in seen:
            seen.add(k)
            res.append(os.path.abspath(r))
    return res


def find_platforms(roots):
    """-> [{version, exe, client, ibcmd, root}] по убыванию версии."""
    found, seen = [], set()
    for root in roots:
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for d in names:
            if not VER_RE.match(d):
                continue
            for bindir in (os.path.join(root, d, "bin"), os.path.join(root, d)):
                exe = _first_file(bindir, ("1cv8.exe", "1cv8"))
                if not exe or os.path.normcase(exe) in seen:
                    continue
                seen.add(os.path.normcase(exe))
                found.append({"version": d, "exe": exe, "root": root,
                              "client": _first_file(bindir, ("1cv8c.exe", "1cv8c")),
                              "ibcmd": _first_file(bindir, ("ibcmd.exe", "ibcmd"))})
                break
    found.sort(key=lambda p: ver_key(p["version"]), reverse=True)
    return found


def _first_file(d, names):
    for n in names:
        p = os.path.join(d, n)
        if os.path.isfile(p):
            return p
    return None


def ibases_files(extra=None, estart=None):
    files = list(extra or [])
    env = os.environ.get("ONEC_IBASES")
    if env:
        files += [p for p in env.split(os.pathsep) if p]
    for d in (estart_dirs() if estart is None else estart):
        files.append(os.path.join(d, "ibases.v8i"))
    files += estart_values("CommonInfoBases", estart)
    seen, res = set(), []
    for f in files:
        k = os.path.normcase(os.path.abspath(f))
        if k not in seen and os.path.isfile(f):
            seen.add(k)
            res.append(f)
    return res


def parse_v8i(text):
    """Список баз из ibases.v8i. Папки списка (без Connect) пропускаются."""
    items, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            cur = {"name": line[1:-1]}
            items.append(cur)
        elif cur is not None and "=" in line:
            k, _, v = line.partition("=")
            cur[k.strip().lower()] = v.strip()
    bases = []
    for it in items:
        conn = it.get("connect", "")

        def part(key, conn=conn):
            m = re.search(r'(?i)(?:^|;)\s*%s\s*=\s*"((?:[^"]|"")*)"' % key, conn)
            return m.group(1).replace('""', '"') if m else None

        base = {"name": it["name"], "version": it.get("version", ""), "folder": it.get("folder", "")}
        if part("File") is not None:
            base.update(kind="file", path=part("File"))
        elif part("Srvr") is not None:
            base.update(kind="server", server=part("Srvr"), ref=part("Ref") or "")
        elif part("ws") is not None:
            base.update(kind="web", url=part("ws"))
        else:
            continue
        bases.append(base)
    return bases


def find_bases(files):
    bases, seen = [], set()
    for f in files:
        for b in parse_v8i(read_any(f)):
            key = (b["name"].lower(), base_target(b).lower())
            if key not in seen:
                seen.add(key)
                bases.append(b)
    return bases


def base_target(b):
    if b["kind"] == "file":
        return b["path"]
    if b["kind"] == "server":
        return "%s\\%s" % (b["server"], b["ref"])
    return b.get("url", "")


def looks_test(b):
    text = (b.get("name", "") + " " + base_target(b)).lower()
    return any(m in text for m in TEST_MARKS)


def find_tools():
    return {name: shutil.which(name) for name in ("oscript", "vrunner", "git", "java")}


def find_oscript(explicit=None):
    """Исполняемый файл OneScript: явный путь, PATH, стандартные каталоги установки."""
    if explicit:
        return os.path.abspath(explicit) if os.path.isfile(explicit) else None
    found = shutil.which("oscript")
    if found:
        return found
    if os.name == "nt":
        for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            v = os.environ.get(var)
            p = os.path.join(v, "OneScript", "bin", "oscript.exe") if v else None
            if p and os.path.isfile(p):
                return p
    return None


def vrunner_version(main_os):
    """Версия vanessa-runner из packagedef пакета (.Версия("3.0.2")) или ''."""
    pkg = os.path.join(os.path.dirname(os.path.dirname(main_os)), "packagedef")
    m = re.search(r'\.Версия\("([^"]+)"\)', read_any(pkg, 1 << 20))
    return m.group(1) if m else ""


def find_vrunner(project, oscript=None, explicit=None):
    """Точка входа vanessa-runner (src/main.os) — её запускает oscript, без .bat-обёртки.

    Порядок: явный путь; локальная установка проекта (opm install -l: oscript_modules);
    глобальная установка рядом с OneScript (lib/vanessa-runner); каталог рядом с vrunner из PATH.
    """
    cands = []
    if explicit:
        cands.append(explicit)
    cands.append(os.path.join(project, "oscript_modules", "vanessa-runner", "src", "main.os"))
    for exe in (oscript, shutil.which("vrunner")):
        if exe:
            root = os.path.dirname(os.path.dirname(os.path.abspath(exe)))   # <OneScript>/bin/oscript -> <OneScript>
            cands.append(os.path.join(root, "lib", "vanessa-runner", "src", "main.os"))
    for c in cands:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


# --------------------------------------------------------------------------------------
# Источники проекта
# --------------------------------------------------------------------------------------
def project_of(start):
    start = os.path.abspath(start or os.getcwd())
    root = onec_paths.find_root(start)
    if root and onec_paths.is_extension(root):
        root = None
    return (onec_paths.project_dir(root) if root else start), root


def _index_extras(root):
    """Каталоги, подключённые к индексу через build --with (если индекс есть)."""
    if not root:
        return []
    db = onec_paths.default_db(root)
    if not os.path.isfile(db):
        return []
    try:
        conn = sqlite3.connect(db)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='extras'").fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row else []
    except (sqlite3.Error, ValueError):
        return []


def find_sources(project, root, extra_dirs=()):
    """-> {'main': {...}|None, 'extensions': [...], 'external': [...]}"""
    res = {"main": None, "extensions": [], "external": []}
    if root:
        name, _is_ext, _pfx, props = onec_index._config_info(os.path.join(root, "Configuration.xml"))
        res["main"] = {"name": name or "", "dir": root, "props": props}
    scan = [project] + [d for d in list(extra_dirs) + _index_extras(root) if os.path.isdir(d)]
    seen_dirs, seen = set(), set()
    for top in scan:
        top = os.path.abspath(top)
        for dp, dn, fns in os.walk(top):
            key = os.path.normcase(dp)
            if key in seen_dirs or (root and key == os.path.normcase(os.path.abspath(root))):
                dn[:] = []
                continue
            seen_dirs.add(key)
            depth = 0 if dp == top else os.path.relpath(dp, top).count(os.sep) + 1
            if "Configuration.xml" in fns:
                name, is_ext, _pfx, props = onec_index._config_info(os.path.join(dp, "Configuration.xml"))
                if name and is_ext and ("ext", name) not in seen:
                    seen.add(("ext", name))
                    res["extensions"].append({"name": name, "dir": dp, "props": props})
                dn[:] = []
                continue
            for fn in sorted(fns):
                if fn.endswith(".xml"):
                    p = os.path.join(dp, fn)
                    k = onec_index._external_kind(p)
                    if k and os.path.normcase(p) not in seen:
                        seen.add(os.path.normcase(p))
                        res["external"].append({"name": fn[:-4], "kind": k, "xml": p})
            dn[:] = sorted(d for d in dn if depth < 4 and d not in SKIP_DIRS and not d.startswith(".")
                           and not os.path.isfile(os.path.join(dp, d + ".xml")))
    return res


def expected_platform(main):
    """Версия платформы (префикс), соответствующая формату выгрузки, или ''."""
    if not main:
        return ""
    return FORMAT_PLATFORM.get(main["props"].get("format_version", ""), "")


def matching(platforms, prefix):
    return [p for p in platforms if prefix and p["version"].startswith(prefix + ".")]


# --------------------------------------------------------------------------------------
# Настройка проекта
# --------------------------------------------------------------------------------------
def env_path(project):
    return os.path.join(project, ".onec", "env.json")


def load_env(project):
    try:
        with open(env_path(project), encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get("schema") == 1 else None
    except (OSError, ValueError):
        return None


def base_line(b):
    kind = {"file": "файл", "server": "сервер", "web": "веб"}[b["kind"]]
    note = ""
    if b["kind"] == "file" and not os.path.isfile(os.path.join(b["path"], "1Cv8.1CD")):
        note = "  (файл базы не найден)"
    return "  %-34s %-7s %s%s" % (b["name"], kind, base_target(b), note)


def cmd_discover(a):
    project, root = project_of(a.project)
    src = find_sources(project, root, a.with_ or ())
    platforms = find_platforms(platform_roots(a.platform_dir))
    bases = find_bases(ibases_files(a.ibases))
    out("Проект: %s" % project)
    want = expected_platform(src["main"])
    if src["main"]:
        p = src["main"]["props"]
        out("Конфигурация: %s %s — %s" % (src["main"]["name"], p.get("Version", ""), src["main"]["dir"]))
        out("  формат выгрузки %s, совместимость %s, запуск %s" % (
            p.get("format_version") or "?", p.get("CompatibilityMode") or "?", p.get("DefaultRunMode") or "?"))
        out("  платформа по формату выгрузки: %s" % (want or "не определена, выберите версию базы вручную"))
    else:
        out("Конфигурация: выгрузка не найдена (нет Configuration.xml)")
    if src["extensions"]:
        out("Расширения (%d):" % len(src["extensions"]))
        for e in src["extensions"]:
            out("  %s — %s" % (e["name"], e["dir"]))
    if src["external"]:
        out("Внешние обработки и отчёты (%d):" % len(src["external"]))
        for e in src["external"][:30]:
            out("  %s %s — %s" % ("обработка" if e["kind"] == "ExternalDataProcessor" else "отчёт", e["name"], e["xml"]))
        if len(src["external"]) > 30:
            out("  ... ещё %d" % (len(src["external"]) - 30))
    out("Платформы 1С (%d):" % len(platforms))
    for p in platforms:
        mark = "  <- по формату выгрузки" if want and p["version"].startswith(want + ".") else ""
        out("  %-14s %s%s" % (p["version"], p["exe"], mark))
    if not platforms:
        out("  не найдены. Нестандартный каталог: --platform-dir <каталог с папками версий>")
    out("Базы из списка 1С (%d):" % len(bases))
    for b in bases[:60]:
        out(base_line(b))
    if len(bases) > 60:
        out("  ... ещё %d" % (len(bases) - 60))
    tools = find_tools()
    osc = find_oscript()
    vr = find_vrunner(project, osc)
    vr_text = ("есть, %s%s" % (vrunner_version(vr) or "версия не определена", "" if osc else ", но нет OneScript")) if vr else "нет"
    try:
        import lxml.etree  # noqa: F401
        lx = "есть"
    except ImportError:
        lx = "нет (нужна генераторам onec_gen.py: python -m pip install lxml)"
    out("Инструменты: OneScript — %s; vanessa-runner — %s; Git — %s; Java — %s; lxml — %s" % (
        "есть" if osc else "нет", vr_text, "есть" if tools["git"] else "нет", "есть" if tools["java"] else "нет", lx))
    if vr:
        out("  vanessa-runner: %s (профили с --runner vrunner)" % vr)
    env = load_env(project)
    if env:
        out("Настройка проекта: платформа %s, тестовая база «%s» (%s)%s" % (
            env["platform"]["version"], env["test_base"].get("name") or "", base_target(env["test_base"]),
            ", пользователь %s" % env["user"] if env.get("user") else ""))
        if env.get("runner") == "vrunner":
            out("Исполнитель по умолчанию: vanessa-runner %s" % (env.get("vrunner", {}).get("version") or ""))
        out("Дальше: profile cf-check | cf-build | cfe-build | epf-build | yaxunit")
    else:
        out("Настройка проекта: не задана.")
        out("Дальше: спросить у пользователя, какая база тестовая, и выполнить")
        out("  setup --base \"<имя из списка>\"   (или --base-file <каталог> / --base-server <сервер>/<база>)")
    return 0


def pick_platform(platforms, spec, want):
    if spec:
        if os.path.isfile(spec):
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)", spec)
            exe = os.path.abspath(spec)
            d = os.path.dirname(exe)
            return {"version": m.group(1) if m else "", "exe": exe,
                    "client": _first_file(d, ("1cv8c.exe", "1cv8c")), "ibcmd": _first_file(d, ("ibcmd.exe", "ibcmd"))}, None
        hits = [p for p in platforms if p["version"] == spec] or [p for p in platforms if p["version"].startswith(spec + ".")]
        if len(hits) == 1:
            return hits[0], None
        if not hits:
            return None, "Платформа %s не найдена среди установленных." % spec
        return None, "Версии %s соответствует несколько сборок: %s. Укажите сборку полностью." % (
            spec, ", ".join(p["version"] for p in hits))
    hits = matching(platforms, want)
    if len(hits) == 1:
        return hits[0], None
    if not platforms:
        return None, "Платформы 1С не найдены. Укажите --platform <путь к 1cv8> или --platform-dir."
    if not hits:
        return None, ("По формату выгрузки платформа не определяется однозначно. Укажите --platform <версия>: "
                      + ", ".join(p["version"] for p in platforms))
    return None, "Подходит несколько сборок: %s. Укажите --platform <версия>." % ", ".join(p["version"] for p in hits)


def cmd_setup(a):
    project, root = project_of(a.project)
    src = find_sources(project, root)
    platforms = find_platforms(platform_roots(a.platform_dir))
    plat, err = pick_platform(platforms, a.platform, expected_platform(src["main"]))
    if err:
        return fail(err)
    given = [x for x in (a.base, a.base_file, a.base_server) if x]
    if len(given) != 1:
        return fail("Укажите одну тестовую базу: --base ИМЯ, --base-file КАТАЛОГ или --base-server СЕРВЕР/БАЗА.\n"
                    "Скрипт базу не выбирает: её называет пользователь.")
    if a.base:
        bases = find_bases(ibases_files(a.ibases))
        hits = [b for b in bases if b["name"].lower() == a.base.lower()]
        if len(hits) != 1:
            return fail("База «%s» в списке 1С %s. Доступные:\n%s" % (
                a.base, "не найдена" if not hits else "встречается несколько раз",
                "\n".join(base_line(b) for b in bases) or "  (список пуст)"))
        base = dict(hits[0])
        if base["kind"] == "web":
            return fail("База опубликована на веб-сервере: Конфигуратор к ней в пакетном режиме не подключается.")
    elif a.base_file:
        base = {"name": os.path.basename(os.path.normpath(a.base_file)), "kind": "file", "path": os.path.abspath(a.base_file)}
    else:
        server, _, ref = a.base_server.replace("\\", "/").partition("/")
        if not server or not ref:
            return fail("Формат: --base-server СЕРВЕР/БАЗА")
        base = {"name": ref, "kind": "server", "server": server, "ref": ref}
    if base["kind"] == "file" and not os.path.isfile(os.path.join(base["path"], "1Cv8.1CD")):
        return fail("В каталоге %s нет файла базы 1Cv8.1CD." % base["path"])
    if a.password_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", a.password_env):
        return fail("Имя переменной окружения: латинские буквы, цифры и подчёркивание.")
    osc = find_oscript(a.oscript)
    vr = find_vrunner(project, osc, a.vrunner)
    if a.runner == "vrunner" and not (osc and vr):
        return fail("Для --runner vrunner нужны OneScript и vanessa-runner: %s. Установка — руководство, раздел 8;\n"
                    "нестандартное место — --oscript <путь к oscript> и --vrunner <путь к src/main.os>."
                    % ", ".join(n for n, ok in (("нет OneScript", osc), ("нет vanessa-runner", vr)) if not ok))
    env = {"schema": 1,
           "platform": {"version": plat["version"], "exe": plat["exe"], "client": plat.get("client") or ""},
           "test_base": {k: base[k] for k in ("name", "kind", "path", "server", "ref") if base.get(k)}}
    if a.user:
        env["user"] = a.user
    if a.password_env:
        env["password_env"] = a.password_env
    if osc and vr:
        env["vrunner"] = {"oscript": osc, "main": vr, "version": vrunner_version(vr)}
    if a.runner == "vrunner":
        env["runner"] = "vrunner"
    os.makedirs(os.path.dirname(env_path(project)), exist_ok=True)
    with open(env_path(project), "w", encoding="utf-8") as f:
        json.dump(env, f, ensure_ascii=False, indent=2)
        f.write("\n")
    out("Записано: %s" % env_path(project))
    out("Платформа: %s — %s" % (plat["version"] or "?", plat["exe"]))
    out("Тестовая база: «%s» — %s" % (base["name"], base_target(base)))
    if not looks_test(base):
        out("ВНИМАНИЕ: по имени и пути не видно, что база тестовая. Загрузка заменяет конфигурацию базы —")
        out("убедитесь у пользователя, что это копия, а не рабочая база.")
    if a.user:
        out("Пользователь: %s; пароль: %s" % (a.user, "из переменной окружения %s" % a.password_env
                                              if a.password_env else "пустой"))
    if env.get("vrunner"):
        out("vanessa-runner %s: %s%s" % (env["vrunner"]["version"] or "", env["vrunner"]["main"],
                                          " — исполнитель по умолчанию" if env.get("runner") == "vrunner" else ""))
    out("Строка для CLAUDE.md проекта: | Тестовая база | %s (%s), платформа %s |" % (
        base["name"], base_target(base), plat["version"]))
    out("Дальше: profile cf-check (загрузка и проверка) или profile cf-build (ещё и .cf)")
    return 0


# --------------------------------------------------------------------------------------
# Профили
# --------------------------------------------------------------------------------------
def conn_args(env):
    b = env["test_base"]
    args = ["/F", b["path"]] if b["kind"] == "file" else ["/S", "%s\\%s" % (b["server"], b["ref"])]
    if env.get("user"):
        args += ["/N", env["user"]]
        if env.get("password_env"):
            args += ["/P", "${env:%s}" % env["password_env"]]
    return args


def run_file(name):
    return "${run}" + SEP + name


def designer(env, sid, kind, args, timeout, inputs=None, outputs=None):
    step = {"id": sid, "kind": kind,
            "command": ["${onec}", "DESIGNER"] + conn_args(env) + ["/DisableStartupDialogs", "/DisableStartupMessages"]
            + args + ["/Out", run_file(sid + ".log"), "/DumpResult", run_file(sid + ".code")],
            "cwd": "${run}", "timeout": timeout,
            "outputs": [{"path": run_file(sid + ".code"), "kind": "exitcode"}] + (outputs or [])}
    if inputs:
        step["inputs"] = inputs
    return step


def check_modes(main, override):
    if override:
        return ["-" + m.lstrip("-") for m in override]
    mode = (main or {}).get("props", {}).get("DefaultRunMode", "")
    return ["-ThickClientOrdinaryApplication", "-Server"] if mode == "OrdinaryApplication" else ["-ThinClient", "-Server"]


def safe_name(name):
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name).strip("_") or "out"


def build_profile(kind, env, src, a):
    t = a.timeout
    steps, prepare, variables = [], [], {"onec": env["platform"]["exe"]}
    modes = check_modes(src["main"], a.check_mode)
    title = kind
    if kind in ("cf-check", "cf-build"):
        if not src["main"]:
            raise ValueError("Не найдена выгрузка основной конфигурации (Configuration.xml).")
        variables["cf"] = src["main"]["dir"]
        load = ["/LoadConfigFromFiles", "${cf}"]
        inputs = ["${cf}" + SEP + "Configuration.xml"]
        if a.list_file:
            lf = os.path.abspath(a.list_file)
            if not os.path.isfile(lf):
                raise ValueError("Нет файла списка: %s" % lf)
            load += ["-listFile", lf, "-Format", "Hierarchical", "-partial"]
            inputs.append(lf)
        steps.append(designer(env, "load-cf", "deploy", load, t, inputs))
        steps.append(designer(env, "check-cf", "check", ["/CheckConfig", "-ConfigLogIntegrity", "-IncorrectReferences"] + modes, t))
        if a.update_db:
            steps.append(designer(env, "update-db", "deploy", ["/UpdateDBCfg"], t))
        if kind == "cf-build":
            cf_file = run_file(safe_name(src["main"]["name"] or "Configuration") + ".cf")
            steps.append(designer(env, "dump-cf", "build", ["/DumpCfg", cf_file], t,
                                  outputs=[{"path": cf_file, "kind": "file"}]))
    elif kind == "cfe-build":
        exts = src["extensions"]
        if a.extension:
            exts = [e for e in exts if e["name"].lower() == a.extension.lower()]
        if len(exts) != 1:
            raise ValueError("Укажите расширение: --extension ИМЯ. Найдены: %s" % (
                ", ".join(e["name"] for e in src["extensions"]) or "нет"))
        ext = exts[0]
        title = "cfe-build-" + safe_name(ext["name"])
        variables.update(cfe=ext["dir"], extension=ext["name"])
        steps.append(designer(env, "load-cfe", "deploy", ["/LoadConfigFromFiles", "${cfe}", "-Extension", "${extension}"], t,
                              ["${cfe}" + SEP + "Configuration.xml"]))
        steps.append(designer(env, "check-cfe", "check", ["/CheckConfig", "-ConfigLogIntegrity", "-IncorrectReferences"]
                              + modes + ["-Extension", "${extension}"], t))
        if a.update_db:
            steps.append(designer(env, "update-db", "deploy", ["/UpdateDBCfg", "-Extension", "${extension}"], t))
        cfe_file = run_file(safe_name(ext["name"]) + ".cfe")
        steps.append(designer(env, "dump-cfe", "build", ["/DumpCfg", cfe_file, "-Extension", "${extension}"], t,
                              outputs=[{"path": cfe_file, "kind": "file"}]))
    elif kind == "epf-build":
        objs = src["external"]
        if a.object:
            objs = [o for o in objs if o["name"].lower() == a.object.lower()]
        if not objs:
            raise ValueError("Внешние обработки и отчёты не найдены%s." % (" по имени " + a.object if a.object else ""))
        used = set()
        for n, o in enumerate(objs, 1):
            fname = safe_name(o["name"]) + (".epf" if o["kind"] == "ExternalDataProcessor" else ".erf")
            if fname.lower() in used:
                fname = "%d_%s" % (n, fname)
            used.add(fname.lower())
            target = run_file("out") + SEP + fname
            steps.append(designer(env, "build-%d" % n, "build", ["/LoadExternalDataProcessorOrReportFromFiles", o["xml"], target],
                                  t, [o["xml"]], [{"path": target, "kind": "file"}]))
    elif kind == "yaxunit":
        if not a.extension:
            raise ValueError("Укажите расширение с тестами: --extension ИМЯ")
        variables = {"client": env["platform"].get("client") or env["platform"]["exe"], "tests_extension": a.extension}
        prepare.append({"path": run_file("yaxunit.json"), "json": {
            "filter": {"extensions": ["${tests_extension}"]}, "reportFormat": "jUnit",
            "reportPath": run_file("yaxunit.xml"), "exitCode": run_file("yaxunit.code"),
            "closeAfterTests": True, "showReport": False}})
        steps.append({"id": "yaxunit", "kind": "test",
                      "command": ["${client}", "ENTERPRISE"] + conn_args(env)
                      + ["/DisableStartupDialogs", "/C", "RunUnitTests=" + run_file("yaxunit.json")],
                      "cwd": "${run}", "timeout": t,
                      "outputs": [{"path": run_file("yaxunit.xml"), "kind": "junit"},
                                  {"path": run_file("yaxunit.code"), "kind": "exitcode"}]})
    else:
        raise ValueError("Неизвестный вид профиля: %s" % kind)
    profile = {"schema": 1, "name": title, "variables": variables, "steps": steps}
    if prepare:
        profile["prepare"] = prepare
    return title, profile


def vr_conn(env):
    b = env["test_base"]
    return ["--ibconnection", "/F" + b["path"] if b["kind"] == "file" else "/S%s\\%s" % (b["server"], b["ref"])]


def vr_env(env):
    """Учётные данные vanessa-runner — через его переменные окружения, а не в командной строке."""
    e = {}
    if env.get("user"):
        e["VRUNNER_DBUSER"] = env["user"]
        if env.get("password_env"):
            e["VRUNNER_DBPWD"] = "${env:%s}" % env["password_env"]
    return e


def vrunner_step(env, sid, kind, args, positional, timeout, inputs=None, outputs=None, base=True):
    """Шаг vanessa-runner 3: oscript main.os <команда> [опции] <аргумент>.

    Опции перед позиционным аргументом — так в документации vanessa-runner 3, иначе разбор команды
    может не сработать. cwd — новый каталог прогона: случайный autumn-properties.json проекта
    не подменит базу и версию платформы.
    """
    cmd = ["${oscript}", "${vrunner}"] + args + (vr_conn(env) if base else []) \
        + ["--v8version", env["platform"]["version"]] + ([positional] if positional else [])
    step = {"id": sid, "kind": kind, "command": cmd, "cwd": "${run}", "timeout": timeout,
            "inputs": ["${vrunner}"] + (inputs or []), "outputs": outputs or []}
    e = vr_env(env) if base else {}
    if e:
        step["env"] = e
    return step


def vr_modes(src, override):
    modes = [m.lstrip("-") for m in check_modes(src["main"], override)]
    out_ = []
    for m in ["ConfigLogIntegrity", "IncorrectReferences"] + modes:
        out_ += ["--mode", m]
    return out_


def build_vrunner_profile(kind, env, src, a):
    vr = env.get("vrunner") or {}
    if not (vr.get("oscript") and vr.get("main")):
        raise ValueError("vanessa-runner не настроен: setup ... --runner vrunner (или --vrunner <путь к src/main.os>).")
    t = a.timeout
    variables = {"oscript": vr["oscript"], "vrunner": vr["main"], "onec": env["platform"]["exe"]}
    steps, title = [], kind + "-vrunner"
    if a.increment and a.list_file:
        raise ValueError("--increment и --list-file вместе не используются.")

    def load_opts():
        o = []
        if a.list_file:
            lf = os.path.abspath(a.list_file)
            if not os.path.isfile(lf):
                raise ValueError("Нет файла списка: %s" % lf)
            o += ["--list", lf]
        if a.increment:
            o += ["--increment"]
        return o + ["--no-update-db"]

    def check(sid, target, report):
        return vrunner_step(env, sid, "check", ["validate", "syntax-check", "--target", target] + vr_modes(src, a.check_mode)
                            + ["--report-format", "junit", "--report-path", run_file(report)], None, t,
                            outputs=[{"path": run_file(report), "kind": "junit", "min_tests": 0}])

    if kind in ("cf-check", "cf-build"):
        if not src["main"]:
            raise ValueError("Не найдена выгрузка основной конфигурации (Configuration.xml).")
        variables["cf"] = src["main"]["dir"]
        steps.append(vrunner_step(env, "load-cf", "deploy", ["cf", "load", "--src-format", "xml"] + load_opts(), "${cf}", t,
                                  ["${cf}" + SEP + "Configuration.xml"]))
        steps.append(check("check-cf", "main", "check-cf.xml"))
        if a.update_db:
            steps.append(vrunner_step(env, "update-db", "deploy", ["infobase", "update", "--target", "main"], None, t))
        if kind == "cf-build":
            cf_file = run_file(safe_name(src["main"]["name"] or "Configuration") + ".cf")
            steps.append(vrunner_step(env, "dump-cf", "build", ["cf", "unload"], cf_file, t,
                                      outputs=[{"path": cf_file, "kind": "file"}]))
    elif kind == "cfe-build":
        exts = src["extensions"]
        if a.extension:
            exts = [e for e in exts if e["name"].lower() == a.extension.lower()]
        if len(exts) != 1:
            raise ValueError("Укажите расширение: --extension ИМЯ. Найдены: %s" % (
                ", ".join(e["name"] for e in src["extensions"]) or "нет"))
        ext = exts[0]
        title = "cfe-build-" + safe_name(ext["name"]) + "-vrunner"
        variables.update(cfe=ext["dir"], extension=ext["name"])
        if a.list_file:
            raise ValueError("--list-file для расширения не поддерживается; используйте --increment.")
        steps.append(vrunner_step(env, "load-cfe", "deploy", ["cfe", "load", "--src-format", "xml", "--extension-name",
                                  "${extension}"] + load_opts(), "${cfe}", t, ["${cfe}" + SEP + "Configuration.xml"]))
        steps.append(check("check-cfe", "${extension}", "check-cfe.xml"))
        if a.update_db:
            steps.append(vrunner_step(env, "update-db", "deploy", ["infobase", "update", "--target", "${extension}"], None, t))
        cfe_file = run_file(safe_name(ext["name"]) + ".cfe")
        steps.append(vrunner_step(env, "dump-cfe", "build", ["cfe", "unload", "--extension-name", "${extension}"], cfe_file, t,
                                  outputs=[{"path": cfe_file, "kind": "file"}]))
    elif kind == "epf-build":
        objs = src["external"]
        if a.object:
            objs = [o for o in objs if o["name"].lower() == a.object.lower()]
        if not objs:
            raise ValueError("Внешние обработки и отчёты не найдены%s." % (" по имени " + a.object if a.object else ""))
        per_dir = {}
        for o in src["external"]:
            per_dir.setdefault(os.path.normcase(os.path.dirname(o["xml"])), []).append(o)
        used = set()
        for n, o in enumerate(objs, 1):
            ext_ = ".epf" if o["kind"] == "ExternalDataProcessor" else ".erf"
            fname = os.path.splitext(os.path.basename(o["xml"]))[0] + ext_     # vrunner называет файл по имени XML
            out_dir = run_file("out") + ("" if fname.lower() not in used else SEP + str(n))
            used.add(fname.lower())
            target = out_dir + SEP + fname
            if len(per_dir[os.path.normcase(os.path.dirname(o["xml"]))]) > 1:
                # vanessa-runner собирает из каталога первый найденный объект — для каталога с несколькими
                # объектами собираем этот объект платформой напрямую
                steps.append(designer(env, "build-%d" % n, "build", ["/LoadExternalDataProcessorOrReportFromFiles",
                                      o["xml"], target], t, [o["xml"]], [{"path": target, "kind": "file"}]))
            else:
                # без --ibconnection vanessa-runner собирает во временной базе: тестовая база не нужна
                steps.append(vrunner_step(env, "build-%d" % n, "build", ["epf", "compile", "--src-format", "xml", "--out", out_dir],
                                          os.path.dirname(o["xml"]), t, [o["xml"]], [{"path": target, "kind": "file"}], base=False))
    elif kind == "yaxunit":
        if not a.extension:
            raise ValueError("Укажите расширение с тестами: --extension ИМЯ")
        variables["tests_extension"] = a.extension
        steps.append(vrunner_step(env, "yaxunit", "test", ["test", "yaxunit", "--ext", "${tests_extension}",
                                  "--report-format", "junit", "--report-path", run_file("yaxunit.xml")], None, t,
                                  outputs=[{"path": run_file("yaxunit.xml"), "kind": "junit"}]))
    else:
        raise ValueError("Неизвестный вид профиля: %s" % kind)
    return title, {"schema": 1, "name": title, "variables": variables,
                   "env_unset_prefixes": ["VRUNNER_", "RUNNER_"], "steps": steps}


def next_run_dir(project, title):
    n = 1
    while os.path.exists(os.path.join(project, "build", "%s-%03d" % (title, n))):
        n += 1
    return os.path.join(project, "build", "%s-%03d" % (title, n))


def cmd_profile(a):
    project, root = project_of(a.project)
    env = load_env(project)
    if not env:
        return fail("Нет настройки проекта (%s). Сначала: discover, затем setup --base ..." % env_path(project))
    if not os.path.isfile(env["platform"]["exe"]):
        return fail("Нет файла платформы %s. Повторите setup." % env["platform"]["exe"])
    src = find_sources(project, root, a.with_ or ())
    runner = a.runner or env.get("runner") or "1cv8"
    try:
        if runner == "vrunner":
            title, profile = build_vrunner_profile(a.kind, env, src, a)
        else:
            if a.increment:
                raise ValueError("--increment — только для --runner vrunner; для 1cv8 — changed + --list-file.")
            title, profile = build_profile(a.kind, env, src, a)
    except ValueError as e:
        return fail(str(e))
    path = a.out or os.path.join(project, ".onec", "pipelines", title + ".json")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
        f.write("\n")
    pipeline = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline.py")
    cmd = 'python "%s" --config "%s" --project "%s" --run-dir "%s"' % (pipeline, path, project, next_run_dir(project, title))
    out("Профиль: %s" % path)
    out("Платформа: %s; исполнитель: %s" % (env["platform"]["version"], "vanessa-runner %s" % (
        env.get("vrunner", {}).get("version") or "") if runner == "vrunner" else "1cv8"))
    out("Тестовая база: «%s» — %s" % (env["test_base"].get("name", ""), base_target(env["test_base"])))
    out("Шаги: " + " -> ".join(s["id"] for s in profile["steps"]))
    if a.kind != "yaxunit" and a.kind != "epf-build":
        out("Загрузка заменяет конфигурацию тестовой базы%s." % (
            "; обновление БД меняет её данные" if a.update_db else "; конфигурация БД не обновляется (нужен --update-db)"))
    if env.get("password_env") and not os.environ.get(env["password_env"]):
        out("Пароль: переменная окружения %s не задана — задайте её до запуска." % env["password_env"])
    out("План (ничего не запускает):")
    out("  " + cmd)
    if runner == "vrunner":
        out("Запуск: та же команда с --apply. Журнал шага — <run-dir>%s<шаг>.stdout.log (вывод vanessa-runner),"
            " показать: onec_env.py log <файл>" % SEP)
    else:
        out("Запуск: та же команда с --apply. Журнал шага Конфигуратора — <run-dir>%s<шаг>.log, у тестов —"
            " <шаг>.stdout.log и отчёт JUnit; показать: onec_env.py log <файл>" % SEP)
    return 0


# --------------------------------------------------------------------------------------
# Изменённые файлы и журналы
# --------------------------------------------------------------------------------------
def changed_git(root, rev):
    def git(*args):
        r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", root] + list(args),
                           capture_output=True, timeout=300)
        if r.returncode != 0:
            raise ValueError("git: " + decode_any(r.stderr).strip())
        return [x for x in decode_any(r.stdout).split("\0") if x]

    changed = git("diff", "--name-only", "--relative", "--diff-filter=d", "-z", rev)
    changed += git("ls-files", "--others", "--exclude-standard", "-z")
    deleted = git("diff", "--name-only", "--relative", "--diff-filter=D", "-z", rev)
    return sorted(set(changed)), deleted


def changed_newer(root, ref_file):
    ref = os.stat(ref_file).st_mtime_ns
    res = []
    for dp, dn, fns in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                if os.stat(p).st_mtime_ns > ref:
                    res.append(os.path.relpath(p, root).replace(os.sep, "/"))
            except OSError:
                pass
    return sorted(res), []


def cmd_changed(a):
    root = os.path.abspath(a.root)
    if not os.path.isfile(os.path.join(root, "Configuration.xml")):
        return fail("В %s нет Configuration.xml." % root)
    try:
        if a.newer_than:
            files, deleted = changed_newer(root, a.newer_than)
        else:
            files, deleted = changed_git(root, a.git or "HEAD")
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return fail("Не удалось определить изменения: %s" % e)
    files = [f for f in files if os.path.basename(f) != "ConfigDumpInfo.xml"]
    if deleted:
        out("Удалённых файлов: %d. Частичная загрузка их из базы не уберёт — нужна полная загрузка." % len(deleted))
    if not files:
        out("Изменённых файлов нет, список не записан.")
        return 3
    project, _ = project_of(root)
    target = os.path.abspath(a.out or os.path.join(project, ".onec", "tmp", "changed.txt"))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n".join(files) + "\n")
    out("Изменённых файлов: %d" % len(files))
    for x in files[:20]:
        out("  " + x)
    if len(files) > 20:
        out("  ... ещё %d" % (len(files) - 20))
    out("Список: %s" % target)
    out("Дальше: profile cf-check --list-file \"%s\"" % target)
    return 0


def cmd_log(a):
    try:
        with open(a.file, "rb") as f:
            text = decode_any(f.read())
    except OSError as e:
        return fail("Не удалось прочитать журнал: %s" % e)
    lines = text.splitlines()
    if not lines:
        out("(журнал пуст)")
        return 0
    if len(lines) > a.tail:
        out("... пропущено строк: %d" % (len(lines) - a.tail))
    for line in lines[-a.tail:]:
        out(line)
    return 0


# --------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, sources=True):
        p.add_argument("--project", help="папка проекта (по умолчанию текущая)")
        p.add_argument("--platform-dir", action="append", metavar="КАТАЛОГ", help="каталог с папками версий 1С")
        p.add_argument("--ibases", action="append", metavar="ФАЙЛ", help="дополнительный файл ibases.v8i")
        if sources:
            p.add_argument("--with", dest="with_", action="append", metavar="КАТАЛОГ",
                           help="каталог расширений или внешних обработок вне папки проекта")

    common(sub.add_parser("discover"))
    p = sub.add_parser("setup")
    common(p, sources=False)
    p.add_argument("--base", help="имя базы из списка 1С")
    p.add_argument("--base-file", help="каталог файловой базы")
    p.add_argument("--base-server", help="СЕРВЕР/БАЗА")
    p.add_argument("--platform", help="версия (8.3.27.1859 или 8.3.27) или путь к 1cv8")
    p.add_argument("--user", help="пользователь тестовой базы")
    p.add_argument("--password-env", help="имя переменной окружения с паролем (сам пароль не хранится)")
    p.add_argument("--runner", choices=("1cv8", "vrunner"), default="1cv8",
                   help="исполнитель профилей по умолчанию (vrunner — vanessa-runner 3.x)")
    p.add_argument("--vrunner", help="путь к src/main.os vanessa-runner, если он не найден сам")
    p.add_argument("--oscript", help="путь к oscript, если он не найден сам")
    p = sub.add_parser("profile")
    common(p)
    p.add_argument("kind", choices=KINDS)
    p.add_argument("--extension", help="имя расширения (cfe-build, yaxunit)")
    p.add_argument("--object", help="имя файла внешней обработки или отчёта (epf-build)")
    p.add_argument("--update-db", action="store_true", help="добавить обновление конфигурации БД")
    p.add_argument("--list-file", help="список файлов для частичной загрузки (см. changed)")
    p.add_argument("--runner", choices=("1cv8", "vrunner"), help="исполнитель: 1cv8 или vanessa-runner (по умолчанию — из setup)")
    p.add_argument("--increment", action="store_true", help="vrunner: загрузить только изменённое (индекс vanessa-runner)")
    p.add_argument("--check-mode", action="append", metavar="РЕЖИМ", help="режим /CheckConfig вместо определённых по конфигурации")
    p.add_argument("--timeout", type=int, default=7200, help="предел шага в секундах (по умолчанию 7200)")
    p.add_argument("--out", help="файл профиля (по умолчанию .onec/pipelines/<вид>.json)")
    p = sub.add_parser("changed")
    p.add_argument("--root", required=True, help="корень выгрузки")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--git", nargs="?", const="HEAD", metavar="РЕВИЗИЯ", help="изменения относительно ревизии Git (HEAD)")
    g.add_argument("--newer-than", metavar="ФАЙЛ", help="файлы новее указанного файла")
    p.add_argument("--out", help="куда записать список (по умолчанию .onec/tmp/changed.txt)")
    p = sub.add_parser("log")
    p.add_argument("file")
    p.add_argument("--tail", type=int, default=40)
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return {"discover": cmd_discover, "setup": cmd_setup, "profile": cmd_profile,
            "changed": cmd_changed, "log": cmd_log}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
