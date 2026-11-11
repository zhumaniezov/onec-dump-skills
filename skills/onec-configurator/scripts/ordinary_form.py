#!/usr/bin/env python3
"""Обычная форма 1С (Ext/Form.bin): сводка, модуль, безопасная замена модуля.

Только стандартная библиотека, Python 3.9+.

  info       <Form.bin> [--limit N]      реквизиты формы, события, дерево элементов, обработчики
  module     <Form.bin> [--out FILE]     текст модуля формы
  set-module <Form.bin> <new.bsl> --expected-sha256 SHA [--apply]
                                         заменить модуль; без --apply только показать diff
  verify     <Form.bin>                  проверить контейнер: записи, сжатие, разбор

Что известно о формате. Запись `form` — дерево в фигурных скобках. Элемент формы —
узел {GUID-класса, номер, {свойства}, {раскладка}, {14,"Имя",...}, ...}. Классы
элементов определены по образцам форм (см. CLASS_NAMES); неподтверждённые помечены «?».
Обработчик события хранится строкой с именем процедуры, поэтому сопоставление
«элемент -> обработчик» делается по совпадению строк свойств с процедурами модуля.
Позиционный смысл остальных чисел не расшифровывается и не выдумывается.
"""
import argparse
import difflib
import hashlib
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v8container  # noqa: E402

GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PROC_RE = re.compile(r"(?im)^[ \t]*(?:Процедура|Функция|Procedure|Function)[ \t]+([\wА-Яа-яЁё]+)[ \t]*\(")
LANGS = {"ru", "en", "Eng", "uz", "kk", "uk", "be", "de", "ro", "tr", "lv", "lt", "az", "ka", "pl", "vi", "hy", "tk", "ky", "tg"}

# Класс элемента -> тип. Определено по именам элементов в реальных формах
# (Надпись1, Кнопка1, РамкаГруппы1, КоманднаяПанель1 ...). «?» — по одному образцу.
CLASS_NAMES = {
    "0fc7e20d-f241-460c-bdf4-5ad88e5474a5": "Надпись",
    "6ff79819-710e-4145-97cd-1618da79e3e2": "Кнопка",
    "381ed624-9217-4e63-85db-c4c3cb87daae": "ПолеВвода",
    "35af3d93-d7c7-4a2e-a8eb-bac87a1a3f26": "Флажок",
    "90db814a-c75f-4b54-bc96-df62e554d67d": "РамкаГруппы",
    "e69bf21d-97b2-4f37-86db-675aea9ec2cb": "КоманднаяПанель",
    "782e569a-79a7-4a4f-a936-b48d013936ec": "Переключатель",
    "ea83fe3a-ac3c-4cce-8045-3dddf35b28b1": "ТабличноеПоле",
    "09ccdc77-ea1a-4a6d-ab1c-3435eada2433": "Панель",
    "b1db1f86-abbb-4cf0-8852-fe6ae21650c2": "Индикатор",
    "19f8b798-314e-4b4e-8121-905b2a7a03f5": "ПолеСписка?",
    "236a17b3-7f44-46d9-a907-75f9cdc61ab5": "ПолеТабличногоДокумента?",
}


# ----------------------------------------------------------------------------------
# Разбор скобочного формата
# ----------------------------------------------------------------------------------
class Str(str):
    """Строка в кавычках (в отличие от неквотированных чисел и GUID)."""


_TOK = re.compile(r'\s*(?:(\{)|(\})|(,)|"((?:[^"]|"")*)"|(#base64:[A-Za-z0-9+/=\s]*)|([^,{}"\s][^,{}]*))', re.S)


def parse_brace(text):
    if text.startswith("﻿"):
        text = text[1:]
    stack = [[]]
    pos, n = 0, len(text)
    while pos < n:
        m = _TOK.match(text, pos)
        if not m:
            if not text[pos:].strip():
                break
            raise ValueError("не удалось разобрать позицию %d: %r" % (pos, text[pos:pos + 40]))
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            if len(stack) < 2:
                raise ValueError("лишняя закрывающая скобка в позиции %d" % pos)
            node = stack.pop()
            stack[-1].append(node)
        elif m.group(3):
            continue
        elif m.group(4) is not None:
            stack[-1].append(Str(m.group(4).replace('""', '"')))
        elif m.group(5):
            stack[-1].append("#base64")
        else:
            stack[-1].append(m.group(6).strip())
    if len(stack) != 1:
        raise ValueError("не закрыто скобок: %d" % (len(stack) - 1))
    top = stack[0]
    return top[0] if len(top) == 1 else top


