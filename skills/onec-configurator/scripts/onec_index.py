#!/usr/bin/env python3
"""onec_index - индекс выгрузки конфигурации 1С (Конфигуратор, XML) в SQLite.

Только стандартная библиотека, Python 3.9+. Запускать: python3 -I onec_index.py <команда> ...

Команды:
  build [--full]               построить/обновить индекс (инкрементально, по объектам)
  stats                        сводка по конфигурации
  info  <Объект>               компактная карточка объекта (реквизиты, формы, модули ...)
  refs  <Объект> [--kind K] [--member M] [--limit N] [--offset N]
                               где используется объект (типы, подсистемы, роли, модули ...)
  find  <подстрока> [--limit N]
                               поиск по именам объектов, реквизитов, процедур

Корень выгрузки: --root DIR, либо переменная ONEC_DUMP, либо ближайший родитель
текущей папки с Configuration.xml. Индекс лежит в <корень>/.onec/index.sqlite.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v8container  # noqa: E402
import onec_query  # noqa: E402

SCHEMA_VERSION = "3"

# kind, каталог выгрузки, рус. ед.ч., рус. мн.ч., есть менеджер в коде (англ. мн.ч. == каталог)
KINDS = [
    ("Catalog", "Catalogs", "Справочник", "Справочники", True),
    ("Document", "Documents", "Документ", "Документы", True),
    ("DocumentJournal", "DocumentJournals", "ЖурналДокументов", "ЖурналыДокументов", True),
    ("Enum", "Enums", "Перечисление", "Перечисления", True),
    ("Report", "Reports", "Отчет", "Отчеты", True),
    ("DataProcessor", "DataProcessors", "Обработка", "Обработки", True),
    ("ChartOfCharacteristicTypes", "ChartsOfCharacteristicTypes", "ПланВидовХарактеристик", "ПланыВидовХарактеристик", True),
    ("ChartOfAccounts", "ChartsOfAccounts", "ПланСчетов", "ПланыСчетов", True),
    ("ChartOfCalculationTypes", "ChartsOfCalculationTypes", "ПланВидовРасчета", "ПланыВидовРасчета", True),
    ("InformationRegister", "InformationRegisters", "РегистрСведений", "РегистрыСведений", True),
    ("AccumulationRegister", "AccumulationRegisters", "РегистрНакопления", "РегистрыНакопления", True),
    ("AccountingRegister", "AccountingRegisters", "РегистрБухгалтерии", "РегистрыБухгалтерии", True),
    ("CalculationRegister", "CalculationRegisters", "РегистрРасчета", "РегистрыРасчета", True),
    ("BusinessProcess", "BusinessProcesses", "БизнесПроцесс", "БизнесПроцессы", True),
    ("Task", "Tasks", "Задача", "Задачи", True),
    ("ExchangePlan", "ExchangePlans", "ПланОбмена", "ПланыОбмена", True),
    ("Constant", "Constants", "Константа", "Константы", True),
    ("SessionParameter", "SessionParameters", "ПараметрСеанса", "ПараметрыСеанса", True),
    ("Sequence", "Sequences", "Последовательность", "Последовательности", True),
    ("SettingsStorage", "SettingsStorages", "ХранилищеНастроек", "ХранилищаНастроек", True),
    ("ExternalDataSource", "ExternalDataSources", "ВнешнийИсточникДанных", "ВнешниеИсточникиДанных", True),
    ("CommonModule", "CommonModules", "ОбщийМодуль", "ОбщиеМодули", False),
    ("CommonForm", "CommonForms", "ОбщаяФорма", "ОбщиеФормы", False),
    ("CommonCommand", "CommonCommands", "ОбщаяКоманда", "ОбщиеКоманды", False),
    ("CommonTemplate", "CommonTemplates", "ОбщийМакет", "ОбщиеМакеты", False),
    ("CommonPicture", "CommonPictures", "ОбщаяКартинка", "ОбщиеКартинки", False),
    ("CommonAttribute", "CommonAttributes", "ОбщийРеквизит", "ОбщиеРеквизиты", False),
    ("CommandGroup", "CommandGroups", "ГруппаКоманд", "ГруппыКоманд", False),
    ("DefinedType", "DefinedTypes", "ОпределяемыйТип", "ОпределяемыеТипы", False),
    ("EventSubscription", "EventSubscriptions", "ПодпискаНаСобытие", "ПодпискиНаСобытия", False),
    ("ScheduledJob", "ScheduledJobs", "РегламентноеЗадание", "РегламентныеЗадания", False),
    ("FunctionalOption", "FunctionalOptions", "ФункциональнаяОпция", "ФункциональныеОпции", False),
    ("FunctionalOptionsParameter", "FunctionalOptionsParameters", "ПараметрФункциональныхОпций", "ПараметрыФункциональныхОпций", False),
    ("FilterCriterion", "FilterCriteria", "КритерийОтбора", "КритерииОтбора", False),
    ("Role", "Roles", "Роль", "Роли", False),
    ("Subsystem", "Subsystems", "Подсистема", "Подсистемы", False),
    ("WebService", "WebServices", "WebСервис", "WebСервисы", False),
    ("HTTPService", "HTTPServices", "HTTPСервис", "HTTPСервисы", False),
    ("WSReference", "WSReferences", "WSСсылка", "WSСсылки", False),
    ("XDTOPackage", "XDTOPackages", "ПакетXDTO", "ПакетыXDTO", False),
    ("Language", "Languages", "Язык", "Языки", False),
    ("StyleItem", "StyleItems", "ЭлементСтиля", "ЭлементыСтиля", False),
    ("Style", "Styles", "Стиль", "Стили", False),
    ("DocumentNumerator", "DocumentNumerators", "НумераторДокументов", "НумераторыДокументов", False),
    ("Interface", "Interfaces", "Интерфейс", "Интерфейсы", False),
    ("Bot", "Bots", "Бот", "Боты", False),
    ("IntegrationService", "IntegrationServices", "СервисИнтеграции", "СервисыИнтеграции", False),
]
DIR_KIND = {d: k for k, d, _, _, _ in KINDS}
KIND_DIR = {k: d for k, d, _, _, _ in KINDS}
KIND_RU = {k: s for k, _, s, _, _ in KINDS}
# слова, по которым вид объекта определяется в тексте/запросах/вводе: в нижнем регистре
WORD_KIND = {}      # любое слово (ед./мн., рус./англ.) -> kind
PLURAL_KIND = {}    # мн.ч. менеджеров -> kind (для Справочники.Имя / Catalogs.Name)
SINGULAR_KIND = {}  # ед.ч. -> kind (для Справочник.Имя в запросах/строках)
for _k, _d, _s, _p, _m in KINDS:
    for _w in (_k, _s, _p, _d):
        WORD_KIND[_w.lower()] = _k
    PLURAL_KIND[_p.lower()] = _k
    PLURAL_KIND[_d.lower()] = _k
    if _m:
        SINGULAR_KIND[_s.lower()] = _k
        SINGULAR_KIND[_k.lower()] = _k
MANAGER_KIND = dict(PLURAL_KIND)
MANAGER_KIND.update(SINGULAR_KIND)

IDENT = r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*"
_KINDS_ALT = "|".join(sorted((k for k, *_ in KINDS), key=len, reverse=True))
TYPE_RE = re.compile(
    r"(?<![\w.])(?:cfg:)?(%s)(?:Ref|Object|Manager|Selection|List|RecordSet|RecordKey|RecordManager)\.(%s)"
    % (_KINDS_ALT, IDENT))
DEFINED_RE = re.compile(r"(?<![\w.])cfg:DefinedType\.(%s)" % IDENT)
DOTTED_RE = re.compile(r"(?<![\w.:])(%s)\.(%s)(?:\.([\w.А-Яа-яЁё]+))?" % (_KINDS_ALT, IDENT))
CODE_RE = re.compile(r"(?<![\w.])(%s)\.(%s)(?:\.(%s))?" % (IDENT, IDENT, IDENT))
PROC_RE = re.compile(
    r"^[ \t]*(Процедура|Функция|Procedure|Function)[ \t]+(%s)[ \t]*\(([^)]*)\)([ \t]*(?:Экспорт|Export)\b)?" % IDENT,
    re.IGNORECASE | re.MULTILINE)
COMMENT_LINE_RE = re.compile(r"^[ \t]*//[^\n]*", re.MULTILINE)

SKIP_TAGS = {"Synonym", "Comment", "ToolTip", "Format", "EditFormat", "Mask", "Explanation", "Name"}
MEMBER_TAGS = {"Attribute", "TabularSection", "Dimension", "Resource", "AddressingAttribute",
               "AccountingFlag", "ExtDimensionAccountingFlag", "Column", "EnumValue", "Command",
               "Operation", "URLTemplate", "Parameter", "Characteristic", "RecalculationDimension"}
SIMPLE_CHILD = {"Form", "Template", "Recalculation", "Method", "Table", "Cube", "Function", "Field", "Value"}


def lname(e):
    t = e.tag
    return t.rsplit("}", 1)[-1] if isinstance(t, str) else ""


def ru_text(e):
    if e is None:
        return ""
    first = ""
    for it in e:
        if lname(it) != "item":
            continue
        lang = content = ""
        for c in it:
            n = lname(c)
            if n == "lang":
                lang = c.text or ""
            elif n == "content":
                content = c.text or ""
        if lang == "ru":
            return content
        first = first or content
    return first


def child(e, name):
    for c in e:
        if lname(c) == name:
            return c
    return None


def child_props(root, name):
    """<name>/<Properties> или None."""
    c = child(root, name)
    return child(c, "Properties") if c is not None else None


def type_str(t):
    """<Type> -> компактная строка типа."""
    if t is None:
        return ""
    parts, q = [], {}
    for c in t:
        n = lname(c)
        x = (c.text or "").strip()
        if n in ("Type", "TypeSet"):
            parts.append(x)
        elif n.endswith("Qualifiers"):
            q[n] = {lname(z): (z.text or "").strip() for z in c}
    out = []
    for p in parts:
        if p == "xs:string":
            ln = q.get("StringQualifiers", {}).get("Length", "0")
            out.append("String(%s)" % ln if ln not in ("0", "") else "String")
        elif p == "xs:decimal":
            nq = q.get("NumberQualifiers", {})
            s = "Number(%s,%s)" % (nq.get("Digits", "?"), nq.get("FractionDigits", "0"))
            if nq.get("AllowedSign") == "Nonnegative":
                s += "+"
            out.append(s)
        elif p == "xs:dateTime":
            out.append({"Date": "Date", "Time": "Time"}.get(q.get("DateQualifiers", {}).get("DateFractions"), "DateTime"))
        elif p == "xs:boolean":
            out.append("Boolean")
        else:
            out.append(re.sub(r"^(cfg|v8|xs):", "", p))
    if len(out) > 6:
        out = out[:5] + ["...(+%d)" % (len(out) - 5)]
    return "|".join(out)


def xml_refs(text):
    """Ссылки на объекты метаданных в текстовом узле XML -> [(kind, name, member)]."""
    res = []
    for m in TYPE_RE.finditer(text):
        res.append((m.group(1), m.group(2), ""))
    for m in DEFINED_RE.finditer(text):
        res.append(("DefinedType", m.group(1), ""))
    for m in DOTTED_RE.finditer(text):
        res.append((m.group(1), m.group(2), m.group(3) or ""))
    return res


def read_text(path):
    with open(path, "rb") as f:
        data = f.read()
    return data.decode("utf-8-sig", errors="replace")


# --------------------------------------------------------------------------------------
# БД
# --------------------------------------------------------------------------------------
DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS units(unit TEXT PRIMARY KEY, sig TEXT);
CREATE TABLE IF NOT EXISTS objects(
  id INTEGER PRIMARY KEY, unit TEXT, kind TEXT, name TEXT, name_lc TEXT, parent TEXT,
  synonym TEXT, syn_lc TEXT, uuid TEXT, file TEXT, props TEXT);
CREATE INDEX IF NOT EXISTS objects_name ON objects(name_lc, kind);
CREATE INDEX IF NOT EXISTS objects_unit ON objects(unit);
CREATE TABLE IF NOT EXISTS members(
  unit TEXT, obj_id INTEGER, mkind TEXT, path TEXT, name TEXT, name_lc TEXT, synonym TEXT, types TEXT, extra TEXT);
CREATE INDEX IF NOT EXISTS members_obj ON members(obj_id);
CREATE INDEX IF NOT EXISTS members_name ON members(name_lc);
CREATE INDEX IF NOT EXISTS members_unit ON members(unit);
CREATE TABLE IF NOT EXISTS refs(
  unit TEXT, src_kind TEXT, src_name TEXT, src_where TEXT, how TEXT,
  dst_kind TEXT, dst_name TEXT, dst_lc TEXT, member TEXT, mem_lc TEXT, cnt INTEGER, lines TEXT, file TEXT);
CREATE INDEX IF NOT EXISTS refs_dst ON refs(dst_kind, dst_lc);
CREATE INDEX IF NOT EXISTS refs_unit ON refs(unit);
CREATE TABLE IF NOT EXISTS modules(
  unit TEXT, owner_kind TEXT, owner_name TEXT, mod TEXT, file TEXT, lines INTEGER, procs INTEGER, exports INTEGER);
CREATE INDEX IF NOT EXISTS modules_owner ON modules(owner_kind, owner_name);
CREATE INDEX IF NOT EXISTS modules_unit ON modules(unit);
CREATE TABLE IF NOT EXISTS procs(
  unit TEXT, owner_kind TEXT, owner_name TEXT, mod TEXT, name TEXT, name_lc TEXT,
  is_func INTEGER, export INTEGER, params TEXT, line INTEGER, file TEXT);
CREATE INDEX IF NOT EXISTS procs_name ON procs(name_lc);
CREATE INDEX IF NOT EXISTS procs_owner ON procs(owner_kind, owner_name);
CREATE INDEX IF NOT EXISTS procs_unit ON procs(unit);
CREATE TABLE IF NOT EXISTS errors(unit TEXT, file TEXT, msg TEXT);
"""
UNIT_TABLES = ("objects", "members", "refs", "modules", "procs", "errors")


