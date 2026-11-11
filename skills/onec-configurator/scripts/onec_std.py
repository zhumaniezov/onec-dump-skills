#!/usr/bin/env python3
"""onec_std - стандарты разработки 1С офлайн (каталог standards/ скилла).

  show <номер> [--clause 1.2] [--limit N]   текст стандарта или пункта (#std657, std657, 657)
  find <слова> [--limit N]                   поиск по названиям и тексту, лучшие совпадения
  diag <код>                                 какие стандарты и пункты стоят за проверкой:
                                             acc:254 | 254 | bslls:VirtualTableCallWithoutParameters |
                                             VirtualTableCallWithoutParameters | v8cs:ql-virtual-table-filters
  list [--limit N] [--offset N]              номера и названия

Права на тексты — фирма «1С»; первоисточник каждого стандарта — ссылка на ИТС в его начале.
Только стандартная библиотека.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STD_DIR = os.path.join(os.path.dirname(HERE), "standards")
WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+")


def load_index(base):
    p = os.path.join(base, "index.json")
    if not os.path.isfile(p):
        sys.exit("Нет офлайн-копии стандартов (%s). Обновить: sync_standards.py --clone" % p)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def read_std(base, num):
    p = os.path.join(base, "std", "%d.md" % num)
    with open(p, encoding="utf-8") as f:
        return f.read()


def parse_num(s):
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None


def out(lines, limit, offset=0):
    part = lines[offset:offset + limit]
    print("\n".join(part))
    if len(lines) > offset + limit:
        print("... ещё %d строк (--offset %d или --clause)" % (len(lines) - offset - limit, offset + limit))


def clause_text(text, clause):
    """Пункт «###### 2.1.» и его подпункты (2.1.x) до следующего пункта того же или высшего уровня."""
    c = clause.strip().rstrip(".")
    lines = text.split("\n")
    start = None
    res = []
    for i, ln in enumerate(lines):
        m = re.match(r"^###### (\d+(?:\.\d+)*)\.?\s*$", ln)
        if m:
            cur = m.group(1)
            if start is None and cur == c:
                start = i
            elif start is not None and not (cur + ".").startswith(c + "."):
                break
        elif start is not None and re.match(r"^###### Источники", ln):
            break
        if start is not None:
            res.append(ln)
    return res


def cmd_show(base, idx, spec, clause, limit, offset):
    num = parse_num(spec)
    meta = next((s for s in idx["standards"] if s["num"] == num), None)
    if meta is None:
        print("Стандарт #std%s не найден в офлайн-копии. Проверить на ИТС: https://its.1c.ru/db/v8std" % (num if num is not None else spec))
        return 3
    text = read_std(base, num)
    if clause:
        part = clause_text(text, clause)
        if not part:
            have = sorted(meta["clauses"]) or re.findall(r"^###### (\d+(?:\.\d+)*)\.", text, re.M)
            print("Пункт %s не найден в #std%d. Пункты: %s" % (clause, num, ", ".join(dict.fromkeys(have))))
            return 3
        lines = ["#std%d. %s — пункт %s  (ИТС: %s)" % (num, meta["title"], clause, meta["its"]), ""] + part
    else:
        lines = text.rstrip("\n").split("\n")
    out(lines, limit, offset)
    return 0


def _stems(text):
    return [w.lower()[:6] for w in WORD.findall(text) if len(w) > 2]


def cmd_find(base, idx, query, limit):
    q = _stems(query)
    if not q:
        print("Пустой запрос.")
        return 2
    rows = []
    for s in idx["standards"]:
        text = read_std(base, s["num"])
        title = set(_stems(s["title"]))
        body = _stems(text)
        bset = set(body)
        if not all(w in bset or w in title for w in q):
            hit = sum(1 for w in q if w in bset or w in title)
            if hit < max(1, len(q) - 1):
                continue
        score = sum(10 for w in q if w in title) + sum(min(body.count(w), 5) for w in q)
        if s["llms_ignore"]:
            score -= 3
        line = ""
        for ln in text.split("\n")[3:]:
            l_st = set(_stems(ln))
            if sum(1 for w in q if w in l_st) >= min(2, len(q)) and not ln.startswith(("```", "    ", "Проверки", "#", ">")):
                line = ln.strip()
                break
        rows.append((score, s, line))
    rows.sort(key=lambda r: -r[0])
    if not rows:
        print("Ничего не найдено. Попробуйте другие слова или поиск на ИТС: https://its.1c.ru/db/v8std")
        return 0
    print("Найдено %d, показаны первые %d:" % (len(rows), min(limit, len(rows))))
    for score, s, line in rows[:limit]:
        print("#std%d  %s" % (s["num"], s["title"]))
        if line:
            print("    %s" % (line[:160] + ("…" if len(line) > 160 else "")))
    print("Текст: onec_std.py show <номер> [--clause N]")
    return 0


def cmd_diag(idx, code):
    c = code.strip()
    diags = idx["diagnostics"]
    keys = []
    if c in diags:
        keys = [c]
    else:
        low = c.lower()
        for k in diags:
            kk = k.lower()
            if kk == low or kk.split(":", 1)[-1] == low or (low.isdigit() and kk == "acc:" + low) or ("acc " + low) == kk.replace(":", " "):
                keys.append(k)
        if not keys:
            keys = [k for k in diags if low in k.lower()][:10]
    if not keys:
        print("Проверка %s не связана со стандартами в офлайн-копии." % code)
        return 3
    for k in keys:
        print("%s — %s" % (k, diags.get(k) or "(название не указано)"))
        for s in idx["standards"]:
            cl = [cn or "весь стандарт" for cn, codes in s["clauses"].items() if k in codes]
            if cl:
                print("  #std%d %s — пункт %s  (ИТС: %s)" % (s["num"], s["title"], ", ".join(cl), s["its"]))
    return 0


def cmd_list(idx, limit, offset):
    lines = ["#std%d  %s%s" % (s["num"], s["title"], "" if not s["llms_ignore"] else "  [в источнике скрыт от ИИ-индекса]")
             for s in sorted(idx["standards"], key=lambda x: x["num"])]
    lines.insert(0, "Стандартов: %d (снимок v8std %s от %s; права на тексты — фирма «1С»)"
                 % (len(idx["standards"]), idx["commit"][:7], idx["date"]))
    out(lines, limit, offset)
    return 0


def main(argv=None):
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="Стандарты разработки 1С офлайн")
    ap.add_argument("--dir", default=STD_DIR, help="каталог standards/")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("show")
    a.add_argument("num")
    a.add_argument("--clause")
    a.add_argument("--limit", type=int, default=150)
    a.add_argument("--offset", type=int, default=0)
    a = sub.add_parser("find")
    a.add_argument("words", nargs="+")
    a.add_argument("--limit", type=int, default=10)
    a = sub.add_parser("diag")
    a.add_argument("code")
    a = sub.add_parser("list")
    a.add_argument("--limit", type=int, default=150)
    a.add_argument("--offset", type=int, default=0)
    a = ap.parse_args(argv)
    idx = load_index(a.dir)
    if a.cmd == "show":
        return cmd_show(a.dir, idx, a.num, a.clause, a.limit, a.offset)
    if a.cmd == "find":
        return cmd_find(a.dir, idx, " ".join(a.words), a.limit)
    if a.cmd == "diag":
        return cmd_diag(idx, a.code)
    return cmd_list(idx, a.limit, a.offset)


if __name__ == "__main__":
    sys.exit(main())
