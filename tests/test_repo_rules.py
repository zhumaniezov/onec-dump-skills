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
