#!/usr/bin/env python3
"""onec_index - индекс выгрузки конфигурации 1С (Конфигуратор, XML) в SQLite.

Только стандартная библиотека, Python 3.9+. Запускать: python3 -I onec_index.py <команда> ...

Команды:
  build [--full] [--with КАТАЛОГ ...] [--no-extras]
                               построить/обновить индекс (инкрементально, по объектам);
                               --with подключает каталоги расширений и внешних обработок/отчётов
  stats                        сводка по конфигурации
  where                        где корень выгрузки и файл индекса
  info  <Объект>               компактная карточка объекта (реквизиты, формы, модули ...)
  refs  <Объект> [--kind K] [--member M] [--limit N] [--offset N]
                               где используется объект (типы, подсистемы, роли, модули ...)
  find  <подстрока> [--limit N]
                               поиск по именам объектов, реквизитов, процедур

Корень выгрузки: --root DIR, либо переменная ONEC_DUMP, либо ближайший родитель
текущей папки с Configuration.xml (или её подпапка cf, src/cf). Индекс — в папке проекта:
<проект>/.onec/index.sqlite (проект — папка над cf/src/cf, иначе сама выгрузка); ONEC_INDEX_DIR, --db.
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
import onec_paths  # noqa: E402
import hashlib  # noqa: E402

SCHEMA_VERSION = "7"

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
# внешние обработки и отчёты: выгружаются по одному, без каталога вида
EXTERNAL_KINDS = {"ExternalDataProcessor": "ВнешняяОбработка", "ExternalReport": "ВнешнийОтчет"}
KIND_DIR = {k: d for k, d, _, _, _ in KINDS}
KIND_RU = {k: s for k, _, s, _, _ in KINDS}
KIND_RU.update({"ExternalDataProcessor": "ВнешняяОбработка", "ExternalReport": "ВнешнийОтчет"})
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
for _k, _r in EXTERNAL_KINDS.items():
    WORD_KIND[_k.lower()] = _k
    WORD_KIND[_r.lower()] = _k
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
CREATE TABLE IF NOT EXISTS units(unit TEXT PRIMARY KEY, sig TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS objects(
  id INTEGER PRIMARY KEY, unit TEXT, kind TEXT, name TEXT, name_lc TEXT, parent TEXT,
  synonym TEXT, syn_lc TEXT, uuid TEXT, file TEXT, props TEXT, source TEXT, adopted INTEGER);
CREATE INDEX IF NOT EXISTS objects_name ON objects(name_lc, kind);
CREATE INDEX IF NOT EXISTS objects_unit ON objects(unit);
CREATE TABLE IF NOT EXISTS members(
  unit TEXT, obj_id INTEGER, mkind TEXT, path TEXT, name TEXT, name_lc TEXT, synonym TEXT, types TEXT, extra TEXT);
CREATE INDEX IF NOT EXISTS members_obj ON members(obj_id);
CREATE INDEX IF NOT EXISTS members_unit ON members(unit);
-- словарь повторяющихся строк: юниты, файлы, имена объектов и членов
CREATE TABLE IF NOT EXISTS strs(id INTEGER PRIMARY KEY, v TEXT UNIQUE, lc TEXT);
CREATE INDEX IF NOT EXISTS strs_lc ON strs(lc);
CREATE TABLE IF NOT EXISTS refs_t(
  uid INTEGER, src_kind TEXT, src_nid INTEGER, where_id INTEGER, how TEXT,
  dst_kind TEXT, dst_nid INTEGER, mem_nid INTEGER, cnt INTEGER, lines TEXT, fid INTEGER, src_source TEXT);
CREATE INDEX IF NOT EXISTS refs_dst ON refs_t(dst_nid, dst_kind);
CREATE INDEX IF NOT EXISTS refs_src ON refs_t(src_nid);
CREATE INDEX IF NOT EXISTS refs_uid ON refs_t(uid);
CREATE VIEW IF NOT EXISTS refs AS
  SELECT u.v AS unit, r.src_kind, sn.v AS src_name, w.v AS src_where, r.how, r.dst_kind,
         dn.v AS dst_name, dn.lc AS dst_lc, mn.v AS member, mn.lc AS mem_lc, r.cnt, r.lines, f.v AS file, r.src_source
  FROM refs_t r JOIN strs u ON u.id=r.uid JOIN strs sn ON sn.id=r.src_nid JOIN strs w ON w.id=r.where_id
       JOIN strs dn ON dn.id=r.dst_nid JOIN strs mn ON mn.id=r.mem_nid JOIN strs f ON f.id=r.fid;
CREATE TABLE IF NOT EXISTS modules(
  unit TEXT, owner_kind TEXT, owner_name TEXT, mod TEXT, file TEXT, lines INTEGER, procs INTEGER, exports INTEGER, source TEXT);
CREATE INDEX IF NOT EXISTS modules_owner ON modules(owner_kind, owner_name);
CREATE INDEX IF NOT EXISTS modules_unit ON modules(unit);
CREATE TABLE IF NOT EXISTS procs_t(
  uid INTEGER, owner_kind TEXT, owner_nid INTEGER, mod TEXT, name TEXT, name_lc TEXT,
  is_func INTEGER, export INTEGER, params TEXT, line INTEGER, fid INTEGER, source TEXT);
CREATE INDEX IF NOT EXISTS procs_owner ON procs_t(owner_nid);
CREATE INDEX IF NOT EXISTS procs_uid ON procs_t(uid);
CREATE VIEW IF NOT EXISTS procs AS
  SELECT u.v AS unit, p.owner_kind, o.v AS owner_name, p.mod, p.name, p.name_lc, p.is_func, p.export, p.params,
         p.line, f.v AS file, p.source
  FROM procs_t p JOIN strs u ON u.id=p.uid JOIN strs o ON o.id=p.owner_nid JOIN strs f ON f.id=p.fid;
CREATE TABLE IF NOT EXISTS errors(unit TEXT, file TEXT, msg TEXT);
CREATE TABLE IF NOT EXISTS sources(source TEXT PRIMARY KEY, kind TEXT, path TEXT, name TEXT, info TEXT);
"""
UNIT_TABLES = ("objects", "members", "modules", "errors")      # строки юнита по столбцу unit
UNIT_ID_TABLES = ("refs_t", "procs_t")                           # строки юнита по uid из strs