def open_db(path, create=True):
    if create:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    except sqlite3.Error:
        row = None
    if row is not None and row[0] != SCHEMA_VERSION:
        if not create:
            sys.exit("Индекс создан другой версией скрипта. Выполните: onec_index.py build")
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            conn.execute("DROP TABLE IF EXISTS %s" % t)
        conn.commit()
    conn.executescript(DDL)
    return conn


def find_root(arg):
    cand = arg or os.environ.get("ONEC_DUMP") or os.getcwd()
    p = os.path.abspath(cand)
    while True:
        if os.path.isfile(os.path.join(p, "Configuration.xml")):
            return p
        up = os.path.dirname(p)
        if up == p:
            break
        p = up
    sys.exit("Не найден Configuration.xml (корень выгрузки). Укажите --root или ONEC_DUMP.")


# --------------------------------------------------------------------------------------
# Извлечение
# --------------------------------------------------------------------------------------
class Unit:
    """Накопитель строк одного объекта верхнего уровня (юнита)."""

    def __init__(self, unit):
        self.unit = unit
        self.objects = []   # (kind,name,parent,synonym,uuid,file,props, members[])
        self.refs = {}
        self.modules = []
        self.procs = []
        self.errors = []

    def add_ref(self, src_kind, src_name, where, how, dk, dn, member, file, line=0, cnt=1):
        key = (src_kind, src_name, where, how, dk, dn.lower(), member, file)
        r = self.refs.get(key)
        if r is None:
            self.refs[key] = [dn, cnt, [line] if line else []]
        else:
            r[1] += cnt
            if line and len(r[2]) < 3:
                r[2].append(line)