def _strings(node, acc):
    if isinstance(node, list):
        for x in node:
            _strings(x, acc)
    elif isinstance(node, Str):
        acc.append(str(node))
    return acc


def _caption(strings):
    """Первая подпись ru (или первая языковая пара)."""
    first = ""
    for i in range(len(strings) - 1):
        if strings[i] in LANGS and strings[i + 1]:
            if strings[i] == "ru":
                return strings[i + 1]
            first = first or strings[i + 1]
    return first


def _is_element(node):
    return (isinstance(node, list) and len(node) >= 5 and isinstance(node[0], str)
            and not isinstance(node[0], Str) and GUID.match(node[0]) is not None
            and isinstance(node[1], str) and node[1].isdigit())


def _element_name(node):
    for y in node[3:7]:
        if isinstance(y, list) and len(y) > 1 and y[0] == "14" and isinstance(y[1], Str):
            return str(y[1])
    return None


def _split_strings(node, procset):
    """Строки свойств элемента -> (собственные строки, имена обработчиков).

    Узел, содержащий строку-имя процедуры модуля, считается описанием события:
    его подписи к элементу не относятся.
    """
    own, handlers = [], []

    def walk(x):
        if not isinstance(x, list):
            if isinstance(x, Str):
                own.append(str(x))
            return
        hit = [str(y) for y in x if isinstance(y, Str) and str(y).lower() in procset]
        if hit:
            handlers.extend(hit)
            return
        for y in x:
            walk(y)

    walk(node)
    return own, sorted(set(handlers))


def elements(tree, procset=frozenset()):
    """-> [(глубина, класс, имя, подпись, обработчики)] в порядке обхода."""
    out = []

    def walk(node, depth):
        if not isinstance(node, list):
            return
        if _is_element(node):
            name = _element_name(node)
            if name is not None:
                own, handlers = _split_strings(node[2], procset)
                out.append((depth, node[0], name, _caption(own), handlers))
                for y in node[2:]:
                    walk(y, depth + 1)
                return
        for y in node:
            walk(y, depth)

    walk(tree, 0)
    return out


def form_attributes(tree):
    """Реквизиты формы: узлы {{N},0|1,0|1,"Имя",{"Pattern",...}}."""
    out = []

    def walk(node):
        if not isinstance(node, list):
            return
        if (len(node) >= 5 and isinstance(node[0], list) and len(node[0]) == 1 and isinstance(node[0][0], str)
                and node[0][0].lstrip("-").isdigit() and isinstance(node[3], Str)
                and isinstance(node[4], list) and node[4] and node[4][0] == "Pattern"):
            out.append((str(node[3]), _pattern(node[4])))
            return
        for y in node:
            walk(y)

    walk(tree)
    return out


def _pattern(p):
    parts = []
    for t in p[1:]:
        if not isinstance(t, list) or not t:
            continue
        k = t[0]
        if k == "S":
            parts.append("Строка" + ("(%s)" % t[1] if len(t) > 1 and t[1] not in ("0", "") else ""))
        elif k == "N":
            parts.append("Число(%s,%s)" % (t[1] if len(t) > 1 else "?", t[2] if len(t) > 2 else "0"))
        elif k == "B":
            parts.append("Булево")
        elif k == "D":
            parts.append("Дата")
        elif k == "#":
            parts.append("ссылка:" + (t[1][:8] if len(t) > 1 else "?"))
        else:
            parts.append(str(k))
    return "|".join(parts) or "?"


def form_events(tree):
    """События формы: узлы {код, GUID, {3,"Обработчик",...}}."""
    out = []

    def walk(node):
        if not isinstance(node, list):
            return
        if (len(node) >= 3 and isinstance(node[0], str) and node[0].isdigit() and len(node[0]) == 5
                and isinstance(node[1], str) and GUID.match(node[1] or "")
                and isinstance(node[2], list) and len(node[2]) > 1 and isinstance(node[2][1], Str)):
            out.append((node[0], str(node[2][1])))
            return
        for y in node:
            walk(y)

    walk(tree)
    return out


