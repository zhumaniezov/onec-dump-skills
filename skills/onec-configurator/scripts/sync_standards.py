#!/usr/bin/env python3
"""Обновить офлайн-копию стандартов разработки 1С (каталог standards/ скилла).

Источник — открытый проект v8std (https://github.com/zeegin/v8std), повторяющий
тексты системы стандартов ИТС (https://its.1c.ru/db/v8std). Права на тексты
стандартов принадлежат фирме «1С»; копия хранится для офлайн-работы и удаляется
по требованию правообладателя.

  sync_standards.py --from <checkout v8std> [--out <каталог standards>]
  sync_standards.py --clone [--out ...]      # git clone --depth 1 во временный каталог

Только стандартная библиотека. Каталог --out пересоздаётся целиком.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO_URL = "https://github.com/zeegin/v8std"
ITS_URL = "https://its.1c.ru/db/v8std#content:%s"

CHIP_BLOCK = re.compile(r"<!-- (diagnostic|fix)-backlinks:start clause=([^ ]+) -->(.*?)<!-- \1-backlinks:end clause=\2 -->", re.S)
CHIP = re.compile(r'<a class="diagnostic-chip[^"]*" href="[^"]*">([^<]+)</a>')
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)(\{[^}]*\})?")
TITLE = re.compile(r"^# (.+)$", re.M)


def clean(text, num):
    """-> (markdown, {пункт: [коды]}, llms_ignore)."""
    ignore = False
    m = FRONT.match(text)
    if m:
        ignore = "ignore: true" in m.group(1)
        text = text[m.end():]
    clauses = {}

    def chips(mm):
        kind, clause, body = mm.group(1), mm.group(2), mm.group(3)
        codes = CHIP.findall(body)
        if kind == "fix":
            return ""
        key = "" if clause.startswith("std") else clause
        clauses.setdefault(key, []).extend(codes)
        return "Проверки: " + ", ".join("`%s`" % c for c in codes) if codes else ""

    text = CHIP_BLOCK.sub(chips, text)
    text = IMAGE.sub(lambda mm: "*[рисунок «%s» — см. ИТС]*" % mm.group(1) if mm.group(1) else "*[рисунок — см. ИТС]*", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    return text, clauses, ignore


def diag_titles(root):
    out = {}
    base = os.path.join(root, "docs", "diagnostics")
    for kind in ("acc", "bslls", "v8-code-style"):
        d = os.path.join(base, kind)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".md") or fn == "index.md":
                continue
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                head = f.read(2000)
            code = re.search(r"^###### (\S+)", head, re.M)
            title = TITLE.search(head)
            if code and title:
                out[code.group(1)] = title.group(1).strip()
    return out


def git_info(root):
    try:
        r = subprocess.run(["git", "-C", root, "log", "-1", "--format=%H %cs"], capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            h, d = r.stdout.split()
            return h, d
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return "unknown", datetime.date.today().isoformat()


README = """# Стандарты разработки 1С — офлайн-копия

Тексты системы стандартов и методик разработки конфигураций для платформы
1С:Предприятие 8 (ИТС, https://its.1c.ru/db/v8std). **Права на тексты принадлежат
фирме «1С».** Копия сделана из открытого проекта v8std ({repo}, коммит `{commit}`
от {date}) для работы агента без доступа к сети. По требованию правообладателя
каталог будет удалён из репозитория.

Первоисточник — ИТС: у каждого стандарта в начале файла указана ссылка на его
страницу. При сомнении в актуальности формулировки сверяться с ИТС.

Состав: {count} стандартов (`std/NNN.md`), `index.json` — номера, названия,
ссылки на ИТС, коды проверок по пунктам (АПК `acc:N`, BSL Language Server `bslls:Имя`,
1C:Code style `v8cs:имя`) и их названия. Рисунки не копировались.

Обновление: `python scripts/sync_standards.py --clone` (нужен git) или
`--from <checkout v8std>`. Вручную файлы не править.
"""


def build(src, out):
    std_dir = os.path.join(src, "docs", "std")
    if not os.path.isdir(std_dir):
        sys.exit("Не найден %s: укажите checkout v8std" % std_dir)
    commit, date = git_info(src)
    tmp = out + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "std"))
    index = {"source": REPO_URL, "commit": commit, "date": date, "its": "https://its.1c.ru/db/v8std",
             "rights": "© фирма «1С»", "standards": [], "diagnostics": {}}
    count = 0
    for fn in sorted(os.listdir(std_dir), key=lambda x: (len(x), x)):
        m = re.match(r"^(\d+)\.md$", fn)
        if not m:
            continue
        num = m.group(1)
        with open(os.path.join(std_dir, fn), encoding="utf-8") as f:
            raw = f.read()
        body, clauses, ignore = clean(raw, num)
        title = TITLE.search(body)
        title = title.group(1).strip() if title else "#std" + num
        its = ITS_URL % num
        header = ("> Стандарт #std%s. Первоисточник: [ИТС](%s). Офлайн-копия из v8std `%s` от %s. "
                  "Права на текст — фирма «1С».\n\n" % (num, its, commit[:7], date))
        with open(os.path.join(tmp, "std", num + ".md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(header + body)
        index["standards"].append({"num": int(num), "title": title, "its": its,
                                   "clauses": clauses, "llms_ignore": ignore})
        count += 1
    titles = diag_titles(src)
    used = set(c for s in index["standards"] for codes in s["clauses"].values() for c in codes)
    index["diagnostics"] = {c: titles.get(c, "") for c in sorted(used)}
    with open(os.path.join(tmp, "index.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, ensure_ascii=False, indent=0, sort_keys=False)
    with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(README.format(repo=REPO_URL, commit=commit[:7], date=date, count=count))
    shutil.rmtree(out, ignore_errors=True)
    os.replace(tmp, out)
    print("Стандартов: %d; кодов проверок: %d; источник %s @ %s (%s) -> %s"
          % (count, len(index["diagnostics"]), REPO_URL, commit[:7], date, out))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Обновить офлайн-копию стандартов 1С")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--from", dest="src")
    g.add_argument("--clone", action="store_true")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "standards"))
    a = ap.parse_args(argv)
    if a.src:
        return build(os.path.abspath(a.src), os.path.abspath(a.out))
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run(["git", "clone", "--depth", "1", REPO_URL, d + "/v8std"], timeout=600)
        if r.returncode != 0:
            sys.exit("git clone не удался")
        return build(d + "/v8std", os.path.abspath(a.out))


if __name__ == "__main__":
    sys.exit(main())