def walk_refs(u, e, src_kind, src_name, ctx, prop, file):
    """Обход элемента: собираем ссылки из текстовых узлов. ctx - путь члена, prop - свойство."""
    n = lname(e)
    if n in SKIP_TAGS:
        return
    if n in MEMBER_TAGS and child(e, "Properties") is not None:
        nm = child(child(e, "Properties"), "Name")
        ctx = (ctx + "/" if ctx else "") + n + ":" + ((nm.text or "") if nm is not None else "")
    if e.text and e.text.strip():
        t = e.text.strip()
        if "." in t or "Ref" in t:
            for dk, dn, mem in xml_refs(t):
                u.add_ref(src_kind, src_name, ctx, prop, dk, dn, mem, file)
    for c in e:
        cn = lname(c)
        np_ = prop
        if lname(e) == "Properties" or prop == "":
            np_ = cn
        walk_refs(u, c, src_kind, src_name, ctx, np_, file)


def parse_object(u, root_dir, rel, kind, parent=""):
    """Разобрать XML объекта метаданных. Возвращает (obj_index, ChildObjects-element)."""
    path = os.path.join(root_dir, rel)
    try:
        tree = ET.parse(path)
    except Exception as ex:  # noqa: BLE001
        u.errors.append((rel, "xml: %s" % ex))
        return None
    top = tree.getroot()
    obj = None
    for c in top:
        if isinstance(c.tag, str):
            obj = c
            break
    if obj is None:
        return None
    props = child(obj, "Properties")
    if props is None:
        return None
    nm = child(props, "Name")
    name = (nm.text or "").strip() if nm is not None else ""
    synonym = ru_text(child(props, "Synonym"))
    scalar = {}
    for c in props:
        if len(c) == 0 and c.text and c.text.strip() and lname(c) not in ("Name", "Comment"):
            if len(c.text) < 120:
                scalar[lname(c)] = c.text.strip()
    members = []
    co = child(obj, "ChildObjects")
    kind = lname(obj) if kind is None else kind
    if co is not None:
        for m in co:
            mk = lname(m)
            mp = child(m, "Properties")
            if mp is not None:
                mname = (child(mp, "Name").text or "") if child(mp, "Name") is not None else ""
                extra = []
                for fl in ("Indexing", "Use", "FillChecking", "Format"):
                    z = child(mp, fl)
                    if z is not None and z.text and z.text.strip() and z.text.strip() not in ("DontCheck", "DontIndex", "ForItem"):
                        if fl in ("Indexing", "FillChecking"):
                            extra.append("%s=%s" % (fl, z.text.strip()))
                members.append((mk, "", mname, ru_text(child(mp, "Synonym")), type_str(child(mp, "Type")), ";".join(extra)))
                if mk == "TabularSection":
                    sub = child(m, "ChildObjects")
                    if sub is not None:
                        for a in sub:
                            ap = child(a, "Properties")
                            if ap is None:
                                continue
                            an = child(ap, "Name")
                            members.append((lname(a), mname, (an.text or "") if an is not None else "",
                                            ru_text(child(ap, "Synonym")), type_str(child(ap, "Type")), ""))
            else:
                members.append((mk, "", (m.text or "").strip(), "", "", ""))
    u.objects.append((kind, name, parent, synonym, obj.get("uuid", ""), rel.replace(os.sep, "/"), json.dumps(scalar, ensure_ascii=False), members))
    # ссылки
    walk_refs(u, props, kind, name, "", "", rel)
    if co is not None:
        for m in co:
            walk_refs(u, m, kind, name, "", "", rel)
    return name


