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
  - перед записью сохраняют затрагиваемые файлы в <проект>/.onec/gen/<запуск>/ и после записи
    печатают, что изменено, добавлено и удалено; `undo` возвращает как было;
  - `--dry-run` выполняет команду на временной копии и показывает изменения, ничего не меняя;
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
FULL_BACKUP_LIMIT = 300 * 1024 * 1024        # корень меньше — резервная копия целиком
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
# Что меняется: снимок, резервная копия, изменения
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


def predicted(root, target, snap):
    """Файлы, которые команда может изменить, — если корень слишком велик для полной копии:
    Configuration.xml, ConfigDumpInfo.xml, целевой файл или каталог и XML-описание его объекта."""
    rel_target = os.path.relpath(os.path.abspath(target), root).replace(os.sep, "/") if target else ""
    keep = {"Configuration.xml", "ConfigDumpInfo.xml"}
    prefixes = []
    if rel_target and not rel_target.startswith(".."):
        t = rel_target.rstrip("/")
        prefixes.append(t)
        base = t[:-4] if t.endswith(".xml") else t
        prefixes.append(base)
        parts = base.split("/")
        if len(parts) >= 2:                       # Catalogs/Товары/Forms/... -> объект Catalogs/Товары
            prefixes.append("/".join(parts[:2]))
    out_ = set()
    for rel in snap:
        if rel in keep or any(rel == p or rel == p + ".xml" or rel.startswith(p + "/") for p in prefixes):
            out_.add(rel)
    return out_


def copy_files(root, rels, dest):
    for rel in rels:
        src = os.path.join(root, *rel.split("/"))
        dst = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def changes(root, before, after):
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(r for r in set(before) & set(after) if before[r] != after[r])
    return added, removed, modified


def project_of(root):
    """Папка проекта для журналов генератора: там, где .onec индекса, иначе над выгрузкой."""
    found = onec_paths.find_root(root)
    if found and not onec_paths.is_extension(found):
        return onec_paths.project_dir(found)
    d = root
    while True:
        if os.path.isdir(os.path.join(d, ".onec")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return onec_paths.project_dir(root)
        d = up


# --------------------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------------------
def run_engine(name, args, cwd=None):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, engine_script(name)] + list(args), cwd=cwd, capture_output=True, env=env)
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
        a = os.path.join(root_a, *rel.split("/"))
        b = os.path.join(root_b, *rel.split("/"))
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


def rewrite_args(args, root, copy_root):
    """Пути внутри root заменяются на те же пути во временной копии."""
    res = []
    nroot = os.path.normcase(os.path.abspath(root))
    for a in args:
        if not a.startswith("-"):
            p = os.path.normcase(os.path.abspath(a))
            if p == nroot or p.startswith(nroot + os.sep):
                a = os.path.join(copy_root, os.path.relpath(os.path.abspath(a), root))
        res.append(a)
    return res


def cmd_run(key, args, dry_run):
    engine, kind, param, _desc = COMMANDS[key]
    if not os.path.isfile(engine_script(engine)):
        return fail("нет движка %s (engine/). Переустановите скилл." % engine)
    if not has_lxml():
        return fail("для генераторов нужна библиотека lxml: python -m pip install lxml")
    if kind == "read":
        return run_engine(engine, args)
    target = arg_value(args, param) if param else None
    if not target:
        return fail("не указан %s — путь, который команда меняет или создаёт" % param)
    if kind == "new":                       # новый каталог: менять в существующих исходниках нечего
        return run_engine(engine, args)
    if kind == "output":
        existed = os.path.isfile(target)
        if existed and not dry_run:
            root = os.path.dirname(os.path.abspath(target))
            return _run_with_backup(engine, args, root, target, {os.path.basename(target)}, full=False)
        if dry_run:
            out("Пробный запуск: результат будет записан во временный файл.")
            tmp = tempfile.mkdtemp(prefix="onec-gen-")
            try:
                t2 = os.path.join(tmp, os.path.basename(target))
                if existed:
                    shutil.copy2(target, t2)
                a2 = [t2 if x == target else x for x in args]
                rc = run_engine(engine, a2)
                if os.path.isfile(t2):
                    show_diff(os.path.dirname(os.path.abspath(target)) if existed else tmp + "-none", tmp,
                              [os.path.basename(target)])
                return rc
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        return run_engine(engine, args)
    # write: меняет исходники внутри корня
    root = write_root(target if os.path.exists(target) else os.path.dirname(os.path.abspath(target)))
    before = snapshot(root)
    full = tree_size(before) <= FULL_BACKUP_LIMIT
    if dry_run:
        size = tree_size(before) / 1024 / 1024
        out("Пробный запуск на временной копии %s (%.0f МБ)..." % (root, size))
        tmp = tempfile.mkdtemp(prefix="onec-gen-")
        copy_root = os.path.join(tmp, os.path.basename(root) or "root")
        try:
            shutil.copytree(root, copy_root, ignore=shutil.ignore_patterns(".onec"))
            rc = run_engine(engine, rewrite_args(args, root, copy_root))
            added, removed, modified = changes(copy_root, before, snapshot(copy_root))
            report(added, removed, modified)
            show_diff(root, copy_root, modified + added)
            out("Пробный запуск: исходники не изменены.")
            return rc
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    rels = set(before) if full else predicted(root, target, before)
    return _run_with_backup(engine, args, root, target, rels, full, before)


