#!/usr/bin/env python3
"""Генераторы и редакторы исходников 1С: создание и правка объектов, форм, СКД, ролей, макетов,
подсистем, XDTO, расширений (заимствование, перехватчики), внешних обработок и отчётов.

Использование:
  onec_gen.py list                              команды и что они делают
  onec_gen.py <область> <действие> [параметры движка] [--dry-run]
  onec_gen.py undo [ЗАПУСК] [--project КАТАЛОГ]  отменить запись (по умолчанию — последнюю)
  onec_gen.py doctor                            проверить Python и lxml, целостность движка

Параметры после команды передаются движку как есть (-ObjectPath, -Operation, -Value, -JsonPath ...);
справка по каждой команде — references/gen/<область>-<действие>.md.

Команды, которые меняют файлы:
  - движок работает под охраной записи: писать можно только в каталог, который меняет команда
    (выгрузка, расширение, внешний объект, каталог нового файла); исходное состояние каждого
    файла сохраняется перед первой его правкой в <проект>/.onec/gen/<запуск>/ — при любом размере
    выгрузки; после записи печатается, что изменено, добавлено и удалено; `undo` возвращает как было;
  - `undo` сначала проверяет всё (файлы не менялись после запуска, удалённые не созданы заново,
    резервная копия цела) и только потом пишет; при любом расхождении ничего не трогает;
  - `--dry-run` выполняет команду на временной копии; к настоящим файлам у движка доступа на запись нет;
  - ConfigDumpInfo.xml не меняют: если движок его тронул, файл восстанавливается.

Движок — Python-версии открытого проекта (лицензия MIT, см. engine/LICENSE и NOTICE.md),
закреплённые в engine/manifest.json. Ему нужна библиотека lxml: python -m pip install lxml.
Python 3.10+.
"""
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import onec_paths  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_DIR = os.path.join(SKILL_DIR, "engine")
MANIFEST = os.path.join(ENGINE_DIR, "manifest.json")
GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "onec_gen_guard.py")
OUT_LIMIT = 150