def scan_module(u, known, cm_names, text, owner_kind, owner_name, mod, rel):
    """Процедуры + ссылки на объекты/общие модули внутри BSL-текста."""
    text = COMMENT_LINE_RE.sub("", text)
    n_lines = text.count("\n") + 1
    procs = 0
    exports = 0
    for m in PROC_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        exp = 1 if m.group(4) else 0
        procs += 1
        exports += exp
        u.procs.append((owner_kind, owner_name, mod, m.group(2), m.group(2).lower(),
                        1 if m.group(1).lower() in ("функция", "function") else 0, exp,
                        " ".join(m.group(3).split())[:200], line, rel))
    u.modules.append((owner_kind, owner_name, mod, rel, n_lines, procs, exports))
    where = mod
    last, line = 0, 1
    for m in CODE_RE.finditer(text):
        a = m.group(1).lower()
        b = m.group(2)
        kind = None
        name = member = None
        if a in ("метаданные", "metadata") and m.group(3):
            k = PLURAL_KIND.get(b.lower())
            if k:
                kind, name, member = k, m.group(3), ""
        else:
            k = MANAGER_KIND.get(a)
            if k:
                kind, name, member = k, b, m.group(3) or ""
            elif a in cm_names:
                kind, name, member = "CommonModule", cm_names[a], b
        if kind is None:
            continue
        if (kind, name.lower()) not in known and kind != "CommonModule":
            continue
        line += text.count("\n", last, m.start())
        last = m.start()
        u.add_ref(owner_kind, owner_name, where, "code", kind, name, member, rel, line)


def process_unit(conn, root, kind, name, known, cm_names):
    unit = "%s/%s" % (KIND_DIR[kind], name)
    u = Unit(unit)
    rel_xml = "%s/%s.xml" % (KIND_DIR[kind], name)
    base = os.path.join(root, KIND_DIR[kind], name)
    parse_object(u, root, rel_xml, kind)

    def process_dir(dirpath, owner_kind, owner_name, relbase):
        # модули объекта и прочее в Ext/
        ext = os.path.join(dirpath, "Ext")
        if os.path.isdir(ext):
            for fn in sorted(os.listdir(ext)):
                p = os.path.join(ext, fn)
                if fn.endswith(".bsl") and os.path.isfile(p):
                    scan_module(u, known, cm_names, read_text(p), owner_kind, owner_name, fn[:-4], relbase + "/Ext/" + fn)
                elif fn == "Rights.xml" and owner_kind == "Role":
                    scan_rights(u, read_text(p), owner_name, relbase + "/Ext/" + fn)
                elif fn == "Predefined.xml":
                    scan_predefined(u, read_text(p), owner_kind, owner_name)
                elif fn == "Template.xml" and owner_kind == "CommonTemplate":
                    scan_query_xml(u, p, owner_kind, owner_name, "Template", relbase + "/Ext/Template.xml")
        fdir = os.path.join(dirpath, "Forms")
        if os.path.isdir(fdir):
            for fn in sorted(os.listdir(fdir)):
                fp = os.path.join(fdir, fn)
                if os.path.isdir(fp):
                    process_form(fp, owner_kind, owner_name, relbase + "/Forms/" + fn, fn)
        cdir = os.path.join(dirpath, "Commands")
        if os.path.isdir(cdir):
            for fn in sorted(os.listdir(cdir)):
                p = os.path.join(cdir, fn, "Ext", "CommandModule.bsl")
                if os.path.isfile(p):
                    scan_module(u, known, cm_names, read_text(p), owner_kind, owner_name, "Command:" + fn,
                                relbase + "/Commands/%s/Ext/CommandModule.bsl" % fn)
        tdir = os.path.join(dirpath, "Templates")
        if os.path.isdir(tdir):
            for fn in sorted(os.listdir(tdir)):
                if fn.endswith(".xml"):
                    meta = os.path.join(tdir, fn)
                    try:
                        tt = child_props(ET.parse(meta).getroot(), "Template")
                        ttype = (child(tt, "TemplateType").text or "") if tt is not None and child(tt, "TemplateType") is not None else ""
                    except Exception:  # noqa: BLE001
                        ttype = ""
                    u.objects[0][7].append(("TemplateFile", "", fn[:-4], "", ttype, ""))
                    tx = os.path.join(tdir, fn[:-4], "Ext", "Template.xml")
                    if os.path.isfile(tx):
                        scan_query_xml(u, tx, owner_kind, owner_name, "Template:" + fn[:-4],
                                       relbase + "/Templates/%s/Ext/Template.xml" % fn[:-4])

    def process_form(fp, owner_kind, owner_name, relbase, fname):
        meta = fp + ".xml"
        ftype = ""
        if os.path.isfile(meta):
            try:
                pr = child_props(ET.parse(meta).getroot(), "Form")
                z = child(pr, "FormType") if pr is not None else None
                ftype = (z.text or "") if z is not None else ""
            except Exception:  # noqa: BLE001
                pass
        ext = os.path.join(fp, "Ext")
        fx = os.path.join(ext, "Form.xml")
        fb = os.path.join(ext, "Form.bin")
        kindform = "ordinary" if os.path.isfile(fb) else ("managed" if os.path.isfile(fx) else "?")
        u.objects[0][7].append(("FormFile", "", fname, "", kindform, ftype))
        if os.path.isfile(fx):
            scan_text_refs(u, read_text(fx), owner_kind, owner_name, "Form:" + fname, "FormXml", relbase + "/Ext/Form.xml")
            scan_query_xml(u, fx, owner_kind, owner_name, "Form:" + fname, relbase + "/Ext/Form.xml")
        mp = os.path.join(ext, "Form", "Module.bsl")
        if os.path.isfile(mp):
            scan_module(u, known, cm_names, read_text(mp), owner_kind, owner_name, "Form:" + fname, relbase + "/Ext/Form/Module.bsl")
        elif os.path.isfile(fb):
            try:
                for en, _c, _m, body in v8container.parse(open(fb, "rb").read()):
                    if en == "module":
                        scan_module(u, known, cm_names, body.decode("utf-8-sig", errors="replace"), owner_kind, owner_name,
                                    "Form:" + fname + "(ordinary)", relbase + "/Ext/Form.bin")
            except Exception as ex:  # noqa: BLE001
                u.errors.append((relbase + "/Ext/Form.bin", "container: %s" % ex))

    if not u.objects:
        return u
    if kind == "Subsystem":
        def walk_sub(dirpath, relbase, parent):
            sdir = os.path.join(dirpath, "Subsystems")
            if not os.path.isdir(sdir):
                return
            for fn in sorted(os.listdir(sdir)):
                if fn.endswith(".xml"):
                    nm = fn[:-4]
                    parse_object(u, root, relbase + "/Subsystems/" + fn, "Subsystem", parent)
                    walk_sub(os.path.join(sdir, nm), relbase + "/Subsystems/" + nm, parent + "." + nm)
        walk_sub(base, "%s/%s" % (KIND_DIR[kind], name), name)
    if kind == "CommonForm" and os.path.isdir(base):
        process_form(base, kind, name, "%s/%s" % (KIND_DIR[kind], name), name)
    if os.path.isdir(base):
        process_dir(base, kind, name, "%s/%s" % (KIND_DIR[kind], name))
    return u