def _run_with_backup(engine, args, root, target, rels, full, before=None):
    before = before if before is not None else snapshot(root)
    project = project_of(root)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + engine
    jdir = os.path.join(project, ".onec", "gen", run_id)
    n = 2
    while os.path.exists(jdir):
        jdir = os.path.join(project, ".onec", "gen", "%s-%d" % (run_id, n))
        n += 1
    bdir = os.path.join(jdir, "backup")
    copy_files(root, rels, bdir)
    rc = run_engine(engine, args)
    after = snapshot(root)
    added, removed, modified = changes(root, before, after)
    restored = []
    if "ConfigDumpInfo.xml" in modified or "ConfigDumpInfo.xml" in removed:
        if "ConfigDumpInfo.xml" in rels:
            copy_files(bdir, ["ConfigDumpInfo.xml"], root)
            restored.append("ConfigDumpInfo.xml")
            modified = [r for r in modified if r != "ConfigDumpInfo.xml"]
            removed = [r for r in removed if r != "ConfigDumpInfo.xml"]
    unsaved = [r for r in modified + removed if r not in rels]
    journal = {"schema": 1, "engine": engine, "args": args, "root": root, "full_backup": full,
               "time": time.strftime("%Y-%m-%d %H:%M:%S"), "returncode": rc,
               "modified": {r: sha256_file(os.path.join(root, *r.split("/"))) for r in modified},
               "added": {r: sha256_file(os.path.join(root, *r.split("/"))) for r in added},
               "removed": removed, "unsaved": unsaved}
    out("")
    report(added, removed, modified)
    if not (added or removed or modified or unsaved):
        shutil.rmtree(jdir, ignore_errors=True)             # ничего не изменилось — журнал не нужен
        if restored:
            out("ConfigDumpInfo.xml восстановлен: генераторы его не меняют (обновляет Конфигуратор при выгрузке).")
        return rc
    with open(os.path.join(jdir, "journal.json"), "w", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False, indent=2)
    if restored:
        out("ConfigDumpInfo.xml восстановлен: генераторы его не меняют (обновляет Конфигуратор при выгрузке).")
    if unsaved:
        out("ВНИМАНИЕ: изменены файлы вне резервной копии (их отмена невозможна): " + ", ".join(unsaved[:10]))
    out("Журнал и резервная копия: %s" % jdir)
    out("Отменить: onec_gen.py undo %s" % os.path.basename(jdir))
    return rc


def _journal(base, run_id):
    try:
        with open(os.path.join(base, run_id, "journal.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def cmd_undo(run_id, project):
    base = os.path.join(project or os.getcwd(), ".onec", "gen")
    if not os.path.isdir(base):
        found = onec_paths.find_root(project or os.getcwd())
        base = os.path.join(onec_paths.project_dir(found), ".onec", "gen") if found else base
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
    with open(os.path.join(jdir, "journal.json"), encoding="utf-8") as f:
        j = json.load(f)
    if j.get("undone"):
        return fail("запуск %s уже отменён" % run_id)
    root = j["root"]
    # файлы после запуска не должны были меняться — иначе отмена затрёт чужую правку
    drift = [r for r, h in list(j["modified"].items()) + list(j["added"].items())
             if not os.path.isfile(os.path.join(root, *r.split("/"))) or sha256_file(os.path.join(root, *r.split("/"))) != h]
    if drift:
        return fail("после запуска изменены файлы, отмена не выполнена: %s" % ", ".join(drift[:10]))
    copy_files(os.path.join(jdir, "backup"), list(j["modified"]) + list(j["removed"]), root)
    for r in j["added"]:
        os.remove(os.path.join(root, *r.split("/")))
    for r in sorted(j["added"], reverse=True):                    # убрать опустевшие каталоги новых объектов
        d = os.path.dirname(os.path.join(root, *r.split("/")))
        while d != root and os.path.isdir(d) and not os.listdir(d):
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
    if not argv or argv[0] in ("-h", "--help", "help"):
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