# Наша команда -> (движок, вид, параметр с изменяемым путём, описание).
# Вид: read — только чтение; output — пишет новый файл (-OutputPath и т.п.); write — меняет исходники;
#      new — создаёт новый каталог проекта (конфигурация, расширение, обработка).
COMMANDS = {
    "object new":       ("meta-compile",      "write",  "-OutputDir",     "Новый объект метаданных по описанию JSON и регистрация в конфигурации"),
    "object edit":      ("meta-edit",         "write",  "-ObjectPath",    "Правка объекта: реквизиты, табличные части, измерения, ресурсы, свойства"),
    "object info":      ("meta-info",         "read",   None,             "Сводка объекта без чтения XML целиком"),
    "object check":     ("meta-validate",     "read",   None,             "Проверка XML объекта"),
    "object draft":     ("meta-decompile",    "output", "-OutputPath",    "JSON-описание существующего объекта (образец для object new)"),
    "object remove":    ("meta-remove",       "write",  "-ConfigDir",     "Удалить объект и ссылки на него из конфигурации"),
    "config new":       ("cf-init",           "new",    "-OutputDir",     "Каркас новой конфигурации"),
    "config edit":      ("cf-edit",           "write",  "-ConfigPath",    "Правка свойств и состава Configuration.xml"),
    "config info":      ("cf-info",           "read",   None,             "Состав и свойства конфигурации"),
    "config check":     ("cf-validate",       "read",   None,             "Проверка Configuration.xml"),
    "ext new":          ("cfe-init",          "new",    "-OutputDir",     "Каркас нового расширения"),
    "ext borrow":       ("cfe-borrow",        "write",  "-ExtensionPath", "Заимствовать объекты основной конфигурации в расширение"),
    "ext intercept":    ("cfe-patch-method",  "write",  "-ExtensionPath", "Перехватчик метода: &Перед, &После, &ИзменениеИКонтроль, &Вместо"),
    "ext diff":         ("cfe-diff",          "read",   None,             "Состав расширения и сверка перехватчиков с основной конфигурацией"),
    "ext check":        ("cfe-validate",      "read",   None,             "Проверка расширения"),
    "epf new":          ("epf-init",          "new",    "-SrcDir",        "Каркас внешней обработки"),
    "epf check":        ("epf-validate",      "read",   None,             "Проверка внешней обработки или отчёта"),
    "erf new":          ("erf-init",          "new",    "-SrcDir",        "Каркас внешнего отчёта (с СКД — -WithSKD)"),
    "erf check":        ("epf-validate",      "read",   None,             "Проверка внешнего отчёта"),
    "form new":         ("form-add",          "write",  "-ObjectPath",    "Добавить управляемую форму объекту (пустую)"),
    "form build":       ("form-compile",      "output", "-OutputPath",    "Содержимое управляемой формы (Form.xml) по описанию JSON"),
    "form edit":        ("form-edit",         "write",  "-FormPath",      "Добавить элементы, реквизиты, команды в существующую форму"),
    "form info":        ("form-info",         "read",   None,             "Структура управляемой формы"),
    "form check":       ("form-validate",     "read",   None,             "Проверка Form.xml"),
    "form draft":       ("form-decompile",    "output", "-OutputPath",    "JSON-описание существующей формы"),
    "form remove":      ("form-remove",       "write",  "-SrcDir",        "Удалить форму объекта"),
    "dcs build":        ("skd-compile",       "output", "-OutputPath",    "Схема компоновки данных по описанию"),
    "dcs edit":         ("skd-edit",          "write",  "-TemplatePath",  "Правка СКД: наборы, поля, параметры, варианты"),
    "dcs info":         ("skd-info",          "read",   None,             "Сводка СКД"),
    "dcs check":        ("skd-validate",      "read",   None,             "Проверка СКД"),
    "dcs draft":        ("skd-decompile",     "output", "-OutputPath",    "JSON-описание существующей СКД"),
    "role new":         ("role-compile",      "write",  "-OutputDir",     "Новая роль по описанию"),
    "role edit":        ("role-edit",         "write",  "-RolePath",      "Правка прав роли"),
    "role info":        ("role-info",         "read",   None,             "Сводка прав роли"),
    "role check":       ("role-validate",     "read",   None,             "Проверка роли"),
    "sheet build":      ("mxl-compile",       "output", "-OutputPath",    "Табличный документ (макет печати) по описанию"),
    "sheet info":       ("mxl-info",          "read",   None,             "Структура табличного макета"),
    "sheet check":      ("mxl-validate",      "read",   None,             "Проверка табличного макета"),
    "sheet draft":      ("mxl-decompile",     "output", "-OutputPath",    "JSON-описание существующего макета"),
    "template add":     ("template-add",      "write",  "-SrcDir",        "Добавить макет объекту"),
    "template remove":  ("template-remove",   "write",  "-SrcDir",        "Удалить макет объекта"),
    "subsystem new":    ("subsystem-compile", "write",  "-OutputDir",     "Новая подсистема"),
    "subsystem edit":   ("subsystem-edit",    "write",  "-SubsystemPath", "Состав и свойства подсистемы"),
    "subsystem info":   ("subsystem-info",    "read",   None,             "Сводка подсистемы"),
    "subsystem check":  ("subsystem-validate", "read",  None,             "Проверка подсистемы"),
    "cmdui edit":       ("interface-edit",    "write",  "-CIPath",        "Командный интерфейс подсистемы или конфигурации"),
    "cmdui check":      ("interface-validate", "read",  None,             "Проверка командного интерфейса"),
    "xdto new":         ("xdto-compile",      "write",  "-OutputDir",     "Пакет XDTO из XSD"),
    "xdto edit":        ("xdto-edit",         "write",  "-PackagePath",   "Правка пакета XDTO"),
    "xdto info":        ("xdto-info",         "read",   None,             "Сводка пакета XDTO"),
    "xdto check":       ("xdto-validate",     "read",   None,             "Проверка пакета XDTO"),
    "xdto draft":       ("xdto-decompile",    "output", "-OutFile",       "XSD из пакета XDTO"),
    "help add":         ("help-add",          "write",  "-SrcDir",        "Встроенная справка объекта"),
    "support set":      ("support-edit",      "write",  "-Path",          "Режим поддержки объекта (на поддержке / снят)"),
}
ENGINES = sorted({v[0] for v in COMMANDS.values()})


def out(text=""):
    print(text)


def fail(text, code=2):
    print("ОШИБКА: " + text, file=sys.stderr)
    return code


# --------------------------------------------------------------------------------------
# Движок
# --------------------------------------------------------------------------------------
def load_manifest():
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def engine_script(name):
    m = load_manifest() or {}
    rel = m.get("entries", {}).get(name, "%s/scripts/%s.py" % (name, name))
    return os.path.join(ENGINE_DIR, *rel.split("/"))