# ----------------------------------------------------------------------------------
# Контейнер
# ----------------------------------------------------------------------------------
def load(path):
    with open(path, "rb") as f:
        data = f.read()
    ents = v8container.parse(data)
    rec = {e[0]: e for e in ents}
    if "form" not in rec:
        raise ValueError("в контейнере нет записи form: это не обычная форма?")
    return data, ents, rec


def looks_plain(body):
    b = body[3:] if body.startswith(b"\xef\xbb\xbf") else body
    return b[:1] in (b"{", b"") or b[:1].isspace() or body.startswith(b"\xef\xbb\xbf")


def decode(body):
    return body.decode("utf-8-sig", errors="replace")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ----------------------------------------------------------------------------------
# Команды
# ----------------------------------------------------------------------------------
def cmd_info(path, limit):
    data, ents, rec = load(path)
    lines = []
    p = lines.append
    p("Обычная форма: %s" % path)
    p("SHA256: %s" % sha256(data))
    p("Записи: " + ", ".join("%s %d байт" % (e[0], len(e[3])) for e in ents))
    tree = parse_brace(decode(rec["form"][3]))
    module = decode(rec["module"][3]) if "module" in rec else ""
    procs = PROC_RE.findall(module)
    procset = set(p_.lower() for p_ in procs)
    p("Модуль: %d строк, процедур/функций %d" % (module.count("\n") + (1 if module.strip() else 0), len(procs)))
    attrs = form_attributes(tree)
    if attrs:
        p("Реквизиты формы (%d):" % len(attrs))
        for name, typ in attrs:
            p("  %s: %s" % (name, typ))
    used = set()
    ev = form_events(tree)
    if ev:
        p("События формы (%d):" % len(ev))
        for code, h in ev:
            mark = "" if h.lower() in procset else "  !! процедуры нет в модуле"
            used.add(h.lower())
            p("  %s -> %s%s" % (code, h, mark))
    els = elements(tree, procset)
    p("Элементы (%d):" % len(els))
    for depth, cls, name, cap, handlers in els:
        used.update(h.lower() for h in handlers)
        s = "  " + "  " * depth + "%s %s" % (CLASS_NAMES.get(cls, "класс " + cls[:8]), name)
        if cap and cap != name:
            cap = " ".join(cap.split())
            s += "  «%s»" % (cap[:60] + ("..." if len(cap) > 60 else ""))
        if handlers:
            s += "  обработчики: " + ", ".join(handlers)
        p(s)
    free = [x for x in procs if x.lower() not in used]
    if free:
        p("Процедуры модуля без привязки к событиям формы/элементов (%d): %s" % (len(free), ", ".join(free[:40])))
        p("  (вызываются из кода, привязаны неизвестным здесь способом или не используются — проверить поиском)")
    p("Ограничения: раскладка и прочие числовые свойства не расшифрованы; классы с «?» определены по одному образцу.")
    _print(lines, limit)
    return 0


def _print(lines, limit):
    print("\n".join(lines[:limit]))
    if len(lines) > limit:
        print("... ещё %d строк (увеличьте --limit)" % (len(lines) - limit))


def cmd_module(path, out):
    _data, _ents, rec = load(path)
    if "module" not in rec:
        print("В форме нет записи module.")
        return 1
    body = rec["module"][3]
    if out:
        with open(out, "wb") as f:
            f.write(body)
        print("Модуль записан: %s (%d байт, sha256 %s)" % (out, len(body), sha256(body)))
    else:
        sys.stdout.write(decode(body))
    return 0


def _match_format(old_body, new_text):
    """Привести новый текст к BOM и окончаниям строк старого модуля."""
    bom = old_body.startswith(b"\xef\xbb\xbf")
    crlf = b"\r\n" in old_body or not old_body.strip(b"\xef\xbb\xbf")
    t = new_text.replace("\r\n", "\n")
    if crlf:
        t = t.replace("\n", "\r\n")
    raw = t.encode("utf-8")
    return (b"\xef\xbb\xbf" + raw) if bom or not old_body else raw


