#!/usr/bin/env python3
"""onec_query - запросы 1С в выгрузке: извлечение, сверка с метаданными, проверки.

Только стандартная библиотека, Python 3.9+.

  scan <путь> [--root R] [--rule R] [--errors] [--limit N] [--offset N]
        найти запросы в модулях (.bsl), схемах СКД (Template.xml) и динамических списках
        (Form.xml) внутри пути и проверить их. Путь — файл, папка объекта или весь корень.
  show <файл> [--line N] [--root R]
        показать запросы файла (или запрос, начинающийся ближе всего к строке N)
        с таблицами, псевдонимами и временными таблицами.

Проверки (код правила - смысл):
  unknown-table   таблица (объект метаданных) отсутствует в конфигурации            ошибка
  unknown-field   поле отсутствует у таблицы/табличной части/ссылочного типа         ошибка
  vt-where        отбор по измерению виртуальной таблицы в ГДЕ, а не в параметрах   #std657
  query-in-loop   выполнение запроса внутри цикла                                    #std436
  full-join       ПОЛНОЕ СОЕДИНЕНИЕ                                                  #std435

Сверка с метаданными требует индекса (onec_index.py build). Без индекса выполняются
только проверки, не зависящие от метаданных. Анализ лексический: запросы, собранные
из частей во время выполнения (СтрЗаменить, конкатенация переменных), проверяются
по видимому тексту, неразрешимые места пропускаются, а не считаются ошибкой.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET

ID = r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*"

# Виды таблиц в языке запросов (ед. число) -> вид метаданных
QUERY_KINDS = {
    "справочник": "Catalog", "catalog": "Catalog",
    "документ": "Document", "document": "Document",
    "перечисление": "Enum", "enum": "Enum",
    "регистрсведений": "InformationRegister", "informationregister": "InformationRegister",
    "регистрнакопления": "AccumulationRegister", "accumulationregister": "AccumulationRegister",
    "регистрбухгалтерии": "AccountingRegister", "accountingregister": "AccountingRegister",
    "регистррасчета": "CalculationRegister", "calculationregister": "CalculationRegister",
    "планвидовхарактеристик": "ChartOfCharacteristicTypes", "chartofcharacteristictypes": "ChartOfCharacteristicTypes",
    "плансчетов": "ChartOfAccounts", "chartofaccounts": "ChartOfAccounts",
    "планвидоврасчета": "ChartOfCalculationTypes", "chartofcalculationtypes": "ChartOfCalculationTypes",
    "бизнеспроцесс": "BusinessProcess", "businessprocess": "BusinessProcess",
    "задача": "Task", "task": "Task",
    "планобмена": "ExchangePlan", "exchangeplan": "ExchangePlan",
    "журналдокументов": "DocumentJournal", "documentjournal": "DocumentJournal",
    "константа": "Constant", "constant": "Constant",
    "последовательность": "Sequence", "sequence": "Sequence",
    "критерийотбора": "FilterCriterion", "filtercriterion": "FilterCriterion",
    "внешнийисточникданных": "ExternalDataSource", "externaldatasource": "ExternalDataSource",
}
REF_KIND = {"CatalogRef": "Catalog", "DocumentRef": "Document", "EnumRef": "Enum",
            "ChartOfCharacteristicTypesRef": "ChartOfCharacteristicTypes", "ChartOfAccountsRef": "ChartOfAccounts",
            "ChartOfCalculationTypesRef": "ChartOfCalculationTypes", "BusinessProcessRef": "BusinessProcess",
            "TaskRef": "Task", "ExchangePlanRef": "ExchangePlan"}

VT_BALANCE = {"остатки", "balance"}
VT_TURNOVERS = {"обороты", "turnovers"}
VT_BAL_TURN = {"остаткииобороты", "balanceandturnovers"}
VT_SLICE = {"срезпоследних", "срезпервых", "slicelast", "slicefirst"}
VT_ALL = VT_BALANCE | VT_TURNOVERS | VT_BAL_TURN | VT_SLICE

KEYWORDS = set("""выбрать select разрешенные allowed различные distinct первые top как as из from где where
соединение join левое left правое right полное full внутреннее inner внешнее outer по on и and или or не not
сгруппировать group имеющие having упорядочить order объединить union все all поместить into уничтожить drop
итоги totals для for изменения update индексировать index выбор case когда when тогда then иначе else конец end
в in между between есть is null подобно like спецсимвол escape возр asc убыв desc автоупорядочивание autoorder
ссылка refs значение value тип type выразить cast естьnull isnull истина true ложь false неопределено undefined
общие overall только only иерархия hierarchy периодами periods""".split())
SECTION_END = {"сгруппировать", "group", "имеющие", "having", "объединить", "union", "упорядочить", "order",
               "итоги", "totals", "для", "for", "индексировать", "index", "поместить", "into"}

def _fields(*names):
    return set(n.lower() for n in names)


STD_FIELDS = {
    "Catalog": _fields("Ссылка", "Ref", "Код", "Code", "Наименование", "Description", "Родитель", "Parent",
                       "Владелец", "Owner", "ЭтоГруппа", "IsFolder", "ПометкаУдаления", "DeletionMark",
                       "Предопределенный", "Predefined", "ИмяПредопределенныхДанных", "PredefinedDataName",
                       "ВерсияДанных", "DataVersion", "Представление", "Presentation"),
    "Document": _fields("Ссылка", "Ref", "Дата", "Date", "Номер", "Number", "Проведен", "Posted",
                        "ПометкаУдаления", "DeletionMark", "ВерсияДанных", "DataVersion", "Представление",
                        "Presentation", "МоментВремени", "PointInTime"),
    "Enum": _fields("Ссылка", "Ref", "Порядок", "Order", "Представление", "Presentation"),
    "TS": _fields("Ссылка", "Ref", "НомерСтроки", "LineNumber", "Представление", "Presentation"),
    "InformationRegister": _fields("Период", "Period", "Регистратор", "Recorder", "НомерСтроки", "LineNumber",
                                   "Активность", "Active", "МоментВремени", "PointInTime"),
    "AccumulationRegister": _fields("Период", "Period", "Регистратор", "Recorder", "НомерСтроки", "LineNumber",
                                    "Активность", "Active", "ВидДвижения", "RecordType", "МоментВремени", "PointInTime"),
    "PERIODS": _fields("Период", "Period", "Регистратор", "Recorder", "НомерСтроки", "LineNumber",
                       "МоментВремени", "PointInTime", "ПериодСекунда", "SecondPeriod", "ПериодМинута",
                       "MinutePeriod", "ПериодЧас", "HourPeriod", "ПериодДень", "DayPeriod", "ПериодНеделя",
                       "WeekPeriod", "ПериодДекада", "TenDaysPeriod", "ПериодМесяц", "MonthPeriod", "ПериодКвартал",
                       "QuarterPeriod", "ПериодПолугодие", "HalfYearPeriod", "ПериодГод", "YearPeriod"),
}
STD_FIELDS["ChartOfCharacteristicTypes"] = STD_FIELDS["Catalog"] | _fields("ТипЗначения", "ValueType")
CHECKABLE = {"Catalog", "Document", "Enum", "ChartOfCharacteristicTypes", "InformationRegister", "AccumulationRegister"}


# --------------------------------------------------------------------------------------
# Метаданные из индекса
# --------------------------------------------------------------------------------------
class Meta:
    """Объекты и поля из индекса onec_index (SQLite)."""

    def __init__(self, db):
        self.ok = bool(db) and os.path.isfile(db)
        self.objects = {}   # (kind, name_lc) -> dict(props, members)
        self.common = set()
        if not self.ok:
            return
        conn = sqlite3.connect(db)
        try:
            ids = {}
            for oid, kind, name, props in conn.execute(
                    "SELECT id,kind,name,props FROM objects WHERE kind IN (%s)" % ",".join("?" * len(set(QUERY_KINDS.values()))),
                    sorted(set(QUERY_KINDS.values()))):
                o = {"name": name, "props": json.loads(props or "{}"), "attrs": {}, "ts": {}, "dims": set(), "res": set(), "values": set()}
                self.objects[(kind, name.lower())] = o
                ids[oid] = (kind, o)
            for oid, mkind, path, name, types in conn.execute("SELECT obj_id,mkind,path,name,types FROM members"):
                if oid not in ids:
                    continue
                kind, o = ids[oid]
                nl = name.lower()
                if mkind == "TabularSection":
                    o["ts"].setdefault(nl, {})
                elif path and mkind == "Attribute":
                    o["ts"].setdefault(path.lower(), {})[nl] = types
                elif mkind == "Dimension":
                    o["dims"].add(nl)
                    o["attrs"][nl] = types
                elif mkind == "Resource":
                    o["res"].add(nl)
                    o["attrs"][nl] = types
                elif mkind in ("Attribute", "AddressingAttribute", "Column"):
                    o["attrs"][nl] = types
                elif mkind == "EnumValue":
                    o["values"].add(nl)
            for (n,) in conn.execute("SELECT name FROM objects WHERE kind='CommonAttribute'"):
                self.common.add(n.lower())
        finally:
            conn.close()

    def get(self, kind, name):
        return self.objects.get((kind, name.lower()))

    def source_fields(self, src):
        """Набор допустимых полей источника или None, если проверять не умеем."""
        kind, obj, ts, vt = src["kind"], src["obj"], src["ts"], src["vt"]
        if kind not in CHECKABLE:
            return None
        o = self.get(kind, obj)
        if o is None:
            return None
        if ts:
            cols = o["ts"].get(ts.lower())
            if cols is None:
                return None
            return STD_FIELDS["TS"] | set(cols) | self.common
        if kind in ("Catalog", "Document", "Enum", "ChartOfCharacteristicTypes"):
            return STD_FIELDS[kind] | set(o["attrs"]) | set(o["ts"]) | self.common
        if kind == "InformationRegister":
            if vt in VT_SLICE or not vt:
                return STD_FIELDS[kind] | set(o["attrs"]) | self.common
            return None
        if kind == "AccumulationRegister":
            dims = set(o["dims"])
            if not vt:
                return STD_FIELDS[kind] | set(o["attrs"]) | self.common
            res = o["res"]
            if vt in VT_BALANCE:
                return dims | {r + s for r in res for s in ("остаток", "balance")} | self.common
            if vt in VT_TURNOVERS:
                return dims | STD_FIELDS["PERIODS"] | {r + s for r in res for s in ("оборот", "приход", "расход", "turnover", "receipt", "expense")} | self.common
            if vt in VT_BAL_TURN:
                sfx = ("начальныйостаток", "конечныйостаток", "оборот", "приход", "расход", "openingbalance",
                       "closingbalance", "turnover", "receipt", "expense")
                return dims | STD_FIELDS["PERIODS"] | {r + s for r in res for s in sfx} | self.common
        return None

    def field_type(self, src, field):
        if src["ts"] or src["kind"] not in CHECKABLE:
            return None
        o = self.get(src["kind"], src["obj"])
        if o is None:
            return None
        return o["attrs"].get(field.lower())

    def ref_fields(self, types):
        """Поля, доступные через разыменование значения типа types; None — не проверяем."""
        if not types:
            return None
        allowed = set()
        for t in types.split("|"):
            m = re.match(r"(\w+)\.(.+)$", t)
            if not m or m.group(1) not in REF_KIND:
                return None
            k = REF_KIND[m.group(1)]
            if k not in CHECKABLE:
                return None
            o = self.get(k, m.group(2))
            if o is None:
                return None
            allowed |= STD_FIELDS[k] | set(o["attrs"]) | set(o["ts"])
        return allowed | self.common


# --------------------------------------------------------------------------------------
# Разбор текста запроса
# --------------------------------------------------------------------------------------
_QTOK = re.compile(r'//[^\n]*|"(?:[^"]|"")*"|&%s|%s(?:\.%s)*|\d+(?:\.\d+)?|\S' % (ID, ID, ID))


def tokenize(text):
    """-> [(значение, позиция)] без комментариев; строковые литералы -> '"'."""
    out = []
    for m in _QTOK.finditer(text):
        v = m.group(0)
        if v.startswith("//"):
            continue
        out.append(('"' if v.startswith('"') else v, m.start()))
    return out


def _is_query(text):
    return re.search(r"(?i)(?<![\wА-Яа-яЁё])(ВЫБРАТЬ|SELECT)(?![\wА-Яа-яЁё])", text) is not None


def object_refs(text):
    """Все упоминания объектов в тексте запроса -> [(kind, name, member)]."""
    refs = []
    for v, _pos in tokenize(text):
        if "." in v and not v.startswith("&"):
            parts = v.split(".")
            k = QUERY_KINDS.get(parts[0].lower())
            if k and len(parts) >= 2:
                refs.append((k, parts[1], ".".join(parts[2:])))
    return refs


def analyze(text, meta=None):
    """Разобрать пакет запросов. -> dict(queries=[...], findings=[(pos, rule, msg)])."""
    toks = tokenize(text)
    findings = []
    queries = []
    temp_tables = set()
    # разбить пакет по ';' вне скобок
    batches, cur, depth = [], [], 0
    for t in toks:
        if t[0] == "(":
            depth += 1
        elif t[0] == ")":
            depth = max(0, depth - 1)
        if t[0] == ";" and depth == 0:
            batches.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        batches.append(cur)
    for bt in batches:
        q = _analyze_one(bt, meta, temp_tables, findings)
        if q:
            queries.append(q)
    # повторные упоминания одного места (одинаковая позиция) — одно замечание
    uniq, seen = [], set()
    for f in findings:
        if (f[0], f[1]) not in seen:
            seen.add((f[0], f[1]))
            uniq.append(f)
    findings[:] = uniq
    return {"queries": queries, "findings": findings, "temp_tables": sorted(temp_tables)}


def _analyze_one(toks, meta, temp_tables, findings):
    low = [t[0].lower() for t in toks]
    if not any(x in ("выбрать", "select", "уничтожить", "drop") for x in low):
        return None
    sources = []      # dict(kind,obj,ts,vt,alias,params,pos)
    aliases = {}      # псевдоним -> [источники] (в частях ОБЪЕДИНИТЬ один псевдоним бывает у разных таблиц)
    n = len(toks)
    declared = set(low[i + 1] for i in range(n - 1) if low[i] in ("как", "as"))
    # ( ВЫБРАТЬ ... ) КАК X — псевдоним подзапроса: его поля по метаданным не проверяются
    for i in range(1, n - 1):
        if low[i] in ("как", "as") and low[i - 1] == ")":
            d, k = 0, i - 1
            while k >= 0:
                if low[k] == ")":
                    d += 1
                elif low[k] == "(":
                    d -= 1
                    if d == 0:
                        break
                k -= 1
            if k >= 0 and any(x in ("выбрать", "select") for x in low[k:i]):
                aliases.setdefault(low[i + 1], []).append({"kind": "(подзапрос)", "obj": "", "ts": "", "vt": "",
                                                           "alias": toks[i + 1][0], "params": None, "pos": toks[i][1],
                                                           "text": "(подзапрос)"})
    # ПОМЕСТИТЬ
    for i, v in enumerate(low):
        if v in ("поместить", "into") and i + 1 < n:
            temp_tables.add(low[i + 1])
    # ПОЛНОЕ СОЕДИНЕНИЕ
    for i, v in enumerate(low):
        if v in ("полное", "full") and i + 1 < n and low[i + 1] in ("соединение", "join", "внешнее", "outer"):
            findings.append((toks[i][1], "full-join", "ПОЛНОЕ СОЕДИНЕНИЕ: по возможности переписать без него (#std435)"))
    # источники
    for i, (v, pos) in enumerate(toks):
        if "." not in v or v.startswith("&"):
            continue
        parts = v.split(".")
        kind = QUERY_KINDS.get(parts[0].lower())
        prev = low[i - 1] if i > 0 else ""
        if not kind:
            continue
        if parts[0].lower() in declared and prev not in ("из", "from", "соединение", "join"):
            continue  # это псевдоним, совпадающий с именем вида (… КАК ПланОбмена)
        # ЗНАЧЕНИЕ(...), ТИП(...), ССЫЛКА ... - упоминание, а не источник
        is_source = prev in ("из", "from", "соединение", "join", ",")
        if prev == "," and _inside_call(low, i):
            is_source = False
        if meta is not None and meta.ok and len(parts) >= 2:
            if kind in CHECKABLE | {"AccountingRegister", "CalculationRegister", "ChartOfAccounts", "ChartOfCalculationTypes",
                                    "BusinessProcess", "Task", "ExchangePlan", "DocumentJournal", "Constant"} and meta.get(kind, parts[1]) is None:
                if (kind, parts[1].lower()) not in meta.objects and re.match(ID + "$", parts[1]):
                    findings.append((pos, "unknown-table", "нет объекта %s.%s в конфигурации" % (parts[0], parts[1])))
            elif kind == "Enum" and len(parts) >= 3 and not is_source:
                o = meta.get(kind, parts[1])
                if o is not None and parts[2].lower() not in o["values"] and parts[2].lower() not in ("пустаяссылка", "emptyref"):
                    findings.append((pos, "unknown-field", "нет значения %s у перечисления %s" % (parts[2], parts[1])))
        if not is_source or len(parts) < 2:
            continue
        ts = vt = ""
        rest = [p.lower() for p in parts[2:]]
        if rest and rest[-1] in VT_ALL:
            vt = rest[-1]
            rest = rest[:-1]
        if rest:
            ts = parts[2]
        j = i + 1
        params = None
        if j < n and toks[j][0] == "(":
            d, k0 = 0, j
            while j < n:
                if toks[j][0] == "(":
                    d += 1
                elif toks[j][0] == ")":
                    d -= 1
                    if d == 0:
                        break
                j += 1
            params = " ".join(t[0] for t in toks[k0 + 1:j])
            j += 1
        alias = None
        if j < n and low[j] in ("как", "as") and j + 1 < n:
            alias = toks[j + 1][0]
        elif j < n and re.match(ID + "$", toks[j][0]) and low[j] not in KEYWORDS:
            alias = toks[j][0]
        if alias is None:
            alias = parts[-1] if not vt else parts[1] + parts[-1]
        src = {"kind": kind, "obj": parts[1], "ts": ts, "vt": vt, "alias": alias, "params": params, "pos": pos,
               "text": v}
        sources.append(src)
        aliases.setdefault(alias.lower(), []).append(src)
    # поля Псевдоним.Поле[.Подполе]
    if meta is not None and meta.ok:
        seen = set()
        for i, (v, pos) in enumerate(toks):
            if "." not in v or v.startswith("&"):
                continue
            parts = v.split(".")
            srcs = aliases.get(parts[0].lower())
            if not srcs or (QUERY_KINDS.get(parts[0].lower()) and parts[0].lower() not in declared):
                continue
            key = (parts[0].lower(), ".".join(parts[1:]).lower())
            if key in seen:
                continue
            seen.add(key)
            problem = _check_field(meta, srcs, parts)
            if problem:
                findings.append((pos, "unknown-field", problem))
        # отбор по измерениям виртуальной таблицы в ГДЕ (#std657)
        for i, v in enumerate(low):
            if v not in ("где", "where") or _depth_at(low, i) != _depth_at(low, 0):
                continue
            d = 0
            reported = set()
            for k in range(i + 1, n):
                if low[k] == "(":
                    d += 1
                elif low[k] == ")":
                    d -= 1
                    if d < 0:
                        break
                elif d == 0 and low[k] in SECTION_END:
                    break
                if "." in low[k]:
                    a, fld = low[k].split(".")[0], low[k].split(".")[1]
                    src = (aliases.get(a) or [None])[0]
                    if src and src["vt"] and src["kind"] in ("AccumulationRegister", "InformationRegister"):
                        o = meta.get(src["kind"], src["obj"])
                        if o is not None and fld in o["dims"] and a not in reported:
                            reported.add(a)
                            findings.append((toks[k][1], "vt-where",
                                             "условие по измерению %s виртуальной таблицы %s в ГДЕ: передать в параметры таблицы (#std657)"
                                             % (toks[k][0].split(".")[1], src["text"])))
    return {"sources": sources, "aliases": sorted(aliases)}


def _check_field(meta, srcs, parts):
    """None, если поле допустимо хотя бы для одного источника с этим псевдонимом."""
    problems = []
    for src in srcs:
        allowed = meta.source_fields(src)
        if allowed is None:
            return None
        f = parts[1].lower()
        if f not in allowed:
            problems.append("нет поля %s у %s (псевдоним %s)" % (parts[1], src["text"], parts[0]))
            continue
        if len(parts) < 3:
            return None
        o = meta.get(src["kind"], src["obj"])
        if not src["ts"] and o is not None and f in o["ts"]:
            if parts[2].lower() in set(o["ts"][f]) | STD_FIELDS["TS"] | meta.common:
                return None
            problems.append("нет колонки %s в табличной части %s.%s" % (parts[2], src["obj"], parts[1]))
            continue
        rf = meta.ref_fields(meta.field_type(src, parts[1]))
        if rf is None or parts[2].lower() in rf:
            return None
        problems.append("нет поля %s у значения %s.%s" % (parts[2], parts[0], parts[1]))
    return problems[0] if problems else None


def _inside_call(low, i):
    """Токен внутри ЗНАЧЕНИЕ(...)/ТИП(...)/функции, а не в списке источников."""
    d = 0
    for k in range(i - 1, -1, -1):
        if low[k] == ")":
            d += 1
        elif low[k] == "(":
            if d == 0:
                return k > 0 and low[k - 1] not in ("из", "from", "соединение", "join", ",")
            d -= 1
        elif d == 0 and low[k] in ("из", "from", "соединение", "join"):
            return False
    return False


def _depth_at(low, i):
    d = 0
    for k in range(i):
        if low[k] == "(":
            d += 1
        elif low[k] == ")":
            d -= 1
    return d


# --------------------------------------------------------------------------------------
# Извлечение запросов
# --------------------------------------------------------------------------------------
LOOP_OPEN = {"цикл", "do"}
LOOP_CLOSE = {"конеццикла", "enddo"}
PROC_BOUND = {"процедура", "функция", "procedure", "function", "конецпроцедуры", "конецфункции", "endprocedure", "endfunction"}


def bsl_scan(text):
    """Лексер BSL: строковые литералы с номерами строк, слова вне строк.

    -> (literals [(line, text, stmt_no)], words [(line, word, stmt_no)])
    Многострочный литерал: продолжение с '|', строки-комментарии внутри пропускаются
    (заменяются пустой строкой, чтобы номера строк совпадали).
    """
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.split("\n")
    literals, words = [], []
    stmt = 0
    in_str = False
    buf, start = [], 0
    word_re = re.compile(ID + r"|;|\.|\(")
    for ln, raw in enumerate(lines, 1):
        line = raw.rstrip("\r")
        i = 0
        if in_str:
            s = line.lstrip()
            if s.startswith("|"):
                i = len(line) - len(s) + 1
                buf.append("\n")
            elif s.startswith("//") or not s:
                buf.append("\n")
                continue
            else:
                literals.append((start, "".join(buf), stmt))
                in_str = False
                buf = []
        while i < len(line):
            if in_str:
                j = line.find('"', i)
                if j < 0:
                    buf.append(line[i:])
                    i = len(line)
                    break
                if j + 1 < len(line) and line[j + 1] == '"':
                    buf.append(line[i:j] + '"')
                    i = j + 2
                    continue
                buf.append(line[i:j])
                literals.append((start, "".join(buf), stmt))
                buf = []
                in_str = False
                i = j + 1
                continue
            c = line[i]
            if c == '"':
                in_str = True
                start = ln
                i += 1
                continue
            if line.startswith("//", i):
                break
            m = word_re.match(line, i)
            if m:
                w = m.group(0)
                if w == ";":
                    stmt += 1
                words.append((ln, w, stmt))
                i = m.end()
            else:
                i += 1
    if in_str:
        literals.append((start, "".join(buf), stmt))
    return literals, words


REPLACE_RE = re.compile(r'(?:СтрЗаменить|StrReplace)\s*\([^;]*?,\s*"((?:[^"]|"")+)"', re.I)


def replaced_fragments(text):
    """Строки, которые модуль подменяет в тексте запроса через СтрЗаменить."""
    return set(m.group(1).replace('""', '"').lower() for m in REPLACE_RE.finditer(text))


def bsl_queries(text):
    """-> [(line, query_text)], [(line, var)] выполнений запроса в цикле."""
    literals, words = bsl_scan(text)
    queries = []
    by_stmt = {}
    for ln, s, st in literals:
        by_stmt.setdefault(st, []).append((ln, s))
    for st, items in by_stmt.items():
        first = items[0][0]
        joined = ""
        for ln, s in items:
            need = (ln - first) - joined.count("\n")
            joined += ("\n" * need) if need > 0 else (" " if joined else "")
            joined += s
        if _is_query(joined):
            queries.append((first, joined))
    # запрос в цикле: переменные, которым присвоен Новый Запрос
    lw = [(ln, w.lower(), st) for ln, w, st in words]
    qvars = set()
    for m in re.finditer(r"(?im)^[ \t]*(%s)[ \t]*=[ \t]*(?:Новый|New)[ \t]+(?:Запрос|Query)\b" % ID, text):
        qvars.add(m.group(1).lower())
    # #std436 — однотипные запросы при обходе коллекции: Для / Для Каждого.
    # Циклы Пока (порционное чтение, повторные попытки) не учитываются.
    # Заголовок цикла («Для Каждого Стр Из Запрос.Выполнить()…») выполняется один раз — не в счёт.
    # Подавление: комментарий «@skip-check query-in-loop» на строке или строкой выше.
    src_lines = text.split("\n")
    loops = []
    stack = []  # [вид, в_теле]
    for k, (ln, w, _st) in enumerate(lw):
        if w in PROC_BOUND:
            stack = []
        elif w in ("для", "for"):
            stack.append(["for", False])
        elif w in ("пока", "while"):
            stack.append(["while", False])
        elif w in LOOP_OPEN:
            if stack and not stack[-1][1]:
                stack[-1][1] = True
        elif w in LOOP_CLOSE:
            if stack:
                stack.pop()
        elif w in ("выполнить", "execute", "выполнитьпакет", "executebatch") \
                and any(t == "for" and body for t, body in stack) \
                and k >= 2 and lw[k - 1][1] == "." and k + 1 < len(lw) and lw[k + 1][1] == "(":
            var = lw[k - 2][1]
            near = " ".join(src_lines[max(0, ln - 2):ln]).lower()
            if "@skip-check" in near and "query-in-loop" in near:
                continue
            if var in qvars or "запрос" in var or "query" in var:
                loops.append((ln, words[k - 2][1]))
    return queries, loops


def xml_queries(path, text):
    """Запросы из Template.xml (СКД) и Form.xml (динамический список) -> [(line, where, query)]."""
    out = []
    tag = "query" if "DataCompositionSchema" in text[:3000] else ("QueryText" if "<QueryText>" in text else None)
    if not tag:
        return out
    for m in re.finditer(r"<%s>(.*?)</%s>" % (tag, tag), text, re.S):
        body = m.group(1)
        q = body.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")
        line = text.count("\n", 0, m.start(1)) + 1
        where = ""
        if tag == "query":
            pre = text[:m.start()]
            names = re.findall(r"<dataSet[^>]*>\s*<name>([^<]+)</name>", pre)
            where = "НаборДанных:" + names[-1] if names else "СКД"
        else:
            pre = text[:m.start()]
            names = re.findall(r'<Attribute name="([^"]+)"', pre)
            where = "ДинамическийСписок:" + names[-1] if names else "ДинамическийСписок"
        if _is_query(q):
            out.append((line, where, q))
    return out


def read_text(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8-sig", errors="replace")


def iter_files(path):
    if os.path.isfile(path):
        yield path
        return
    for dp, dn, fns in os.walk(path):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for fn in fns:
            if fn.endswith(".bsl") or fn in ("Template.xml", "Form.xml"):
                yield os.path.join(dp, fn)


def file_queries(path, with_replaced=False):
    """-> [(line, where, query)], [(line, var)] (+ подменяемые строки) для файла."""
    text = read_text(path)
    if path.endswith(".bsl"):
        qs, loops = bsl_queries(text)
        res = ([(ln, "модуль", q) for ln, q in qs], loops)
        return res + (replaced_fragments(text),) if with_replaced else res
    res = (xml_queries(path, text), [])
    return res + (set(),) if with_replaced else res


_TOKEN_AT = re.compile(r"&?%s(?:\.%s)*" % (ID, ID))


def _token_at(q, pos):
    m = _TOKEN_AT.match(q, pos)
    return m.group(0).lower() if m else ""


def _line_of(query, pos, base):
    return base + query.count("\n", 0, pos)


# --------------------------------------------------------------------------------------
# Команды
# --------------------------------------------------------------------------------------
def find_root(start):
    p = os.path.abspath(start if os.path.isdir(start) else os.path.dirname(start))
    while True:
        if os.path.isfile(os.path.join(p, "Configuration.xml")):
            return p
        up = os.path.dirname(p)
        if up == p:
            return None
        p = up


def cmd_scan(path, root, db, rules, errors_only, limit, offset):
    root = root or find_root(path)
    db = db or (os.path.join(root, ".onec", "index.sqlite") if root else None)
    meta = Meta(db)
    nq = {"модуль": 0, "СКД": 0, "ДинамическийСписок": 0}
    nfiles = 0
    rows = []
    for f in iter_files(path):
        try:
            qs, loops, repl = file_queries(f, with_replaced=True)
        except OSError:
            continue
        rel = os.path.relpath(f, root) if root else f
        if qs or loops:
            nfiles += 1
        for ln, where, q in qs:
            nq[where.split(":")[0] if ":" in where else where] = nq.get(where.split(":")[0] if ":" in where else where, 0) + 1
            res = analyze(q, meta)
            for pos, rule, msg in res["findings"]:
                if rule.startswith("unknown") and repl:
                    tok = _token_at(q, pos)
                    if any(r in tok or tok in r for r in repl if len(r) > 2):
                        continue  # шаблон: модуль подменяет это место через СтрЗаменить
                rows.append((rel, _line_of(q, pos, ln), rule, msg, where))
        for ln, var in loops:
            rows.append((rel, ln, "query-in-loop", "выполнение запроса (%s) внутри цикла: собрать данные одним запросом (#std436)" % var, "модуль"))
    if rules:
        rows = [r for r in rows if r[2] in rules]
    if errors_only:
        rows = [r for r in rows if r[2] in ("unknown-table", "unknown-field")]
    lines = []
    lines.append("Запросов: модули %d, СКД %d, динамические списки %d; файлов с запросами %d"
                 % (nq.get("модуль", 0), nq.get("СКД", 0) + nq.get("НаборДанных", 0), nq.get("ДинамическийСписок", 0), nfiles))
    if not meta.ok:
        lines.append("Индекс не найден: проверки таблиц и полей не выполнялись (onec_index.py build).")
    cnt = {}
    for r in rows:
        cnt[r[2]] = cnt.get(r[2], 0) + 1
    lines.append("Замечания: " + (", ".join("%s %d" % kv for kv in sorted(cnt.items())) if cnt else "нет"))
    for rel, ln, rule, msg, where in sorted(rows):
        lines.append("%s:%d  [%s] %s%s" % (rel, ln, rule, msg, "  (%s)" % where if where not in ("модуль",) else ""))
    part = lines[offset:offset + limit] if offset else lines[:limit]
    print("\n".join(part))
    if len(lines) > offset + limit:
        print("... ещё %d строк (--offset %d)" % (len(lines) - offset - limit, offset + limit))
    return 0


def cmd_show(path, line, root, db):
    root = root or find_root(path)
    db = db or (os.path.join(root, ".onec", "index.sqlite") if root else None)
    meta = Meta(db)
    qs, loops = file_queries(path)
    if line:
        cand = [q for q in qs if q[0] <= line]
        qs = [max(cand, key=lambda x: x[0])] if cand else qs[:1]
    if not qs:
        print("Запросов не найдено.")
    for ln, where, q in qs[:20]:
        res = analyze(q, meta)
        print("=== строка %d (%s)" % (ln, where))
        print(q.strip("\n"))
        for i, qq in enumerate(res["queries"], 1):
            src = ", ".join("%s%s КАК %s" % (s["text"], "(…)" if s["params"] else "", s["alias"]) for s in qq["sources"])
            print("--- запрос %d: %s" % (i, src or "без таблиц метаданных"))
        if res["temp_tables"]:
            print("--- временные таблицы: " + ", ".join(res["temp_tables"]))
        for pos, rule, msg in res["findings"]:
            print("!! стр.%d [%s] %s" % (_line_of(q, pos, ln), rule, msg))
    if len(qs) > 20:
        print("... ещё %d запросов (укажите --line)" % (len(qs) - 20))
    for ln, var in loops:
        if line and abs(ln - line) > 200:
            continue
        print("!! стр.%d [query-in-loop] %s внутри цикла (#std436)" % (ln, var))
    return 0


def main(argv=None):
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="Запросы 1С в выгрузке: извлечение и проверка")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("scan")
    a.add_argument("path")
    a.add_argument("--root")
    a.add_argument("--db")
    a.add_argument("--rule", action="append")
    a.add_argument("--errors", action="store_true", help="только unknown-table и unknown-field")
    a.add_argument("--limit", type=int, default=150)
    a.add_argument("--offset", type=int, default=0)
    a = sub.add_parser("show")
    a.add_argument("path")
    a.add_argument("--line", type=int)
    a.add_argument("--root")
    a.add_argument("--db")
    a = ap.parse_args(argv)
    if a.cmd == "scan":
        return cmd_scan(a.path, a.root, a.db, a.rule, a.errors, a.limit, a.offset)
    return cmd_show(a.path, a.line, a.root, a.db)


if __name__ == "__main__":
    sys.exit(main())