def has_lxml():
    try:
        import lxml.etree  # noqa: F401
        return True
    except ImportError:
        return False


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_doctor():
    m = load_manifest()
    ok = True
    out("Python: %s" % sys.version.split()[0])
    if sys.version_info < (3, 10):
        ok = False
        out("  нужен Python 3.10 или новее")
    if has_lxml():
        import lxml.etree as et
        out("lxml: %s" % et.__version__)
    else:
        ok = False
        out("lxml: не установлен — установить: python -m pip install lxml")
    if not m:
        out("Движок: нет engine/manifest.json")
        return 2
    bad = [p for p, h in m["files"].items()
           if not os.path.isfile(os.path.join(ENGINE_DIR, p)) or sha256_file(os.path.join(ENGINE_DIR, p)) != h]
    out("Движок: ревизия %s, файлов %d, %s" % (m["commit"][:10], len(m["files"]),
                                              "целы" if not bad else "изменены или отсутствуют: %d" % len(bad)))
    for p in bad[:10]:
        out("  " + p)
    return 0 if ok and not bad else 2


def cmd_list():
    areas = {}
    for k, v in COMMANDS.items():
        areas.setdefault(k.split()[0], []).append((k, v))
    marks = {"read": "чтение", "output": "новый файл", "write": "меняет файлы", "new": "новый каталог"}
    for area, items in areas.items():
        out(area)
        for k, (_e, kind, _p, desc) in items:
            out("  %-17s %-13s %s" % (k, marks[kind], desc))
    out("Справка по команде: references/gen/<область>-<действие>.md")
    return 0


# --------------------------------------------------------------------------------------
# Что меняется: корень, снимок, журнал
# --------------------------------------------------------------------------------------
def arg_value(args, name):
    low = name.lower()
    for i, a in enumerate(args):
        if a.lower() == low and i + 1 < len(args):
            return args[i + 1]
    return None


def write_root(path):
    """Корень, внутри которого команда меняет файлы: выгрузка (Configuration.xml) или внешний объект."""
    p = os.path.abspath(path)
    start = p if os.path.isdir(p) else os.path.dirname(p)
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "Configuration.xml")):
            return d
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    # внешняя обработка или отчёт: каталог с корневым XML (ищем вверх каталог, где лежит <Имя>.xml)
    d = start
    while True:
        for fn in os.listdir(d) if os.path.isdir(d) else ():
            if fn.endswith(".xml") and _is_external(os.path.join(d, fn)):
                return d
        up = os.path.dirname(d)
        if up == d:
            return start
        d = up


def _is_external(path):
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    return b"<ExternalDataProcessor" in head or b"<ExternalReport" in head


def scope_root(kind, target):
    """Каталог, внутри которого команде разрешено писать; всё вне его движку запрещено."""
    t = os.path.abspath(target)
    if kind == "new":
        return t
    if kind == "output":
        return os.path.dirname(t)
    return write_root(t if os.path.exists(t) else os.path.dirname(t))


def snapshot(root):
    snap = {}
    for dp, dn, fns in os.walk(root):
        dn[:] = [d for d in dn if d != ".onec"]
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            snap[os.path.relpath(p, root).replace(os.sep, "/")] = (st.st_size, st.st_mtime_ns)
    return snap


def tree_size(snap):
    return sum(v[0] for v in snap.values())


def changes(root, before, after):
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(r for r in set(before) & set(after) if before[r] != after[r])
    return added, removed, modified


def project_of(root):
    """Папка проекта для журналов генератора: ближайшая вверх с .onec или .git; иначе папка над
    основной выгрузкой (cf, src/cf, src, config, dump); иначе над самим корнем."""
    probe = os.path.abspath(root)
    while not os.path.isdir(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    d = probe
    while True:
        if os.path.isdir(os.path.join(d, ".onec")) or os.path.exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    d = probe
    while True:
        for sub in [""] + list(onec_paths.ROOT_SUBDIRS):
            c = os.path.join(d, sub) if sub else d
            if os.path.isfile(os.path.join(c, "Configuration.xml")) and not onec_paths.is_extension(c):
                return onec_paths.project_dir(c)
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return onec_paths.project_dir(probe)


def _path(root, rel):
    return os.path.join(root, *rel.split("/"))


# --------------------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------------------
def run_engine(name, args, root, journal="", allow=()):
    """Скрипт движка под охраной записи (onec_gen_guard.py): писать можно только внутри root
    (и во временный каталог движка); исходное состояние каждого меняемого файла — в journal."""
    scratch = tempfile.mkdtemp(prefix="onec-gen-tmp-")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               ONEC_GEN_ROOT=os.path.abspath(root), ONEC_GEN_JOURNAL=os.path.abspath(journal) if journal else "",
               ONEC_GEN_ALLOW=os.pathsep.join([scratch] + [os.path.abspath(a) for a in allow]),
               TMPDIR=scratch, TEMP=scratch, TMP=scratch)
    try:
        r = subprocess.run([sys.executable, GUARD, engine_script(name)] + list(args), capture_output=True, env=env)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    text = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    lines = text.splitlines()
    for line in lines[:OUT_LIMIT]:
        out(line)
    if len(lines) > OUT_LIMIT:
        out("... вывод движка сокращён: ещё %d строк" % (len(lines) - OUT_LIMIT))
    return r.returncode