class Strs:
    """Словарь строк: одна копия каждой строки в strs, в таблицах — её id."""

    def __init__(self, conn):
        self.conn = conn
        self.ids = {v: i for i, v in conn.execute("SELECT id,v FROM strs")}

    def id(self, v):
        v = v or ""
        i = self.ids.get(v)
        if i is None:
            i = self.conn.execute("INSERT INTO strs(v,lc) VALUES(?,?)", (v, v.lower())).lastrowid
            self.ids[v] = i
        return i

    def get(self, v):
        return self.ids.get(v or "")


def delete_unit(conn, strs, unit):
    for t in UNIT_TABLES:
        conn.execute("DELETE FROM %s WHERE unit=?" % t, (unit,))
    uid = strs.get(unit)
    if uid is not None:
        for t in UNIT_ID_TABLES:
            conn.execute("DELETE FROM %s WHERE uid=?" % t, (uid,))


def compact(conn):
    """Убрать строки словаря без ссылок; VACUUM, если свободных страниц много."""
    conn.execute("DELETE FROM strs WHERE id NOT IN ("
                 "SELECT uid FROM refs_t UNION SELECT src_nid FROM refs_t UNION SELECT where_id FROM refs_t "
                 "UNION SELECT dst_nid FROM refs_t UNION SELECT mem_nid FROM refs_t UNION SELECT fid FROM refs_t "
                 "UNION SELECT uid FROM procs_t UNION SELECT owner_nid FROM procs_t UNION SELECT fid FROM procs_t)")
    conn.commit()
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    if pages and free * 4 > pages:
        conn.execute("VACUUM")


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
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall():
            conn.execute("DROP VIEW IF EXISTS %s" % t)
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            conn.execute("DROP TABLE IF EXISTS %s" % t)
        conn.commit()
    conn.executescript(DDL)
    return conn


def find_root(arg):
    r = onec_paths.find_root(arg or os.environ.get("ONEC_DUMP") or os.getcwd())
    if r is None:
        sys.exit("Не найден Configuration.xml (корень выгрузки). Укажите --root или ONEC_DUMP.")
    return r


# --------------------------------------------------------------------------------------
# Извлечение
# --------------------------------------------------------------------------------------
class Unit:
    """Накопитель строк одного объекта верхнего уровня (юнита)."""

    def __init__(self, unit, source=""):
        self.unit = unit
        self.source = source
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
                ob = child(mp, "ObjectBelonging")
                if ob is not None and (ob.text or "").strip() == "Adopted":
                    extra.append("adopted")
                for fl in ("Indexing", "Use", "FillChecking", "Format"):
                    z = child(mp, fl)
                    if z is not None and z.text and z.text.strip() and z.text.strip() not in ("DontCheck", "DontIndex", "ForItem"):
                        if fl in ("Indexing", "FillChecking"):
                            extra.append("%s=%s" % (fl, z.text.strip()))
                if extra and extra[0] == "adopted":
                    extra = ["adopted"]
                members.append((mk, "", mname, ru_text(child(mp, "Synonym")), type_str(child(mp, "Type")), ";".join(extra)))
                if mk == "TabularSection":
                    sub = child(m, "ChildObjects")
                    if sub is not None:
                        for a in sub:
                            ap = child(a, "Properties")
                            if ap is None:
                                continue
                            an = child(ap, "Name")
                            aob = child(ap, "ObjectBelonging")
                            members.append((lname(a), mname, (an.text or "") if an is not None else "",
                                            ru_text(child(ap, "Synonym")), type_str(child(ap, "Type")),
                                            "adopted" if aob is not None and (aob.text or "").strip() == "Adopted" else ""))
            else:
                members.append((mk, "", (m.text or "").strip(), "", "", ""))
    u.objects.append((kind, name, parent, synonym, obj.get("uuid", ""), rel.replace(os.sep, "/"), json.dumps(scalar, ensure_ascii=False), members))
    # ссылки
    walk_refs(u, props, kind, name, "", "", rel)
    if co is not None:
        for m in co:
            walk_refs(u, m, kind, name, "", "", rel)
    return name


