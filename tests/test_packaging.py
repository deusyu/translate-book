import fnmatch
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
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


class ClawHubIgnoreTest(unittest.TestCase):
    """The package published to ClawHub is SKILL.md, LICENSE and scripts/.

    ClawHub audits every file it ships, so `.clawhubignore` keeps out the docs,
    assets and tests that only serve the GitHub repository.
    """

    REQUIRED_RULES = {"/README*.md", "/AGENTS.md", "/CLAUDE.md", "/assets/", "/tests/"}

    def setUp(self):
        text = (REPO_ROOT / ".clawhubignore").read_text(encoding="utf-8")
        self.rules = [line.strip() for line in text.splitlines()
                      if line.strip() and not line.startswith("#")]

    def test_keeps_docs_assets_and_tests_out(self):
        self.assertEqual(self.REQUIRED_RULES - set(self.rules), set())

    def test_no_rule_excludes_skill_md_license_or_scripts(self):
        skill_files = ["SKILL.md", "LICENSE"] + sorted(
            path.relative_to(REPO_ROOT).as_posix()
            for path in (REPO_ROOT / "scripts").rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
        self.assertIn("scripts/convert.py", skill_files)
        hits = [(rule, path) for rule in self.rules if not rule.startswith("!")
                for path in skill_files if self.may_exclude(rule, path)]
        self.assertEqual(hits, [])

    @staticmethod
    def may_exclude(rule, path):
        """Loose gitignore match that errs towards a hit: anchors and trailing `/`
        are dropped, every level of the path is tried, and `*` may cross `/`."""
        pattern = rule.strip("/")
        variants = {pattern, pattern.replace("/**/", "/"), pattern.removeprefix("**/")}
        parts = path.split("/")
        names = {"/".join(parts[:end]) for end in range(1, len(parts) + 1)} | set(parts)
        return any(fnmatch.fnmatchcase(name, variant) for name in names for variant in variants)


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


class SkillShellCommandTest(unittest.TestCase):
    """Book text reaches the scripts through files, never inside shell quotes.

    The SKILL.md commands run here the way an agent runs them, with text that
    breaks or runs inside quotes: apostrophes, `"`, `$(...)`, backticks, `$HOME`.
    """

    BOOK_TEXT = "O'Brien's \"tale\" $(touch pwned) `touch pwned-tick` $HOME \\ end"
    EXPORT_NAME = "O'Brien's $(touch pwned) `touch pwned-tick` $HOME"
    # Placeholders a bash block may put in a command; none of them is book text.
    SAFE_PLACEHOLDERS = {"file_path", "target_lang", "temp_dir", "NNNN"}

    def setUp(self):
        text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.bash_lines = [
            line.strip()
            for block in re.findall(r"```bash\n(.*?)```", text, re.S)
            for line in block.splitlines()
            if line.strip()
        ]
        self.shells = [s for s in ("sh", "bash", "zsh") if shutil.which(s)]

    def command(self, needle):
        lines = [line for line in self.bash_lines if needle in line]
        self.assertEqual(len(lines), 1, needle)
        return lines[0]

    def run_command(self, shell, cmd, cwd, base_dir):
        if cmd.startswith("python3 "):
            cmd = shlex.quote(sys.executable) + cmd[len("python3"):]
        cmd = cmd.replace("{baseDir}", shlex.quote(str(base_dir)))
        cmd = cmd.replace("<temp_dir>", "book_temp")
        return subprocess.run([shell, "-c", cmd], cwd=cwd, capture_output=True, text=True)

    def test_bash_blocks_take_no_book_text(self):
        for line in self.bash_lines:
            with self.subTest(line=line):
                self.assertNotRegex(line, r"\b(echo|printf)\b|<<")
                self.assertLessEqual(set(re.findall(r"<(\w+)>", line)), self.SAFE_PLACEHOLDERS)

    def test_apply_merge_reads_book_text_from_a_file(self):
        if not self.shells:
            self.skipTest("no POSIX shell")
        cmd = self.command("merge_meta.py apply-merge")
        glossary = {
            "version": 2,
            "terms": [{"id": "Tai", "source": "Tai", "target": "泰", "category": "person",
                       "aliases": [], "gender": "unknown", "confidence": "medium",
                       "frequency": 0, "evidence_refs": [], "notes": ""}],
            "high_frequency_top_n": 20,
            "applied_meta_hashes": {},
        }
        meta = {
            "schema_version": 1,
            "new_entities": [{"source": self.BOOK_TEXT, "target_proposal": self.BOOK_TEXT,
                              "category": "person", "evidence": self.BOOK_TEXT}],
            "alias_hypotheses": [],
            "attribute_hypotheses": [],
            "used_term_sources": [],
            "conflicts": [{"entity_source": "Tai", "field": "target", "injected": "泰",
                           "observed_better": self.BOOK_TEXT, "evidence": self.BOOK_TEXT}],
        }
        for shell in self.shells:
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as tmp:
                temp_dir = Path(tmp) / "book_temp"
                temp_dir.mkdir()
                (temp_dir / "glossary.json").write_text(json.dumps(glossary), encoding="utf-8")
                (temp_dir / "output_chunk0001.meta.json").write_text(json.dumps(meta), encoding="utf-8")
                prepared = subprocess.run(
                    [sys.executable, str(REPO_ROOT / "scripts" / "merge_meta.py"),
                     "prepare-merge", str(temp_dir)],
                    capture_output=True, text=True, check=True,
                )
                proposal = json.loads(prepared.stdout)
                decisions = {
                    "auto_apply": proposal["auto_apply"],
                    "decisions": [dict(d, choice="record_in_notes")
                                  for d in proposal["decisions_needed"]],
                    "consumed_chunk_ids": proposal["consumed_chunk_ids"],
                }
                (temp_dir / "merge_decisions.json").write_text(json.dumps(decisions), encoding="utf-8")

                result = self.run_command(shell, cmd, tmp, REPO_ROOT)

                self.assertEqual(result.returncode, 0, result.stderr)
                saved = json.loads((temp_dir / "glossary.json").read_text(encoding="utf-8"))
                terms = {t["source"]: t for t in saved["terms"]}
                self.assertEqual(terms[self.BOOK_TEXT]["target"], self.BOOK_TEXT)
                self.assertIn(f"evidence={self.BOOK_TEXT!r}", terms["Tai"]["notes"])
                self.assertIn("chunk0001", saved["applied_meta_hashes"])
                self.assertEqual(list(Path(tmp).glob("pwned*")), [])

    def test_build_reads_title_and_export_name_from_files(self):
        if not self.shells:
            self.skipTest("no POSIX shell")
        cmd = self.command('merge_and_build.py --temp-dir "<temp_dir>" --title')
        for shell in self.shells:
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as tmp:
                # A stand-in merge_and_build.py that records the arguments it gets.
                skill_dir = Path(tmp) / "skill"
                (skill_dir / "scripts").mkdir(parents=True)
                (skill_dir / "scripts" / "merge_and_build.py").write_text(
                    "import json, sys\n"
                    "with open('argv.json', 'w', encoding='utf-8') as f:\n"
                    "    json.dump(sys.argv[1:], f)\n",
                    encoding="utf-8",
                )
                temp_dir = Path(tmp) / "book_temp"
                temp_dir.mkdir()
                (temp_dir / "title.txt").write_text(self.BOOK_TEXT + "\n", encoding="utf-8")
                (temp_dir / "export_name.txt").write_text(self.EXPORT_NAME + "\n", encoding="utf-8")

                result = self.run_command(shell, cmd, tmp, skill_dir)

                self.assertEqual(result.returncode, 0, result.stderr)
                argv = json.loads((Path(tmp) / "argv.json").read_text(encoding="utf-8"))
                self.assertEqual(argv, ["--temp-dir", "book_temp", "--title", self.BOOK_TEXT,
                                        "--export-name", self.EXPORT_NAME])
                self.assertEqual(list(Path(tmp).glob("pwned*")), [])


if __name__ == "__main__":
    unittest.main()