def show_diff(root_a, root_b, rels, limit=120):
    shown = 0
    for rel in rels:
        a = _path(root_a, rel)
        b = _path(root_b, rel)
        try:
            ta = open(a, encoding="utf-8-sig", errors="replace").read().splitlines() if os.path.isfile(a) else []
            tb = open(b, encoding="utf-8-sig", errors="replace").read().splitlines() if os.path.isfile(b) else []
        except OSError:
            continue
        for line in difflib.unified_diff(ta, tb, rel, rel, lineterm="", n=1):
            if shown >= limit:
                out("... diff сокращён")
                return
            out(line)
            shown += 1


def report(added, removed, modified):
    out("Изменено: %d, добавлено: %d, удалено: %d" % (len(modified), len(added), len(removed)))
    for title, items in (("изменён", modified), ("добавлен", added), ("удалён", removed)):
        for rel in items[:40]:
            out("  %s  %s" % (title, rel))
        if len(items) > 40:
            out("  ... ещё %d" % (len(items) - 40))


def rewrite_args(args, src, dst):
    """Пути внутри src заменяются на те же пути внутри dst (временная копия для пробного запуска)."""
    res = []
    nsrc = os.path.normcase(os.path.abspath(src))
    for a in args:
        if not a.startswith("-"):
            p = os.path.normcase(os.path.abspath(a))
            if p == nsrc or p.startswith(nsrc + os.sep):
                a = os.path.join(dst, os.path.relpath(os.path.abspath(a), src))
        res.append(a)
    return res


def cmd_run(key, args, dry_run):
    engine, kind, param, _desc = COMMANDS[key]
    if not os.path.isfile(engine_script(engine)):
        return fail("нет движка %s (engine/). Переустановите скилл." % engine)
    if not has_lxml():
        return fail("для генераторов нужна библиотека lxml: python -m pip install lxml")
    if kind == "read":
        # только чтение: движку разрешено писать лишь отчёт, если его попросили (-OutFile)
        report_file = arg_value(args, "-OutFile")
        sandbox = tempfile.mkdtemp(prefix="onec-gen-ro-")
        try:
            return run_engine(engine, args, sandbox, allow=[report_file] if report_file else [])
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)
    target = arg_value(args, param) if param else None
    if not target:
        return fail("не указан %s — путь, который команда меняет или создаёт" % param)
    root = scope_root(kind, target)
    if dry_run:
        return _dry_run(engine, args, kind, root)
    return _run_journaled(engine, args, kind, root)


def _dry_run(engine, args, kind, root):
    """Команда выполняется на временной копии корня; на запись настоящих файлов у движка прав нет."""
    exists = os.path.isdir(root)
    tmp = tempfile.mkdtemp(prefix="onec-gen-dry-")
    copy_root = os.path.join(tmp, os.path.basename(root.rstrip("\\/")) or "root")
    try:
        if kind == "output":                               # для нового файла достаточно файлов его каталога
            os.makedirs(copy_root)
            for fn in os.listdir(root) if exists else ():
                if os.path.isfile(os.path.join(root, fn)):
                    shutil.copy2(os.path.join(root, fn), os.path.join(copy_root, fn))
            before = snapshot(copy_root)
        else:
            before = snapshot(root) if exists else {}
            if exists:
                out("Пробный запуск на временной копии %s (%.0f МБ)..." % (root, tree_size(before) / 1024 / 1024))
                shutil.copytree(root, copy_root, ignore=shutil.ignore_patterns(".onec"))
        rc = run_engine(engine, rewrite_args(args, root, copy_root), copy_root)
        after = snapshot(copy_root) if os.path.isdir(copy_root) else {}
        added, removed, modified = changes(copy_root, before, after)
        for rel in list(modified):                            # копия получила новое время — сравнить содержимое
            if sha256_file(_path(root, rel)) == sha256_file(_path(copy_root, rel)):
                modified.remove(rel)
        report(added, removed, modified)
        show_diff(root, copy_root, modified + added)
        out("Пробный запуск: исходники не изменены.")
        return rc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _new_journal_dir(project, engine):
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + engine
    jdir = os.path.join(project, ".onec", "gen", run_id)
    n = 2
    while os.path.exists(jdir):
        jdir = os.path.join(project, ".onec", "gen", "%s-%d" % (run_id, n))
        n += 1
    os.makedirs(jdir)
    return jdir


