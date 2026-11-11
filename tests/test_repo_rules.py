"""Правила репозитория из AGENTS.md, которые проверяются автоматически (локально и в GitHub Actions)."""
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
STD = os.path.join(SKILLS, "onec-configurator", "standards", "std")


def frontmatter(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise AssertionError("нет frontmatter: " + path)
    fields = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        fields[k.strip()] = v.strip().strip('"')
    return fields, text


class SkillRulesTest(unittest.TestCase):
    def test_skills(self):
        found = [d for d in os.listdir(SKILLS) if os.path.isfile(os.path.join(SKILLS, d, "SKILL.md"))]
        self.assertTrue(found)
        for d in found:
            path = os.path.join(SKILLS, d, "SKILL.md")
            fields, text = frontmatter(path)
            with self.subTest(skill=d):
                self.assertRegex(fields.get("name", ""), r"^onec-[a-z0-9]+(-[a-z0-9]+)*$")
                self.assertLessEqual(len(fields["name"]), 64)
                self.assertEqual(fields["name"], d)
                self.assertTrue(fields.get("description"))
                self.assertLessEqual(len(fields["description"]), 400, "описание длиннее 400 символов")
                self.assertLessEqual(text.count("\n"), 400, "SKILL.md длиннее 400 строк")
                for ref in re.findall(r"\]\((references/[^)#]+)", text):
                    self.assertTrue(os.path.isfile(os.path.join(SKILLS, d, ref)), "нет файла " + ref)


class TextPathsTest(unittest.TestCase):
    """Пути к .md, которые справочники называют в тексте (не ссылкой), ведут к существующим файлам —
    от папки скилла, по ним Claude и откроет файл. Ссылки Markdown — отдельно, от папки документа."""

    PATH_RX = re.compile(r"(?<![\w/(.\-#$])((?:[\w-]+/)+[\w.-]+\.md)\b")

    def test_text_paths_resolve(self):
        bad = []
        for d in os.listdir(SKILLS):
            skill = os.path.join(SKILLS, d)
            for dp, _dn, fns in os.walk(skill):
                if os.path.relpath(dp, skill).split(os.sep)[0] == "standards":
                    continue                                           # дословные тексты 1С
                for fn in fns:
                    if not fn.endswith(".md"):
                        continue
                    path = os.path.join(dp, fn)
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                    links = re.findall(r"\]\(([^)#\s]+\.md)", text)
                    for ref in links:
                        if not re.match(r"https?:", ref) and not os.path.isfile(os.path.join(dp, ref)):
                            bad.append("%s: ссылка %s" % (os.path.relpath(path, ROOT), ref))
                    text = re.sub(r"\]\([^)]*\)", "]()", text)
                    text = re.sub(r"https?://\S+", "", text)
                    for m in self.PATH_RX.finditer(text):
                        ref = m.group(1)
                        if not (os.path.isfile(os.path.join(skill, ref)) or os.path.isfile(os.path.join(dp, ref))
                                or os.path.isfile(os.path.join(ROOT, ref))):
                            bad.append("%s: путь %s" % (os.path.relpath(path, ROOT), ref))
        self.assertEqual(bad, [])


class StandardsRulesTest(unittest.TestCase):
    def test_every_standard_has_source_and_rightsholder(self):
        files = [f for f in os.listdir(STD) if f.endswith(".md")]
        self.assertTrue(files)
        for f in files:
            with open(os.path.join(STD, f), encoding="utf-8") as fh:
                first = fh.readline()
            with self.subTest(file=f):
                self.assertIn("Первоисточник: [ИТС](https://its.1c.ru/db/v8std#content:", first)
                self.assertIn("Права на текст — фирма «1С»", first)


class ManifestTest(unittest.TestCase):
    def test_plugin_and_marketplace_agree(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            plugin = json.load(f)
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as f:
            market = json.load(f)
        self.assertRegex(plugin["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual([p["name"] for p in market["plugins"]], [plugin["name"]])
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
            self.assertIn("## " + plugin["version"] + " ", f.read(), "версии нет в CHANGELOG.md")
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            self.assertIn("Версия **" + plugin["version"] + "**", f.read(), "версия в README.md не совпадает")


if __name__ == "__main__":
    unittest.main()