INTERCEPT_RE = re.compile(r'^[ \t]*&(Перед|После|Вместо|ИзменениеИКонтроль|Before|After|Around|ChangeAndValidate)[ \t]*\([ \t]*"(%s)"' % IDENT,
                          re.IGNORECASE | re.MULTILINE)


def scan_module(u, known, cm_names, text, owner_kind, owner_name, mod, rel):
    """Процедуры + ссылки на объекты/общие модули внутри BSL-текста."""
    if u.source.startswith("ext:"):
        # перехват метода заимствованного объекта: &После("ПередЗаписью") и т.п.
        for m in INTERCEPT_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            u.add_ref(owner_kind, owner_name, "%s &%s" % (mod, m.group(1)), "intercept", owner_kind, owner_name,
                      m.group(2), rel, line)
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


def process_unit(conn, root, kind, name, known, cm_names, unit=None, rel_xml=None, base_rel=None, source=""):
    """Один объект верхнего уровня. root — корень своего источника (конфигурация,
    расширение, каталог внешних обработок); пути в результатах — относительно root."""
    base_rel = base_rel or "%s/%s" % (KIND_DIR[kind], name)
    unit = unit or base_rel
    u = Unit(unit, source)
    rel_xml = rel_xml or base_rel + ".xml"
    base = os.path.join(root, *base_rel.split("/"))
    parsed = parse_object(u, root, rel_xml, kind)
    if parsed and kind in EXTERNAL_KINDS:
        name = parsed

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
        walk_sub(base, base_rel, name)
    if kind == "CommonForm" and os.path.isdir(base):
        process_form(base, kind, name, base_rel, name)
    if os.path.isdir(base):
        process_dir(base, kind, name, base_rel)
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


def _source_prefix(src_root, root):
    """Путь источника относительно основной выгрузки (../alfa_extensions/...) или полный,
    если источник далеко или на другом диске."""
    try:
        rel = os.path.relpath(src_root, root).replace(os.sep, "/")
    except ValueError:
        rel = None
    if rel is None or rel.split("/").count("..") > 2:
        rel = os.path.abspath(src_root).replace(os.sep, "/")
    return rel


def _pre(prefix, f):
    return (prefix + "/" + f) if prefix and f else f