def _run_journaled(engine, args, kind, root):
    """Запуск с журналом: исходное состояние каждого файла, который движок меняет, сохраняется
    до первой правки (охрана записи). После запуска — сверка со снимком корня: изменение,
    для которого нет сохранённого исходного состояния, — ошибка, такой запуск не отменяется."""
    root_existed = os.path.isdir(root)
    use_snapshot = kind != "output"                    # для output корень — каталог файла, его не обходим
    if kind == "output" and not root_existed:
        os.makedirs(root)                              # каталог для нового файла; undo уберёт его, если опустеет
    before = snapshot(root) if (use_snapshot and root_existed) else {}
    jdir = _new_journal_dir(project_of(root), engine)
    rc = run_engine(engine, args, root, journal=jdir)
    after = snapshot(root) if (use_snapshot and os.path.isdir(root)) else {}
    touched = []
    tfile = os.path.join(jdir, "touched.txt")
    if os.path.isfile(tfile):
        with open(tfile, encoding="utf-8") as f:
            touched = [ln.rstrip("\n") for ln in f if ln.strip()]
    s_added, s_removed, s_modified = changes(root, before, after)
    added, removed, modified, unsaved = {}, {}, {}, []
    for rel in sorted(set(touched) | set(s_added) | set(s_removed) | set(s_modified)):
        bak = _path(os.path.join(jdir, "backup"), rel)
        cur = _path(root, rel)
        had = os.path.isfile(bak)
        if not had and not os.path.isfile(_path(os.path.join(jdir, "new"), rel)) and rel in before:
            unsaved.append(rel)                                # изменён без сохранения исходного состояния
            continue
        has = os.path.isfile(cur)
        h_before = sha256_file(bak) if had else None
        h_after = sha256_file(cur) if has else None
        if had and has and h_before != h_after:
            modified[rel] = {"before": h_before, "after": h_after}
        elif had and not has:
            removed[rel] = h_before
        elif has and not had:
            added[rel] = h_after
    restored = False
    if "ConfigDumpInfo.xml" in modified or "ConfigDumpInfo.xml" in removed:
        _restore_file(os.path.join(jdir, "backup"), root, "ConfigDumpInfo.xml")
        modified.pop("ConfigDumpInfo.xml", None)
        removed.pop("ConfigDumpInfo.xml", None)
        restored = True
    out("")
    report(sorted(added), sorted(removed), sorted(modified) + unsaved)
    if restored:
        out("ConfigDumpInfo.xml восстановлен: генераторы его не меняют (обновляет Конфигуратор при выгрузке).")
    if not (added or removed or modified or unsaved):
        shutil.rmtree(jdir, ignore_errors=True)               # ничего не изменилось — журнал не нужен
        return rc
    journal = {"schema": 2, "engine": engine, "args": args, "root": os.path.abspath(root),
               "root_existed": root_existed, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "returncode": rc,
               "modified": modified, "added": added, "removed": removed, "unsaved": unsaved}
    with open(os.path.join(jdir, "journal.json"), "w", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False, indent=2)
    out("Журнал и исходные файлы: %s" % jdir)
    if unsaved:
        out("ОШИБКА: файлы изменены в обход резервной копии, отмена этого запуска невозможна: "
            + ", ".join(unsaved[:10]))
        out("Верните их из системы контроля версий (git checkout / git restore).")
        return rc or 3
    if rc:
        out("Движок завершился с ошибкой, но успел изменить файлы. Отменить: onec_gen.py undo %s"
            % os.path.basename(jdir))
    else:
        out("Отменить: onec_gen.py undo %s" % os.path.basename(jdir))
    return rc


