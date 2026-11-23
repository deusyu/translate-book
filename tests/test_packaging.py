import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class MarketplaceManifestTest(unittest.TestCase):
    def setUp(self):
        path = REPO_ROOT / ".claude-plugin" / "marketplace.json"
        self.manifest = json.loads(path.read_text(encoding="utf-8"))

    def test_single_plugin_points_at_skill_root(self):
        plugins = self.manifest["plugins"]
        self.assertEqual(len(plugins), 1)
        plugin = plugins[0]
        self.assertEqual(plugin["name"], "translate-book")
        source = (REPO_ROOT / plugin["source"]).resolve()
        self.assertTrue((source / "SKILL.md").is_file())
        for skill in plugin.get("skills", []):
            self.assertTrue((source / skill / "SKILL.md").is_file())

    def test_no_version_field(self):
        self.assertNotIn("version", self.manifest)
        for plugin in self.manifest["plugins"]:
            self.assertNotIn("version", plugin)


class SkillDefinitionTest(unittest.TestCase):
    def setUp(self):
        self.text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")

    def test_frontmatter_fields_are_single_line(self):
        lines = self.text.splitlines()
        self.assertEqual(lines[0], "---")
        end = lines.index("---", 1)
        for line in lines[1:end]:
            self.assertRegex(line, r"^[a-z][a-z-]*: \S")

    def test_defines_base_dir(self):
        self.assertIn(
            "`{baseDir}` below is the directory containing this SKILL.md.",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()