def commit_unit(conn, u, prefix="", strs=None):
    """prefix — путь корня источника относительно корня основной конфигурации."""
    strs = strs or Strs(conn)
    cur = conn.cursor()
    delete_unit(conn, strs, u.unit)
    for (k, name, parent, syn, uuid, file, props, members) in u.objects:
        adopted = 1 if json.loads(props or "{}").get("ObjectBelonging") == "Adopted" else 0
        cur.execute("INSERT INTO objects(unit,kind,name,name_lc,parent,synonym,syn_lc,uuid,file,props,source,adopted) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (u.unit, k, name, name.lower(), parent, syn, syn.lower(), uuid, _pre(prefix, file), props, u.source, adopted))
        oid = cur.lastrowid
        cur.executemany("INSERT INTO members VALUES(?,?,?,?,?,?,?,?,?)",
                        [(u.unit, oid, m[0], m[1], m[2], m[2].lower(), m[3], m[4], m[5]) for m in members])
    sid = strs.id
    uid = sid(u.unit)
    cur.executemany("INSERT INTO refs_t VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(uid, k[0], sid(k[1]), sid(k[2]), k[3], k[4], sid(v[0]), sid(k[6]), v[1], ",".join(map(str, v[2])),
                      sid(_pre(prefix, k[7])), u.source) for k, v in u.refs.items()])
    cur.executemany("INSERT INTO modules VALUES(?,?,?,?,?,?,?,?,?)",
                    [(u.unit,) + m[:3] + (_pre(prefix, m[3]),) + m[4:] + (u.source,) for m in u.modules])
    cur.executemany("INSERT INTO procs_t VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(uid, p_[0], sid(p_[1])) + p_[2:9] + (sid(_pre(prefix, p_[9])), u.source) for p_ in u.procs])
    cur.executemany("INSERT INTO errors VALUES(?,?,?)", [(u.unit, _pre(prefix, a_), b_) for a_, b_ in u.errors])


def unit_signature(xml_path, base):
    """Быстрый отпечаток объекта: число, размеры, даты и имена файлов.

    Имена входят в отпечаток: переименование файла (ObjectModule.bsl -> ManagerModule.bsl)
    не меняет ни размер, ни дату, но меняет смысл.
    """
    n = sz = mt = 0
    names = hashlib.blake2b(digest_size=8)
    try:
        st = os.stat(xml_path)
        n, sz, mt = 1, st.st_size, st.st_mtime_ns
    except OSError:
        pass
    for dp, dn, fns in os.walk(base):
        dn.sort()
        for fn in sorted(fns):
            try:
                st = os.stat(os.path.join(dp, fn))
            except OSError:
                continue
            n += 1
            sz += st.st_size
            mt = max(mt, st.st_mtime_ns)
            names.update(os.path.relpath(os.path.join(dp, fn), base).replace(os.sep, "/").encode("utf-8") + b"\0")
    return "%d:%d:%d:%s" % (n, sz, mt, names.hexdigest())


def unit_text_files(base):
    """Файлы объекта, где может быть код: модули BSL и Form.bin обычных форм."""
    for dp, _dn, fns in os.walk(base):
        for fn in fns:
            if fn.endswith(".bsl") or fn == "Form.bin":
                yield os.path.join(dp, fn)


def units_mentioning(units, names, skip):
    """Юниты, в модулях которых встречается одно из имён (без учёта регистра).

    Ложные совпадения (имя внутри другого слова) безопасны: юнит просто разбирается заново.
    """
    if not names:
        return set()
    rx = re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)))
    hit = set()
    for x in units:
        if x[0] in skip:
            continue
        base = os.path.join(x[2], *x[6].split("/"))
        for f in unit_text_files(base):
            try:
                with open(f, "rb") as fh:
                    text = fh.read().decode("utf-8", errors="ignore").lower()
            except OSError:
                continue
            if rx.search(text):
                hit.add(x[0])
                break
    return hit


