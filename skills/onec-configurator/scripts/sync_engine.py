#!/usr/bin/env python3
"""Обновить движок генераторов (engine/) и справочники к командам onec_gen.py (references/gen/).

  sync_engine.py --from <checkout>   взять файлы из локальной копии репозитория-источника
  sync_engine.py --clone             скачать источник (git) во временный каталог
  sync_engine.py --check ...         только сравнить engine/ с источником, ничего не менять
  --commit <sha>                     ревизия источника (по умолчанию — закреплённая в manifest.json)

Источник — открытый проект с Python-версиями генераторов 1С (лицензия MIT). Скрипты переносятся
без изменений, их хеши записываются в engine/manifest.json. Документация переносится с заменой
имён команд источника на наши (onec_gen.py <область> <действие>). Вручную engine/ и
references/gen/ не править. Python 3.10+, только стандартная библиотека; git — для --clone и --commit.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import onec_gen  # noqa: E402

SKILL = os.path.dirname(HERE)
ENGINE = os.path.join(SKILL, "engine")
REFS = os.path.join(SKILL, "references", "gen")
REPO_URL = "https://github.com/Nikolay-Shirokov/cc-1c-skills"
SPECS = {"meta-dsl-spec.md": "spec-object.md", "form-dsl-spec.md": "spec-form.md", "skd-dsl-spec.md": "spec-dcs.md",
         "mxl-dsl-spec.md": "spec-sheet.md", "role-dsl-spec.md": "spec-role.md", "xdto-dsl-spec.md": "spec-xdto.md"}
# Справочные (без скриптов) разделы источника, полезные агенту, — под нашими именами
GUIDES = {"epf-bsp-init": "epf-bsp-registration", "epf-bsp-add-command": "epf-bsp-command"}
# Команды источника, которым у нас соответствует другой инструмент или которых нет
OTHER = {
    "db-load-xml": "загрузка в тестовую базу (`onec_env.py profile cf-check`)",
    "db-load-git": "частичная загрузка в тестовую базу (`onec_env.py changed` + `profile cf-check --list-file`)",
    "db-update": "обновление конфигурации БД (`onec_env.py profile ... --update-db`)",
    "db-create": "создание информационной базы", "db-run": "запуск 1С:Предприятия",
    "db-list": "список баз (`onec_env.py discover`)", "db-dump-xml": "выгрузка конфигурации в файлы",
    "db-load-cf": "загрузка .cf в базу", "db-dump-cf": "выгрузка .cf", "db-dump-dt": "выгрузка .dt",
    "db-load-dt": "загрузка .dt", "db-cfe-admin": "управление расширениями базы", "db-repo": "хранилище конфигурации",
    "epf-build": "сборка EPF/ERF (`onec_env.py profile epf-build`)", "erf-build": "сборка EPF/ERF (`onec_env.py profile epf-build`)",
    "epf-dump": "разборка EPF/ERF в файлы", "erf-dump": "разборка EPF/ERF в файлы",
    "erf-validate": "`erf check`", "v8-xsd-fetch": "XSD-схемы платформы", "img-grid": "сетка по скриншоту",
    "web-info": "веб-публикация", "web-publish": "веб-публикация", "web-unpublish": "веб-публикация",
    "web-stop": "веб-публикация", "web-test": "веб-тесты",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise SystemExit("git %s: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def engine_to_cmd():
    """Имя движка -> наша команда (первая по порядку, если движок у нескольких команд)."""
    m = {}
    for k, v in onec_gen.COMMANDS.items():
        m.setdefault(v[0], k)
    return m


def slug(cmd):
    return cmd.replace(" ", "-")


def transform(text, own_slug="", own_cmd=""):
    """Документация источника -> наша: имена команд и способ вызова заменяются на onec_gen.py."""
    e2c = engine_to_cmd()
    names = sorted(set(e2c) | set(OTHER) | set(GUIDES), key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in names)

    def call(m):
        n = m.group(1)
        if n in e2c:
            return "\x00GEN\x00 %s" % e2c[n]
        if n in OTHER:
            return "# %s — у нас нет такой команды" % OTHER[n]
        return "\x00GEN\x00 %s" % own_cmd                     # init.ps1, add-template.ps1 ...

    # вызов скрипта: powershell.exe ... -File ".../<файл>.ps1" и python ".../<файл>.py"
    text = re.sub(r'powershell(?:\.exe)?\s+(?:-NoProfile\s+)?(?:-ExecutionPolicy\s+\S+\s+)?-File\s+"?[^"\s]*?(?:/|\\)([\w-]+)\.ps1"?',
                  call, text)
    text = re.sub(r'python3?\s+"?[^"\s]*?scripts(?:/|\\)([\w-]+)\.py"?', call, text)
    text = text.replace("```powershell", "```text")
    # пути внутри набора источника
    text = re.sub(r"\.claude/skills/(%s)/" % alt,
                  lambda m: "references/gen/%s/" % slug(e2c.get(m.group(1), m.group(1))), text)
    text = re.sub(r"`?scripts/(?!onec_|sync_)[\w-]+\.(?:ps1|py)`?", "код движка", text)
    text = text.replace("\x00GEN\x00", 'python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py"')
    for up, ours in SPECS.items():
        text = text.replace(up, ours)
    # ссылки на документы источника, которые мы не переносим, — без ссылки, только текст
    text = re.sub(r"\[([^\]]+)\]\((?:\.\./)*(?:docs/)?(?:1c-[\w-]+-spec|[\w-]+-guide|[\w-]+-spec)\.md(?:#[^)]*)?\)",
                  lambda m: m.group(1) if not m.group(0).endswith(tuple("(%s)" % v for v in SPECS.values())) else m.group(0), text)
    if own_slug:
        text = text.replace("](SKILL.md)", "](../%s.md)" % own_slug)
    text = text.replace("SKILL.md", "основной справке")

    def name(m):
        n = m.group(2)
        if n in e2c:
            return "`%s`" % e2c[n]
        if n in GUIDES:
            return "[%s](%s.md)" % (GUIDES[n], GUIDES[n])
        return OTHER[n]

    # упоминания команд: /имя, `имя`, `/имя` и просто имя (но не имя файла: «имя.json»)
    text = re.sub(r"(`?/?)(%s)\b(?![-\w])(?!\.\w)`?" % alt, name, text)
    # относительные ссылки на файлы раздела — в каталог раздела
    if own_slug:
        text = re.sub(r"\]\((?!https?:|#|/|%s/)([\w./-]+\.(?:md|json))\)" % re.escape(own_slug),
                      lambda m: "](%s/%s)" % (own_slug, m.group(1)), text)
    return text


def strip_front(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            return text[end + 4:].lstrip("\n")
    return text


HEADER = ("> Справка к `onec_gen.py %s`. Основано на документации открытого проекта (лицензия MIT,\n"
          "> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.\n\n")


def build(src, commit, check=False):
    skills = os.path.join(src, ".claude", "skills")
    files = {}
    plan = []                                    # (откуда, куда относительно ENGINE)
    entries = {}
    for e in onec_gen.ENGINES:
        pys = sorted(fn for fn in os.listdir(os.path.join(skills, e, "scripts")) if fn.endswith(".py"))
        if len(pys) != 1:
            raise SystemExit("%s: ожидался один .py в scripts, найдено %d" % (e, len(pys)))
        entries[e] = "%s/scripts/%s" % (e, pys[0])
        plan.append((os.path.join(skills, e, "scripts", pys[0]), entries[e]))
    preset_dir = os.path.join(skills, "form-compile", "presets")
    for fn in sorted(os.listdir(preset_dir)):
        if fn.endswith(".json"):
            plan.append((os.path.join(preset_dir, fn), "form-compile/presets/" + fn))
    plan.append((os.path.join(src, "LICENSE"), "LICENSE"))
    if check:
        m = onec_gen.load_manifest() or {"files": {}}
        diff = []
        for a, rel in plan:
            mine = os.path.join(ENGINE, *rel.split("/"))
            if not os.path.isfile(mine) or sha256(mine) != sha256(a):
                diff.append(rel)
        extra = sorted(set(m["files"]) - {rel for _a, rel in plan})
        print("Ревизия источника %s: отличаются %d, лишние в манифесте %d" % (commit[:10], len(diff), len(extra)))
        for d in diff + extra:
            print("  " + d)
        return 1 if diff or extra else 0

    if os.path.isdir(ENGINE):
        shutil.rmtree(ENGINE)
    for a, rel in plan:
        dst = os.path.join(ENGINE, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(a, dst)
        files[rel] = sha256(dst)
    lxml = sorted(e for e in onec_gen.ENGINES
                  if re.search(r"^\s*(?:from|import)\s+lxml", open(os.path.join(ENGINE, *entries[e].split("/")),
                                                                   encoding="utf-8-sig").read(), re.M))
    manifest = {"schema": 1, "repository": REPO_URL, "commit": commit, "license": "MIT",
                "note": "Скрипты перенесены без изменений; обновлять только scripts/sync_engine.py.",
                "requires_lxml": lxml, "entries": entries, "files": files}
    with open(os.path.join(ENGINE, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # справочники
    if os.path.isdir(REFS):
        shutil.rmtree(REFS)
    os.makedirs(REFS)
    for key, (e, _kind, _p, desc) in onec_gen.COMMANDS.items():
        sk = os.path.join(skills, e)
        s = slug(key)
        text = strip_front(open(os.path.join(sk, "SKILL.md"), encoding="utf-8").read())
        text = transform(re.sub(r"^# .*\n", "\x01TITLE\x01\n", text, count=1), s, key)
        text = text.replace("\x01TITLE\x01\n", "# onec_gen.py %s — %s\n\n" % (key, desc) + HEADER % key, 1)
        with open(os.path.join(REFS, s + ".md"), "w", encoding="utf-8") as f:
            f.write(text)
        for dp, _dn, fns in os.walk(sk):
            if os.path.relpath(dp, sk).split(os.sep)[0] in ("scripts", "presets", "examples"):
                continue
            for fn in fns:
                if fn.endswith(".md") and not (dp == sk and fn == "SKILL.md"):
                    rel = os.path.relpath(os.path.join(dp, fn), sk)
                    dst = os.path.join(REFS, s, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    depth = rel.count(os.sep)
                    back = "../" * (depth + 1)
                    body = transform(open(os.path.join(dp, fn), encoding="utf-8").read(), "", key)
                    body = body.replace("](../%s.md)" % s, "](%s%s.md)" % (back, s)).replace("](основной справке)", "](%s%s.md)" % (back, s))
                    with open(dst, "w", encoding="utf-8") as f:
                        f.write(HEADER % key + body)
    for up, ours in GUIDES.items():
        text = strip_front(open(os.path.join(skills, up, "SKILL.md"), encoding="utf-8").read())
        with open(os.path.join(REFS, ours + ".md"), "w", encoding="utf-8") as f:
            f.write(HEADER.replace("`onec_gen.py %s`", "генераторам") + transform(text))
    for up, ours in SPECS.items():
        p = os.path.join(src, "docs", up)
        if os.path.isfile(p):
            with open(os.path.join(REFS, ours), "w", encoding="utf-8") as f:
                f.write(HEADER.replace("`onec_gen.py %s`", "формату описаний JSON") + transform(open(p, encoding="utf-8").read()))
    print("Движок: ревизия %s, файлов %d, нужен lxml: %d" % (commit[:10], len(files), len(lxml)))
    print("Справочники: %s" % REFS)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--from", dest="src", help="локальная копия репозитория-источника")
    g.add_argument("--clone", action="store_true", help="скачать источник")
    ap.add_argument("--commit", help="ревизия (по умолчанию закреплённая)")
    ap.add_argument("--check", action="store_true", help="только сравнить")
    a = ap.parse_args(argv)
    m = onec_gen.load_manifest()
    commit = a.commit or (m or {}).get("commit")
    tmp = None
    try:
        if a.clone:
            tmp = tempfile.mkdtemp(prefix="onec-engine-")
            src = os.path.join(tmp, "src")
            git("clone", "--quiet", REPO_URL, src)
        else:
            src = os.path.abspath(a.src)
        if commit:
            if git("-C", src, "rev-parse", "HEAD") != commit:
                git("-C", src, "checkout", "--quiet", commit)
        else:
            commit = git("-C", src, "rev-parse", "HEAD")
        return build(src, commit, a.check)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