def cmd_set_module(path, new_path, expected, apply):
    data, ents, rec = load(path)
    cur = sha256(data)
    if expected != cur:
        print("ОТКАЗ: Form.bin изменился. Ожидался sha256 %s, сейчас %s. Перестройте план по текущему файлу." % (expected, cur))
        return 4
    with open(new_path, "rb") as f:
        new_text = f.read().decode("utf-8-sig")
    old_body = rec["module"][3] if "module" in rec else b""
    new_body = _match_format(old_body, new_text)
    if new_body == old_body:
        print("Изменений нет.")
        return 0
    diff = list(difflib.unified_diff(decode(old_body).splitlines(), decode(new_body).splitlines(),
                                     "module (было)", "module (стало)", lineterm="", n=2))
    _print(diff, 150)
    new_ents = []
    for name, created, modified, body in ents:
        new_ents.append((name, created, modified, new_body if name == "module" else body))
    if "module" not in rec:
        new_ents.append(("module", rec["form"][1], rec["form"][2], new_body))
    out = v8container.build(new_ents)
    # проверка: всё, кроме модуля, байт-в-байт; модуль равен новому
    chk = {e[0]: e[3] for e in v8container.parse(out)}
    for name, _c, _m, body in ents:
        if name != "module" and chk.get(name) != body:
            print("ОТКАЗ: при сборке изменилась запись %s. Файл не записан." % name)
            return 5
    if chk.get("module") != new_body:
        print("ОТКАЗ: модуль после сборки не совпал. Файл не записан.")
        return 5
    if not apply:
        print("\nПробный режим: файл не изменён. Записи несжатые, остальные записи без изменений.")
        print("Для записи повторите с --apply --expected-sha256 %s" % cur)
        return 0
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".Form.bin.", dir=d)
    with os.fdopen(fd, "wb") as f:
        f.write(out)
    os.replace(tmp, path)
    print("\nЗаписано: %s\nНовый sha256: %s" % (path, sha256(out)))
    print("Осталось проверить в 1С: загрузка конфигурации из файлов и открытие формы в толстом клиенте.")
    return 0


def cmd_verify(path):
    data, ents, rec = load(path)
    ok = True
    print("Записи: " + ", ".join("%s %d байт" % (e[0], len(e[3])) for e in ents))
    for name, _c, _m, body in ents:
        if body and not looks_plain(body):
            ok = False
            print("ОШИБКА: запись %s похожа на сжатую. Платформа такую форму не откроет." % name)
    try:
        tree = parse_brace(decode(rec["form"][3]))
        print("Запись form разобрана: элементов %d, реквизитов %d, событий %d"
              % (len(elements(tree)), len(form_attributes(tree)), len(form_events(tree))))
    except ValueError as ex:
        ok = False
        print("ОШИБКА разбора form: %s" % ex)
    print("Итог: %s (проверка файла; загрузку в ИБ проверяет платформа)" % ("ok" if ok else "есть ошибки"))
    return 0 if ok else 6


def main(argv=None):
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="Обычная форма 1С (Form.bin)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("info")
    a.add_argument("form_bin")
    a.add_argument("--limit", type=int, default=150)
    a = sub.add_parser("module")
    a.add_argument("form_bin")
    a.add_argument("--out")
    a = sub.add_parser("set-module")
    a.add_argument("form_bin")
    a.add_argument("new_module")
    a.add_argument("--expected-sha256", required=True)
    a.add_argument("--apply", action="store_true")
    a = sub.add_parser("verify")
    a.add_argument("form_bin")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "info":
            return cmd_info(a.form_bin, a.limit)
        if a.cmd == "module":
            return cmd_module(a.form_bin, a.out)
        if a.cmd == "set-module":
            return cmd_set_module(a.form_bin, a.new_module, a.expected_sha256, a.apply)
        return cmd_verify(a.form_bin)
    except (OSError, ValueError) as ex:
        print("ОШИБКА: %s" % ex)
        return 2


if __name__ == "__main__":
    sys.exit(main())