def _restore_file(src_root, dst_root, rel):
    """Вернуть файл из резервной копии: запись во временный файл рядом и атомарная замена."""
    src = _path(src_root, rel)
    dst = _path(dst_root, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".onec-undo"
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def _journal(base, run_id):
    try:
        with open(os.path.join(base, run_id, "journal.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _undo_problems(j, jdir):
    """Все проверки отмены до первой записи. Пустой список — можно отменять."""
    root = j["root"]
    bdir = os.path.join(jdir, "backup")
    problems = []
    if j.get("schema") != 2:
        return ["журнал прежней версии onec_gen (до 3.0.1): его резервная копия могла быть неполной. "
                "Верните файлы из git или вручную из %s" % bdir]
    if j.get("unsaved"):
        return ["запуск изменил файлы без резервной копии (%s) — частичная отмена испортила бы выгрузку. "
                "Верните файлы из git" % ", ".join(j["unsaved"][:5])]
    for rel, h in list(j["modified"].items()):
        cur = _path(root, rel)
        if not os.path.isfile(cur) or sha256_file(cur) != h["after"]:
            problems.append("изменён после запуска: " + rel)
    for rel, h in j["added"].items():
        cur = _path(root, rel)
        if not os.path.isfile(cur) or sha256_file(cur) != h:
            problems.append("изменён после запуска: " + rel)
    for rel in j["removed"]:
        if os.path.lexists(_path(root, rel)):
            problems.append("создан заново после удаления: " + rel)
    originals = {r: h["before"] for r, h in j["modified"].items()}
    originals.update(j["removed"])
    for rel, h in originals.items():
        b = _path(bdir, rel)
        if not os.path.isfile(b) or sha256_file(b) != h:
            problems.append("нет или повреждена резервная копия: " + rel)
    return problems


def cmd_undo(run_id, project):
    base = os.path.join(project_of(project or os.getcwd()), ".onec", "gen")
    runs = sorted((d for d in os.listdir(base) if os.path.isfile(os.path.join(base, d, "journal.json"))),
                  key=lambda d: os.path.getmtime(os.path.join(base, d, "journal.json"))) if os.path.isdir(base) else []
    if not runs:
        return fail("нет журналов генератора в %s" % base)
    if not run_id:                                   # последний ещё не отменённый запуск
        open_runs = [d for d in runs if not _journal(base, d).get("undone")]
        if not open_runs:
            return fail("все запуски уже отменены")
        run_id = open_runs[-1]
    if run_id not in runs:
        return fail("нет запуска %s. Есть: %s" % (run_id, ", ".join(runs[-10:])))
    jdir = os.path.join(base, run_id)
    j = _journal(base, run_id)
    if j.get("undone"):
        return fail("запуск %s уже отменён" % run_id)
    problems = _undo_problems(j, jdir)
    if problems:
        return fail("отмена %s не выполнена, файлы не тронуты:\n  " % run_id + "\n  ".join(problems[:20]))
    root = j["root"]
    for rel in list(j["modified"]) + list(j["removed"]):
        _restore_file(os.path.join(jdir, "backup"), root, rel)
    for rel in j["added"]:
        os.remove(_path(root, rel))
    stop = os.path.normcase(os.path.dirname(root) if not j.get("root_existed", True) else root)
    for rel in sorted(j["added"], reverse=True):                    # убрать опустевшие каталоги новых файлов
        d = os.path.dirname(_path(root, rel))
        while os.path.normcase(d) != stop and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
    j["undone"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(jdir, "journal.json"), "w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False, indent=2)
    out("Отменено: %s — восстановлено %d, удалено новых %d" % (run_id, len(j["modified"]) + len(j["removed"]), len(j["added"])))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    for st in (sys.stdout, sys.stderr):
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8", errors="replace")
    if not argv or argv[0] in ("-h", "--help") or argv == ["help"]:
        out(__doc__)
        return 0
    if argv[0] == "list":
        return cmd_list()
    if argv[0] == "doctor":
        return cmd_doctor()
    if argv[0] == "undo":
        rest = argv[1:]
        project = None
        if "--project" in rest:
            i = rest.index("--project")
            project = rest[i + 1] if i + 1 < len(rest) else None
            rest = rest[:i] + rest[i + 2:]
        return cmd_undo(rest[0] if rest else None, project)
    if len(argv) < 2 or " ".join(argv[:2]) not in COMMANDS:
        return fail("неизвестная команда %s. Список: onec_gen.py list" % " ".join(argv[:2]))
    key = " ".join(argv[:2])
    rest = argv[2:]
    dry = "--dry-run" in rest
    rest = [a for a in rest if a != "--dry-run"]
    return cmd_run(key, rest, dry)


if __name__ == "__main__":
    sys.exit(main())