def unit_hash(xml_path, base):
    """Хеш содержимого файлов объекта: перевыгрузка без изменений не требует разбора."""
    h = hashlib.blake2b(digest_size=16)
    files = [xml_path] if os.path.isfile(xml_path) else []
    for dp, dn, fns in os.walk(base):
        dn.sort()
        files.extend(os.path.join(dp, fn) for fn in sorted(fns))
    for f in files:
        h.update(os.path.relpath(f, os.path.dirname(xml_path)).replace(os.sep, "/").encode("utf-8") + b"\0")
        try:
            with open(f, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            pass
    return h.hexdigest()


def _config_info(path):
    """Configuration.xml -> (имя, это_расширение, префикс, свойства)."""
    try:
        top = ET.parse(path).getroot()
    except Exception:  # noqa: BLE001
        return None, False, "", {}
    cprops = child_props(top, "Configuration")
    if cprops is None:
        return None, False, "", {}
    props = {}
    for key in ("Name", "Version", "Vendor", "CompatibilityMode", "ConfigurationExtensionCompatibilityMode",
                "DefaultRunMode", "ConfigurationExtensionPurpose", "NamePrefix"):
        z = child(cprops, key)
        if z is not None and z.text:
            props[key] = z.text.strip()
    props["Synonym"] = ru_text(child(cprops, "Synonym"))
    props["format_version"] = top.get("version", "")
    return props.get("Name"), "ConfigurationExtensionPurpose" in props, props.get("NamePrefix", ""), props


def _external_kind(path):
    try:
        with open(path, "rb") as f:
            head = f.read(4096).decode("utf-8", errors="ignore")
    except OSError:
        return None
    for k in EXTERNAL_KINDS:
        if "<%s " % k in head or "<%s>" % k in head:
            return k
    return None


def discover(main_root, extras):
    """-> список юнитов [(unit, source, src_root, kind, name, rel_xml, base_rel)] и описания источников."""
    units, sources = [], []

    def config_units(src_root, source, prefix):
        for d in sorted(os.listdir(src_root)):
            k = DIR_KIND.get(d)
            if not k or not os.path.isdir(os.path.join(src_root, d)):
                continue
            for fn in sorted(os.listdir(os.path.join(src_root, d))):
                if fn.endswith(".xml"):
                    base_rel = "%s/%s" % (d, fn[:-4])
                    units.append((prefix + base_rel, source, src_root, k, fn[:-4], base_rel + ".xml", base_rel))

    config_units(main_root, "", "")
    seen = set()
    ext_dirs = {}                 # имя расширения -> каталог (одно расширение — один источник)
    labels = set()                # метка подключённого каталога в ключе внешних объектов
    for extra in extras:
        ex = os.path.abspath(extra)
        if not os.path.isdir(ex):
            print("предупреждение: нет каталога %s" % extra, file=sys.stderr)
            continue
        # Ключ внешнего объекта: <метка каталога>/<путь внутри каталога>. Метка — имя каталога,
        # при совпадении имён у разных каталогов — с номером, чтобы объекты не вытесняли друг друга.
        label = os.path.basename(ex.rstrip("\\/")) or "with"
        n = 2
        while label.lower() in labels:
            label = "%s~%d" % (os.path.basename(ex.rstrip("\\/")) or "with", n)
            n += 1
        labels.add(label.lower())
        for dp, dn, fns in os.walk(ex):
            depth = os.path.relpath(dp, ex).count(os.sep) + (0 if dp == ex else 1)
            if "Configuration.xml" in fns:
                name, is_ext, _prefix, props = _config_info(os.path.join(dp, "Configuration.xml"))
                if name and is_ext and dp not in seen and name.lower() in ext_dirs:
                    print("предупреждение: расширение %s найдено дважды: %s и %s; второе не подключено"
                          % (name, ext_dirs[name.lower()], dp), file=sys.stderr)
                elif name and is_ext and dp not in seen:
                    seen.add(dp)
                    ext_dirs[name.lower()] = dp
                    source = "ext:" + name
                    sources.append((source, "extension", dp, name, json.dumps(props, ensure_ascii=False)))
                    config_units(dp, source, source + "/")
                dn[:] = []
                continue
            for fn in sorted(fns):
                if not fn.endswith(".xml"):
                    continue
                k = _external_kind(os.path.join(dp, fn))
                if k:
                    rel_dir = os.path.relpath(dp, ex).replace(os.sep, "/")
                    rel_dir = "" if rel_dir == "." else rel_dir + "/"
                    base_rel = rel_dir + fn[:-4]
                    source = "%s:%s/%s" % ("epf" if k == "ExternalDataProcessor" else "erf", label, base_rel)
                    sources.append((source, k, ex, fn[:-4], ""))
                    units.append((source, source, ex, k, fn[:-4], base_rel + ".xml", base_rel))
            dn[:] = [d for d in dn if depth < 3 and not d.startswith(".")
                     and not os.path.isfile(os.path.join(dp, d + ".xml"))]
    return units, sources


def cmd_build(root, db, full=False, extras=None):
    t0 = time.time()
    conn = open_db(db)
    if conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone() != (SCHEMA_VERSION,):
        full = True
    if extras is None:
        row = conn.execute("SELECT value FROM meta WHERE key='extras'").fetchone()
        extras = json.loads(row[0]) if row else []
    else:
        extras = [os.path.abspath(e) for e in extras]
    if full:
        for t in UNIT_TABLES + UNIT_ID_TABLES + ("strs", "units", "sources"):
            conn.execute("DELETE FROM %s" % t)
    # корневые данные конфигурации
    name, _is_ext, _pfx, props = _config_info(os.path.join(root, "Configuration.xml"))
    if name is None:
        print("предупреждение: Configuration.xml не разобран", file=sys.stderr)
    for key in ("Name", "Version", "Vendor", "CompatibilityMode", "ConfigurationExtensionCompatibilityMode", "DefaultRunMode", "Synonym"):
        if props.get(key):
            conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("cfg." + key, props[key]))
    conn.execute("INSERT OR REPLACE INTO meta VALUES('format_version',?)", (props.get("format_version", ""),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES('schema',?)", (SCHEMA_VERSION,))
    conn.execute("INSERT OR REPLACE INTO meta VALUES('root',?)", (root,))
    conn.execute("INSERT OR REPLACE INTO meta VALUES('extras',?)", (json.dumps(extras, ensure_ascii=False),))

    units, sources = discover(root, extras)
    conn.execute("DELETE FROM sources")
    conn.executemany("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?)", sources)
    have = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT unit,sig,hash FROM units")}
    present = set(x[0] for x in units)
    strs = Strs(conn)
    gone_units = set(have) - present
    for gone in gone_units:
        delete_unit(conn, strs, gone)
        conn.execute("DELETE FROM units WHERE unit=?", (gone,))
    changed = []
    same_content = 0
    for x in units:
        unit, _source, src_root, _k, _n, rel_xml, base_rel = x
        xml_p, base_p = os.path.join(src_root, *rel_xml.split("/")), os.path.join(src_root, *base_rel.split("/"))
        sig = unit_signature(xml_p, base_p)
        old = have.get(unit)
        if old and old[0] == sig:
            continue
        hsh = unit_hash(xml_p, base_p)
        if old and old[1] == hsh:
            # перевыгрузка: даты файлов новые, содержимое то же
            conn.execute("UPDATE units SET sig=? WHERE unit=?", (sig, unit))
            same_content += 1
            continue
        changed.append(x + (sig, hsh))
    # объекты всех источников нужны для фильтра ссылок из кода (имена берём из имён файлов)
    known = set((x[3], x[4].lower()) for x in units if x[3] not in EXTERNAL_KINDS)
    cm_names = {x[4].lower(): x[4] for x in units if x[3] == "CommonModule"}
    # Ссылки из кода зависят от состава объектов. Если объект появился, пропал или общий
    # модуль сменил написание имени, неизменённые модули, где он упоминается, разбираются заново —
    # иначе частичная сборка разойдётся с полной.
    cur_names = sorted("%s|%s" % (x[3], x[4]) for x in units if x[3] not in EXTERNAL_KINDS)
    row = conn.execute("SELECT value FROM meta WHERE key='known'").fetchone()
    prev_names = set(json.loads(row[0])) if row and not full else set(cur_names)
    diff = prev_names.symmetric_difference(cur_names)
    rescan = 0
    if diff:
        names = {d.split("|", 1)[1].lower() for d in diff}
        taken = {c[0] for c in changed}
        affected = set()
        for d in diff:
            k, n = d.split("|", 1)
            affected.update(r[0] for r in conn.execute(
                "SELECT DISTINCT unit FROM refs WHERE how='code' AND dst_kind=? AND dst_lc=?", (k, n.lower())))
        affected |= units_mentioning(units, names, taken | affected)
        by_unit = {x[0]: x for x in units}
        for unit in sorted(affected - taken):
            x = by_unit.get(unit)
            if x is None:
                continue
            xml_p, base_p = os.path.join(x[2], *x[5].split("/")), os.path.join(x[2], *x[6].split("/"))
            changed.append(x + (unit_signature(xml_p, base_p), unit_hash(xml_p, base_p)))
            rescan += 1
    conn.execute("INSERT OR REPLACE INTO meta VALUES('known',?)", (json.dumps(cur_names, ensure_ascii=False),))
    done = 0
    for unit, source, src_root, k, n, rel_xml, base_rel, sig, hsh in changed:
        u = process_unit(conn, src_root, k, n, known, cm_names, unit=unit, rel_xml=rel_xml, base_rel=base_rel, source=source)
        prefix = "" if src_root == root else _source_prefix(src_root, root)
        commit_unit(conn, u, prefix, strs)
        conn.execute("INSERT OR REPLACE INTO units VALUES(?,?,?)", (unit, sig, hsh))
        done += 1
        if done % 200 == 0:
            conn.commit()
            print("  обработано %d/%d объектов..." % (done, len(changed)), file=sys.stderr)
    conn.commit()
    if done or gone_units:
        compact(conn)
    tot = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
    errs = conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
    print("Индекс: %s" % db)
    print("Объектов в индексе: %d; пересобрано: %d из %d; ошибок разбора: %d; %.1f с"
          % (tot, done, len(units), errs, time.time() - t0))
    if same_content:
        print("Без изменений содержимого (только новые даты файлов): %d" % same_content)
    if rescan:
        print("Перечитано из-за появления или удаления объектов: %d" % rescan)
    if sources:
        print("Дополнительные источники: " + ", ".join(src[0] for src in sources))
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
    rows = conn.execute("SELECT id,kind,name,synonym,file,props,parent,source,adopted FROM objects WHERE name_lc=?"
                        + (" AND kind=?" if kind else "") + " ORDER BY source='' DESC, adopted, source",
                        (name.lower(),) + ((kind,) if kind else ())).fetchall()
    return rows


def src_label(ss):
    if not ss:
        return ""
    if ss.startswith("ext:"):
        return "расширение " + ss[4:]
    if ss.startswith("epf:"):
        return "внешняя обработка, файл %s.xml" % ss[4:]
    if ss.startswith("erf:"):
        return "внешний отчёт, файл %s.xml" % ss[4:]
    return ss


def pick(conn, spec):
    rows = resolve(conn, spec)
    if not rows:
        like = conn.execute("SELECT kind,name FROM objects WHERE name_lc LIKE ? ORDER BY kind,name LIMIT 15",
                            ("%" + spec.split(".")[-1].lower() + "%",)).fetchall()
        print("Объект не найден: %s" % spec)
        if like:
            print("Похожие: " + ", ".join("%s.%s" % r for r in like))
        sys.exit(3)
    if len(set(r[1] for r in rows)) > 1:
        print("Неоднозначно, уточните вид (например Справочник.Имя):")
        for r in rows:
            print("  %s.%s%s" % (r[1], r[2], "  [%s]" % src_label(r[7]) if r[7] else ""))
        sys.exit(3)
    # один вид: основная конфигурация, иначе собственный объект расширения/внешний объект
    return rows[0][:7]


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
    srcs = conn.execute("SELECT s.source,s.path,(SELECT COUNT(*) FROM objects o WHERE o.source=s.source),"
                        "(SELECT COUNT(*) FROM objects o WHERE o.source=s.source AND o.adopted=1),"
                        "(SELECT o.kind||'.'||o.name FROM objects o WHERE o.source=s.source LIMIT 1) FROM sources s ORDER BY s.source").fetchall()
    if srcs:
        print("Дополнительные источники (%d):" % len(srcs))
        for ss, path, n, ad, nm in srcs:
            if ss.startswith("ext:"):
                print("  %s: объектов %d%s  (%s)" % (src_label(ss), n, ", заимствовано %d" % ad if ad else "", path))
            else:
                print("  %s — %s  (%s)" % (nm or "?", src_label(ss), path))
    else:
        print("Расширения и внешние обработки не подключены (build --with <каталог>).")
    return 0


def cmd_info(conn, spec, out):
    oid, kind, name, syn, file, props, parent = pick(conn, spec)
    full = "%s.%s" % (kind, name)
    own_src = conn.execute("SELECT source FROM objects WHERE id=?", (oid,)).fetchone()[0]
    out.p("%s  «%s»  (%s)%s" % (full, syn, file, "  [%s]" % src_label(own_src) if own_src else ""))
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
    mods = conn.execute("SELECT mod,lines,procs,exports,file,source FROM modules WHERE owner_kind=? AND owner_name=? "
                        "ORDER BY source='' DESC, source, mod", (kind, name)).fetchall()
    if mods:
        out.p("Модули:")
        for m in mods:
            out.p("  %s: %d строк, %d процедур (экспорт %d)  %s%s" % (m[:5] + ("  [%s]" % src_label(m[5]) if m[5] else "",)))
    # что добавили и перехватили расширения в этом объекте
    for eid, esrc in conn.execute("SELECT id,source FROM objects WHERE kind=? AND name_lc=? AND source<>'' ORDER BY source",
                                  (kind, name.lower())).fetchall():
        em = [] if eid == oid else conn.execute("SELECT mkind,path,name,types FROM members WHERE obj_id=? AND extra NOT LIKE 'adopted%' "
                          "AND mkind NOT IN ('Predefined')", (eid,)).fetchall()
        parts = []
        by = {}
        for mk, pth, nm, ty in em:
            if mk == "FormFile":
                by.setdefault("формы", []).append(nm)
            elif mk == "TemplateFile":
                by.setdefault("макеты", []).append(nm)
            else:
                by.setdefault("реквизиты и прочее", []).append(("%s.%s" % (pth, nm) if pth else nm) + (": " + ty if ty else ""))
        for k_, v_ in by.items():
            parts.append("%s: %s" % (k_, _short(v_, 12)))
        ic = conn.execute("SELECT src_where,member FROM refs WHERE src_kind=? AND src_name=? AND src_source=? AND how='intercept' "
                          "ORDER BY src_where,member", (kind, name, esrc)).fetchall()
        if ic:
            parts.append("перехваты: " + _short(["%s(«%s»)" % (w_, m_) for w_, m_ in ic], 12))
        if eid == oid and not parts:
            continue
        lab_ = src_label(esrc)
        out.p("%s: %s" % (lab_[:1].upper() + lab_[1:], "; ".join(parts) if parts else "заимствован без изменений структуры"))
    if kind == "CommonModule":
        ex = conn.execute("SELECT name,is_func,params FROM procs WHERE owner_kind=? AND owner_name=? AND export=1 ORDER BY source='' DESC, line LIMIT 80",
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
    cnt = conn.execute("SELECT src_kind,COUNT(DISTINCT src_name||'|'||src_source) FROM refs WHERE dst_kind=? AND dst_lc=? "
                       "AND NOT (src_kind=? AND src_name=? AND src_source='') AND how<>'intercept' GROUP BY src_kind ORDER BY 2 DESC",
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
    q = ("SELECT src_kind,src_name,src_where,how,member,cnt,lines,src_source FROM refs WHERE dst_kind=? AND dst_lc=? "
         "AND NOT (src_kind=? AND src_name=? AND src_source='')")
    args = [kind, name.lower(), kind, name]
    if kind_filter:
        q += " AND src_kind=?"
        args.append(WORD_KIND.get(kind_filter.lower(), kind_filter))
    if member:
        q += " AND mem_lc=?"
        args.append(member.lower())
    q += " ORDER BY src_kind,src_source<>'',src_source,src_name,src_where"
    rows = conn.execute(q, args).fetchall()
    srcs = {}
    for r in rows:
        srcs.setdefault((r[0], r[1], r[7]), []).append(r)
    kinds = {}
    for (sk, _sn, _ss) in srcs:
        kinds[sk] = kinds.get(sk, 0) + 1
    ext_n = len(set(k[2] for k in srcs if k[2]))
    out.p("Использование %s.%s: %d объектов-источников, %d связей%s" % (
        kind, name, len(srcs), len(rows), " (участник %s)" % member if member else ""))
    out.p("По видам: " + ", ".join("%s %d" % kv for kv in sorted(kinds.items(), key=lambda x: -x[1]))
          + ("; из расширений и внешних объектов: %d" % sum(1 for k in srcs if k[2]) if ext_n else ""))
    cur = None
    for (sk, sn, ss), rs in srcs.items():
        lab = "  [%s]" % src_label(ss) if ss else ""
        if sk != cur:
            cur = sk
            out.p("== %s (%d) ==" % (sk, kinds[sk]))
        if detail:
            for _k, _n, sw, how, mem, cnt, lines, _ss in rs:
                s = "  %s%s  [%s%s]" % (sn, lab, how, ", " + sw if sw else "")
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
        other = [r for r in rs if r[3] not in ("Type", "FillValue", "FormXml", "code", "rights", "query", "intercept")]
        ic = ["%s(«%s»)" % (r[2], r[4]) for r in rs if r[3] == "intercept"]
        if ic:
            parts.append("перехват: " + _short(ic, 6))
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
        out.p("  %s%s — %s" % (sn, lab, "; ".join(parts)))
    return 0


def cmd_find(conn, text, out):
    t = text.lower()
    r = conn.execute("SELECT kind,name,synonym,source,adopted FROM objects WHERE (name_lc LIKE ? OR syn_lc LIKE ?) "
                     "AND NOT (adopted=1) ORDER BY kind,name LIMIT 300",
                     ("%" + t + "%", "%" + t + "%")).fetchall()
    out.p("Объекты (%d):" % len(r))
    for k, n, s, ss, _ad in r:
        out.p("  %s.%s%s%s" % (k, n, "  «%s»" % s if s else "", "  [%s]" % src_label(ss) if ss else ""))
    r = conn.execute("SELECT o.kind,o.name,m.mkind,m.path,m.name,m.types,o.source FROM members m JOIN objects o ON o.id=m.obj_id "
                     "WHERE m.name_lc LIKE ? AND m.mkind NOT IN ('FormFile','TemplateFile','Predefined') "
                     "AND m.extra NOT LIKE 'adopted%' ORDER BY o.kind,o.name LIMIT 300",
                     ("%" + t + "%",)).fetchall()
    out.p("Реквизиты/члены (%d):" % len(r))
    for k, n, mk, pth, mn, ty, ss in r:
        out.p("  %s.%s %s%s: %s%s" % (k, n, (pth + "." if pth else ""), mn, ty, "  [%s]" % src_label(ss) if ss else ""))
    r = conn.execute("SELECT owner_kind,owner_name,mod,name,is_func,export,line,source FROM procs WHERE name_lc LIKE ? "
                     "ORDER BY export DESC,owner_name LIMIT 300", ("%" + t + "%",)).fetchall()
    out.p("Процедуры/функции (%d):" % len(r))
    for k, n, mod, nm, f, ex, ln, ss in r:
        out.p("  %s.%s/%s: %s%s :%d%s" % (k, n, mod, nm, " Экспорт" if ex else "", ln, "  [%s]" % src_label(ss) if ss else ""))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Индекс выгрузки 1С (Конфигуратор)")
    ap.add_argument("cmd", choices=["build", "stats", "info", "refs", "find", "where"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--root")
    ap.add_argument("--db")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--with", dest="with_", action="append", metavar="КАТАЛОГ",
                    help="build: подключить каталог расширений или внешних обработок/отчётов (можно несколько; запоминается)")
    ap.add_argument("--no-extras", action="store_true", help="build: отключить ранее подключённые каталоги")
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
    db = a.db or onec_paths.default_db(root)
    if not a.db and a.cmd == "build":
        for old in onec_paths.old_locations(root):
            if os.path.exists(old) and os.path.abspath(old) != os.path.abspath(db) and not db.startswith(old):
                print("Прежнее место индекса больше не используется, его можно удалить: %s" % old, file=sys.stderr)
    if a.cmd == "where":
        print("Корень выгрузки: %s\nИндекс: %s%s" % (root, db, "" if os.path.isfile(db) else " (ещё не построен)"))
        return 0
    if a.cmd == "build":
        extras = [] if a.no_extras else a.with_
        return cmd_build(root, db, a.full, extras)
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