def scan_text_refs(u, text, owner_kind, owner_name, where, how, rel):
    """Ссылки на метаданные в «сыром» тексте XML с номерами строк."""
    found = []
    for rx in (TYPE_RE, DEFINED_RE, DOTTED_RE):
        for m in rx.finditer(text):
            if rx is TYPE_RE:
                found.append((m.start(), m.group(1), m.group(2), ""))
            elif rx is DEFINED_RE:
                found.append((m.start(), "DefinedType", m.group(1), ""))
            else:
                found.append((m.start(), m.group(1), m.group(2), m.group(3) or ""))
    found.sort()
    last, line = 0, 1
    for pos, dk, dn, mem in found:
        line += text.count("\n", last, pos)
        last = pos
        u.add_ref(owner_kind, owner_name, where, how, dk, dn, mem, rel, line)


def scan_query_xml(u, path, owner_kind, owner_name, where, rel):
    """Запросы СКД (Template.xml) и динамических списков (Form.xml) -> refs how='query'."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
            if b"DataCompositionSchema" not in head and b"<QueryText>" not in head and not path.endswith("Form.xml"):
                return
            data = head + f.read()
    except OSError:
        return
    text = data.decode("utf-8-sig", errors="replace")
    for line, qwhere, q in onec_query.xml_queries(path, text):
        for k, n, mem in onec_query.object_refs(q):
            u.add_ref(owner_kind, owner_name, where + (" " + qwhere if qwhere else ""), "query", k, n, mem, rel, line)


def scan_rights(u, text, role, rel):
    for m in re.finditer(r"<object>\s*<name>([^<]+)</name>(.*?)</object>", text, re.DOTALL):
        rights = re.findall(r"<name>(\w+)</name>\s*<value>true</value>", m.group(2))
        if not rights:
            continue
        full = m.group(1)
        mm = DOTTED_RE.match(full)
        if mm:
            u.add_ref("Role", role, ",".join(rights)[:200], "rights", mm.group(1), mm.group(2), mm.group(3) or "", rel)


def scan_predefined(u, text, owner_kind, owner_name):
    names = re.findall(r"<Item[^>]*>\s*<Name>([^<]+)</Name>", text)
    if names:
        u.objects[0][7].append(("Predefined", "", ",".join(names[:200]), "", str(len(names)), ""))


def commit_unit(conn, u):
    cur = conn.cursor()
    for t in UNIT_TABLES:
        cur.execute("DELETE FROM %s WHERE unit=?" % t, (u.unit,))
    first_id = None
    for (k, name, parent, syn, uuid, file, props, members) in u.objects:
        cur.execute("INSERT INTO objects(unit,kind,name,name_lc,parent,synonym,syn_lc,uuid,file,props) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (u.unit, k, name, name.lower(), parent, syn, syn.lower(), uuid, file, props))
        oid = cur.lastrowid
        first_id = first_id or oid
        cur.executemany("INSERT INTO members VALUES(?,?,?,?,?,?,?,?,?)",
                        [(u.unit, oid, m[0], m[1], m[2], m[2].lower(), m[3], m[4], m[5]) for m in members])
    cur.executemany("INSERT INTO refs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(u.unit, k[0], k[1], k[2], k[3], k[4], v[0], k[5], k[6], k[6].lower(), v[1], ",".join(map(str, v[2])), k[7])
                     for k, v in u.refs.items()])
    cur.executemany("INSERT INTO modules VALUES(?,?,?,?,?,?,?,?)", [(u.unit,) + m for m in u.modules])
    cur.executemany("INSERT INTO procs VALUES(?,?,?,?,?,?,?,?,?,?,?)", [(u.unit,) + p for p in u.procs])
    cur.executemany("INSERT INTO errors VALUES(?,?,?)", [(u.unit, a, b) for a, b in u.errors])


def unit_signature(root, kind, name):
    n = sz = mt = 0
    x = os.path.join(root, KIND_DIR[kind], name + ".xml")
    try:
        st = os.stat(x)
        n, sz, mt = 1, st.st_size, st.st_mtime_ns
    except OSError:
        pass
    base = os.path.join(root, KIND_DIR[kind], name)
    for dp, _dn, fns in os.walk(base):
        for fn in fns:
            try:
                st = os.stat(os.path.join(dp, fn))
            except OSError:
                continue
            n += 1
            sz += st.st_size
            mt = max(mt, st.st_mtime_ns)
    return "%d:%d:%d" % (n, sz, mt)


def cmd_build(root, db, full=False):
    t0 = time.time()
    conn = open_db(db)
    if conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone() != (SCHEMA_VERSION,):
        full = True
    if full:
        for t in UNIT_TABLES + ("units",):
            conn.execute("DELETE FROM %s" % t)
    # корневые данные конфигурации
    cfg = os.path.join(root, "Configuration.xml")
    fmt = ""
    try:
        top = ET.parse(cfg).getroot()
        fmt = top.get("version", "")
        cprops = child(child(top, "Configuration"), "Properties")
        for key in ("Name", "Version", "Vendor", "CompatibilityMode", "ConfigurationExtensionCompatibilityMode", "DefaultRunMode"):
            z = child(cprops, key)
            if z is not None and z.text:
                conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("cfg." + key, z.text.strip()))
        conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("cfg.Synonym", ru_text(child(cprops, "Synonym"))))
    except Exception as ex:  # noqa: BLE001
        print("предупреждение: Configuration.xml не разобран: %s" % ex, file=sys.stderr)
    conn.execute("INSERT OR REPLACE INTO meta VALUES('format_version',?)", (fmt,))
    conn.execute("INSERT OR REPLACE INTO meta VALUES('schema',?)", (SCHEMA_VERSION,))
    conn.execute("INSERT OR REPLACE INTO meta VALUES('root',?)", (root,))

    # перечень юнитов
    units = []
    for d in sorted(os.listdir(root)):
        k = DIR_KIND.get(d)
        if not k or not os.path.isdir(os.path.join(root, d)):
            continue
        for fn in sorted(os.listdir(os.path.join(root, d))):
            if fn.endswith(".xml"):
                units.append((k, fn[:-4]))
    have = {r[0]: r[1] for r in conn.execute("SELECT unit,sig FROM units")}
    present = set("%s/%s" % (KIND_DIR[k], n) for k, n in units)
    for gone in set(have) - present:
        for t in UNIT_TABLES:
            conn.execute("DELETE FROM %s WHERE unit=?" % t, (gone,))
        conn.execute("DELETE FROM units WHERE unit=?", (gone,))
    changed = []
    for k, n in units:
        sig = unit_signature(root, k, n)
        if have.get("%s/%s" % (KIND_DIR[k], n)) != sig:
            changed.append((k, n, sig))
    # фаза 1: объекты (нужны для фильтра ссылок из кода). Берём имена из имён файлов.
    known = set((k, n.lower()) for k, n in units)
    cm_names = {n.lower(): n for k, n in units if k == "CommonModule"}
    done = 0
    for k, n, sig in changed:
        u = process_unit(conn, root, k, n, known, cm_names)
        commit_unit(conn, u)
        conn.execute("INSERT OR REPLACE INTO units VALUES(?,?)", (u.unit, sig))
        done += 1
        if done % 200 == 0:
            conn.commit()
            print("  обработано %d/%d объектов..." % (done, len(changed)), file=sys.stderr)
    conn.commit()
    tot = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
    errs = conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
    print("Индекс: %s" % db)
    print("Объектов в индексе: %d; пересобрано: %d из %d; ошибок разбора: %d; %.1f с"
          % (tot, done, len(units), errs, time.time() - t0))
    if errs:
        for r in conn.execute("SELECT file,msg FROM errors LIMIT 5"):
            print("  ошибка: %s: %s" % r)
    conn.close()
    return 0


# --------------------------------------------------------------------------------------
# Запросы
# --------------------------------------------------------------------------------------
class Out:
    def __init__(self, limit=150, offset=0):
        self.lines, self.limit, self.offset = [], limit, offset

    def p(self, s=""):
        self.lines.append(s)

    def flush(self):
        part = self.lines[self.offset:self.offset + self.limit]
        print("\n".join(part))
        rest = len(self.lines) - self.offset - len(part)
        if rest > 0:
            print("... ещё %d строк (--offset %d)" % (rest, self.offset + len(part)))


def resolve(conn, spec, kind_only=None):
    spec = spec.strip()
    kind = name = None
    if "." in spec:
        a, b = spec.split(".", 1)
        k = WORD_KIND.get(a.lower())
        if k:
            kind, name = k, b.split(".")[0]
    if name is None:
        name = spec
    rows = conn.execute("SELECT id,kind,name,synonym,file,props,parent FROM objects WHERE name_lc=?"
                        + (" AND kind=?" if kind else ""), (name.lower(),) + ((kind,) if kind else ())).fetchall()
    return rows


def pick(conn, spec):
    rows = resolve(conn, spec)
    if not rows:
        like = conn.execute("SELECT kind,name FROM objects WHERE name_lc LIKE ? ORDER BY kind,name LIMIT 15",
                            ("%" + spec.split(".")[-1].lower() + "%",)).fetchall()
        print("Объект не найден: %s" % spec)
        if like:
            print("Похожие: " + ", ".join("%s.%s" % r for r in like))
        sys.exit(3)
    if len(rows) > 1:
        print("Неоднозначно, уточните вид (например Справочник.Имя):")
        for r in rows:
            print("  %s.%s" % (r[1], r[2]))
        sys.exit(3)
    return rows[0]


def cmd_stats(conn):
    g = lambda k: (conn.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone() or ("",))[0]  # noqa: E731
    print("Конфигурация: %s «%s» %s" % (g("cfg.Name"), g("cfg.Synonym"), g("cfg.Version")))
    print("Формат выгрузки: %s; совместимость: %s; поставщик: %s" % (g("format_version"), g("cfg.CompatibilityMode"), g("cfg.Vendor")))
    print("Объекты по видам:")
    for k, n in conn.execute("SELECT kind,COUNT(*) FROM objects GROUP BY kind ORDER BY COUNT(*) DESC"):
        print("  %-28s %d" % (k, n))
    m = conn.execute("SELECT COUNT(*),SUM(lines),SUM(procs),SUM(exports) FROM modules").fetchone()
    print("Модулей: %s; строк BSL: %s; процедур/функций: %s (экспортных %s)" % tuple(x or 0 for x in m))
    ordf = conn.execute("SELECT COUNT(*) FROM members WHERE mkind='FormFile' AND types='ordinary'").fetchone()[0]
    mgf = conn.execute("SELECT COUNT(*) FROM members WHERE mkind='FormFile' AND types='managed'").fetchone()[0]
    print("Форм: управляемых %d, обычных %d" % (mgf, ordf))
    print("Связей (refs): %d" % conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0])
    return 0


def cmd_info(conn, spec, out):
    oid, kind, name, syn, file, props, parent = pick(conn, spec)
    full = "%s.%s" % (kind, name)
    out.p("%s  «%s»  (%s)" % (full, syn, file))
    pr = json.loads(props or "{}")
    keys = [k for k in ("Hierarchical", "HierarchyType", "CodeLength", "DescriptionLength", "Owners", "RegisterType",
                        "WriteMode", "InformationRegisterPeriodicity", "Posting", "RealTimePosting", "Server",
                        "ServerCall", "ClientManagedApplication", "Global", "Privileged", "ReturnValuesReuse",
                        "ExternalConnection", "Handler", "Use", "Predefined", "DataLockControlMode") if k in pr]
    if pr and keys:
        out.p("Свойства: " + "; ".join("%s=%s" % (k, pr[k]) for k in keys))
    mem = conn.execute("SELECT mkind,path,name,synonym,types,extra FROM members WHERE obj_id=? ORDER BY rowid", (oid,)).fetchall()
    groups = {}
    for m in mem:
        groups.setdefault(m[0], []).append(m)

    def line(m, indent="  "):
        s = indent + m[2]
        if m[4]:
            s += ": " + m[4]
        if m[3] and m[3] != m[2]:
            s += "  «%s»" % m[3]
        if m[5]:
            s += "  [%s]" % m[5]
        return s

    for mk, title in (("Dimension", "Измерения"), ("Resource", "Ресурсы"), ("Attribute", "Реквизиты"),
                      ("AddressingAttribute", "Реквизиты адресации"), ("AccountingFlag", "Признаки учета"),
                      ("Column", "Графы")):
        items = [m for m in groups.get(mk, []) if not m[1]]
        if items:
            out.p("%s (%d):" % (title, len(items)))
            for m in items:
                out.p(line(m))
    ts = groups.get("TabularSection", [])
    for t in ts:
        cols = [m for m in mem if m[1] == t[2] and m[0] == "Attribute"]
        out.p("Табличная часть %s (%d колонок)%s:" % (t[2], len(cols), "  «%s»" % t[3] if t[3] and t[3] != t[2] else ""))
        for m in cols:
            out.p(line(m, "    "))
    ev = groups.get("EnumValue", [])
    if ev:
        out.p("Значения (%d): %s" % (len(ev), ", ".join(m[2] for m in ev[:60]) + (" ..." if len(ev) > 60 else "")))
    pd = groups.get("Predefined", [])
    if pd:
        out.p("Предопределенные (%s): %s" % (pd[0][4], pd[0][2][:300]))
    ff = groups.get("FormFile", [])
    if ff:
        out.p("Формы (%d): %s" % (len(ff), ", ".join("%s[%s]" % (m[2], "обычная" if m[4] == "ordinary" else m[4]) for m in ff)))
    cm = groups.get("Command", [])
    if cm:
        out.p("Команды (%d): %s" % (len(cm), ", ".join(m[2] for m in cm)))
    tf = groups.get("TemplateFile", [])
    if tf:
        out.p("Макеты (%d): %s" % (len(tf), ", ".join("%s[%s]" % (m[2], m[4]) for m in tf)))
    for mk in ("Operation", "URLTemplate"):
        it = groups.get(mk, [])
        if it:
            out.p("%s (%d): %s" % (mk, len(it), ", ".join(m[2] for m in it[:30])))
    mods = conn.execute("SELECT mod,lines,procs,exports,file FROM modules WHERE owner_kind=? AND owner_name=? ORDER BY mod", (kind, name)).fetchall()
    if mods:
        out.p("Модули:")
        for m in mods:
            out.p("  %s: %d строк, %d процедур (экспорт %d)  %s" % m)
    if kind == "CommonModule":
        ex = conn.execute("SELECT name,is_func,params FROM procs WHERE owner_kind=? AND owner_name=? AND export=1 ORDER BY line LIMIT 80",
                          (kind, name)).fetchall()
        if ex:
            out.p("Экспортные методы (первые %d):" % len(ex))
            for n_, f_, p_ in ex:
                out.p("  %s %s(%s)" % ("Функция" if f_ else "Процедура", n_, p_))
    # движения документа
    if kind == "Document":
        rr = conn.execute("SELECT dst_kind,dst_name FROM refs WHERE src_kind=? AND src_name=? AND how='RegisterRecords' ORDER BY dst_kind,dst_name",
                          (kind, name)).fetchall()
        if rr:
            out.p("Движения (%d): %s" % (len(rr), ", ".join("%s.%s" % r for r in rr)))
    if kind == "Role":
        rr = conn.execute("SELECT dst_kind,dst_name,member,src_where FROM refs WHERE src_kind='Role' AND src_name=? AND how='rights' ORDER BY dst_kind,dst_name,member",
                          (name,)).fetchall()
        if rr:
            byk = {}
            for r in rr:
                byk[r[0]] = byk.get(r[0], 0) + 1
            out.p("Права (%d записей): %s" % (len(rr), ", ".join("%s %d" % kv for kv in sorted(byk.items(), key=lambda x: -x[1]))))
            for dk, dn, mem, rights in rr:
                out.p("  %s.%s%s: %s" % (dk, dn, "." + mem if mem else "", rights if len(rights) < 90 else rights[:87] + "..."))
    elif kind in ("Subsystem", "EventSubscription", "FunctionalOption", "FunctionalOptionsParameter", "DefinedType",
                  "CommonAttribute", "ScheduledJob", "SessionParameter", "Constant", "FilterCriterion", "DocumentJournal",
                  "CommandGroup", "CommonCommand"):
        rows = conn.execute("SELECT how,dst_kind,dst_name,member FROM refs WHERE src_kind=? AND src_name=? "
                            "AND how NOT IN ('code','FormXml','rights') ORDER BY how,dst_kind,dst_name", (kind, name)).fetchall()
        groups2 = {}
        for how, dk, dn, mem in rows:
            groups2.setdefault(how, []).append("%s.%s%s" % (dk, dn, "." + mem if mem else ""))
        for how, items in groups2.items():
            out.p("%s (%d): %s" % (how, len(items), _short(items, 40)))
        if kind == "Subsystem":
            subs = conn.execute("SELECT name FROM objects WHERE kind='Subsystem' AND parent=? ORDER BY name", (parent + "." + name if parent else name,)).fetchall()
            if subs:
                out.p("Вложенные подсистемы (%d): %s" % (len(subs), _short([x[0] for x in subs], 40)))
            if parent:
                out.p("Родитель: %s" % parent)
    # сводка использования
    cnt = conn.execute("SELECT src_kind,COUNT(DISTINCT src_name) FROM refs WHERE dst_kind=? AND dst_lc=? AND NOT (src_kind=? AND src_name=?) GROUP BY src_kind ORDER BY 2 DESC",
                       (kind, name.lower(), kind, name)).fetchall()
    if cnt:
        out.p("Используется (см. refs): " + ", ".join("%s: %d" % r for r in cnt))
    return 0


def _short(items, n=8):
    items = list(dict.fromkeys(items))
    s = ", ".join(items[:n])
    return s + (" +%d" % (len(items) - n) if len(items) > n else "")


def cmd_refs(conn, spec, out, kind_filter=None, member=None, detail=False):
    oid, kind, name, syn, file, props, parent = pick(conn, spec)
    q = ("SELECT src_kind,src_name,src_where,how,member,cnt,lines FROM refs WHERE dst_kind=? AND dst_lc=? "
         "AND NOT (src_kind=? AND src_name=?)")
    args = [kind, name.lower(), kind, name]
    if kind_filter:
        q += " AND src_kind=?"
        args.append(WORD_KIND.get(kind_filter.lower(), kind_filter))
    if member:
        q += " AND mem_lc=?"
        args.append(member.lower())
    q += " ORDER BY src_kind,src_name,src_where"
    rows = conn.execute(q, args).fetchall()
    srcs = {}
    for r in rows:
        srcs.setdefault((r[0], r[1]), []).append(r)
    kinds = {}
    for (sk, _sn) in srcs:
        kinds[sk] = kinds.get(sk, 0) + 1
    out.p("Использование %s.%s: %d объектов-источников, %d связей%s" % (
        kind, name, len(srcs), len(rows), " (участник %s)" % member if member else ""))
    out.p("По видам: " + ", ".join("%s %d" % kv for kv in sorted(kinds.items(), key=lambda x: -x[1])))
    cur = None
    for (sk, sn), rs in srcs.items():
        if sk != cur:
            cur = sk
            out.p("== %s (%d) ==" % (sk, kinds[sk]))
        if detail:
            for _k, _n, sw, how, mem, cnt, lines in rs:
                s = "  %s  [%s%s]" % (sn, how, ", " + sw if sw else "")
                if mem:
                    s += "  ." + mem
                if how == "code":
                    s += "  x%d%s" % (cnt, " стр.%s" % lines if lines else "")
                out.p(s)
            continue
        parts = []
        types = [r[2].replace("TabularSection:", "").replace("Attribute:", "").replace("/", ".") for r in rs if r[3] == "Type" and r[2]]
        if types:
            parts.append("тип в: " + _short(types))
        other = [r for r in rs if r[3] not in ("Type", "FillValue", "FormXml", "code", "rights", "query")]
        for how in dict.fromkeys(r[3] for r in other):
            w = [r[2] or "" for r in other if r[3] == how]
            parts.append(how + (": " + _short([x for x in w if x]) if any(w) else ""))
        fv = [r[2].replace("Attribute:", "") for r in rs if r[3] == "FillValue"]
        if fv:
            parts.append("значение заполнения: " + _short(fv, 4))
        qs = [r[2] for r in rs if r[3] == "query"]
        if qs:
            parts.append("запросы: " + _short(qs, 4))
        forms = [r[2][5:] for r in rs if r[3] == "FormXml"]
        if forms:
            parts.append("формы: " + _short(forms))
        code = {}
        for r in rs:
            if r[3] == "code":
                code[r[2]] = code.get(r[2], 0) + r[5]
        if code:
            parts.append("код: " + _short(["%s x%d" % kv for kv in code.items()]))
        rights = [r[2] for r in rs if r[3] == "rights"]
        if rights:
            parts.append("права: " + _short(rights, 3))
        out.p("  %s — %s" % (sn, "; ".join(parts)))
    return 0


def cmd_find(conn, text, out):
    t = text.lower()
    r = conn.execute("SELECT kind,name,synonym FROM objects WHERE name_lc LIKE ? OR syn_lc LIKE ? ORDER BY kind,name LIMIT 300",
                     ("%" + t + "%", "%" + t + "%")).fetchall()
    out.p("Объекты (%d):" % len(r))
    for k, n, s in r:
        out.p("  %s.%s%s" % (k, n, "  «%s»" % s if s else ""))
    r = conn.execute("SELECT o.kind,o.name,m.mkind,m.path,m.name,m.types FROM members m JOIN objects o ON o.id=m.obj_id "
                     "WHERE m.name_lc LIKE ? AND m.mkind NOT IN ('FormFile','TemplateFile','Predefined') ORDER BY o.kind,o.name LIMIT 300",
                     ("%" + t + "%",)).fetchall()
    out.p("Реквизиты/члены (%d):" % len(r))
    for k, n, mk, pth, mn, ty in r:
        out.p("  %s.%s %s%s: %s" % (k, n, (pth + "." if pth else ""), mn, ty))
    r = conn.execute("SELECT owner_kind,owner_name,mod,name,is_func,export,line FROM procs WHERE name_lc LIKE ? ORDER BY export DESC,owner_name LIMIT 300",
                     ("%" + t + "%",)).fetchall()
    out.p("Процедуры/функции (%d):" % len(r))
    for k, n, mod, nm, f, ex, ln in r:
        out.p("  %s.%s/%s: %s%s :%d" % (k, n, mod, nm, " Экспорт" if ex else "", ln))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Индекс выгрузки 1С (Конфигуратор)")
    ap.add_argument("cmd", choices=["build", "stats", "info", "refs", "find"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--root")
    ap.add_argument("--db")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--detail", action="store_true", help="refs: по одной строке на каждую связь")
    ap.add_argument("--kind")
    ap.add_argument("--member")
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--offset", type=int, default=0)
    a = ap.parse_args(argv)
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    root = find_root(a.root)
    db = a.db or os.path.join(root, ".onec", "index.sqlite")
    if a.cmd == "build":
        return cmd_build(root, db, a.full)
    if not os.path.isfile(db):
        sys.exit("Индекс не найден: %s. Сначала: onec_index.py build" % db)
    conn = open_db(db, create=False)
    out = Out(a.limit, a.offset)
    if a.cmd == "stats":
        rc = cmd_stats(conn)
        conn.close()
        return rc
    if not a.arg:
        sys.exit("Нужен аргумент (объект или подстрока)")
    rc = {"info": lambda: cmd_info(conn, a.arg, out),
          "refs": lambda: cmd_refs(conn, a.arg, out, a.kind, a.member, a.detail),
          "find": lambda: cmd_find(conn, a.arg, out)}[a.cmd]()
    out.flush()
    conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
