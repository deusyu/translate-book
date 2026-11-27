import process_guard  # noqa: F401  first, so it guards every process the tests below start
import ast
import contextlib
import io
import json
import os
import re
import runpy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import process_guard_probe

TESTS_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import convert  # noqa: E402
import merge_and_build  # noqa: E402

TEST_MODULES = sorted(path.stem for path in TESTS_DIR.glob("test_*.py"))

# Ways that, if the guard failed, would only start a short-lived touch in this
# process; the others would split or replace the test run, so only the probe
# makes them.
IN_PROCESS_FORMS = [form for form in process_guard_probe.FORMS if form not in {
    "os.fork", "os.forkpty", "pty.fork", "multiprocessing fork", "multiprocessing spawn",
    "multiprocessing forkserver", "ProcessPoolExecutor", "os.execv"}]


def probe(module, *attempts, first=None):
    """Run the probe: (CompletedProcess, {attempt: its JSON line}, marker files made)."""
    marker_dir = Path(tempfile.mkdtemp(prefix="markers-"))
    try:
        result = process_guard.run_probe(*(("--first", first) if first else ()), module, str(marker_dir), *attempts)
        lines = {}
        for line in result.stdout.splitlines():
            record = json.loads(line)
            lines[record["attempt"]] = record
        return result, lines, sorted(path.name for path in marker_dir.iterdir())
    finally:
        shutil.rmtree(marker_dir)


def marker_name(attempt):
    return attempt.replace(" ", "_")


class EveryWayIsRefusedTest(unittest.TestCase):
    """Each way of starting a process fails once a test module is imported."""

    def assert_refused(self, record, result):
        self.assertIsNotNone(record, result.stderr[-2000:])
        self.assertRegex(record["error"] or "", r"^ProcessGuardError: ")

    def test_without_the_guard_every_way_starts_a_process(self):
        # The control for the tests below: no test module imported. os.execv
        # replaces the probe, so it goes last and prints nothing.
        result, lines, markers = probe("-", *process_guard_probe.FORMS)
        for form in process_guard_probe.FORMS:
            with self.subTest(form=form):
                error = lines.get(form, {}).get("error")
                if marker_name(form) not in markers and error and not error.startswith("ProcessGuardError"):
                    self.skipTest(f"this Python cannot start a process this way: {error}")
                self.assertIn(marker_name(form), markers, result.stderr[-2000:])

    def test_a_test_module_refuses_every_way(self):
        result, lines, markers = probe("test_meta", *process_guard_probe.FORMS)
        for form in process_guard_probe.FORMS:
            with self.subTest(form=form):
                self.assert_refused(lines.get(form), result)
        self.assertEqual(markers, [])
        self.assertEqual(result.returncode, 70, result.stderr[-2000:])

    def test_every_test_module_installs_the_guard_when_imported(self):
        # tests.test_meta is how `python3 -m unittest tests/test_meta.py` imports it.
        attempts = ("subprocess.run", "_posixsubprocess.fork_exec")
        for module in [*TEST_MODULES, "tests.test_meta"]:
            with self.subTest(module=module):
                result, lines, markers = probe(module, *attempts)
                for attempt in attempts:
                    self.assert_refused(lines.get(attempt), result)
                self.assertEqual(markers, [])

    def test_this_process_refuses_every_way(self):
        with tempfile.TemporaryDirectory() as tmp:
            for form in IN_PROCESS_FORMS:
                marker = Path(tmp, marker_name(form))
                with self.subTest(form=form), process_guard.refusal_expected(), \
                        self.assertRaises(process_guard.ProcessGuardError):
                    process_guard_probe.FORMS[form](str(marker))
            self.assertEqual(sorted(os.listdir(tmp)), [])

    def test_each_subinterpreter_module_is_refused_by_name(self):
        # Also the modules this Python lacks: `import` raises the audit event before it looks for one.
        for name in sorted(process_guard.SUBINTERPRETER_EXTENSIONS | process_guard.SUBINTERPRETER_MODULES):
            with self.subTest(name=name), process_guard.refusal_expected(), \
                    self.assertRaises(process_guard.ProcessGuardError):
                process_guard_probe.import_statement(name)

    def test_a_module_name_that_is_not_a_plain_str_is_refused(self):
        # A str subclass can answer == and rpartition() as it likes, so the guard does not ask it.
        class Disguised(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return False

            def rpartition(self, sep):
                return ("", sep, "harmless")

        with process_guard.refusal_expected(), self.assertRaises(process_guard.ProcessGuardError):
            process_guard_probe.import_statement(Disguised("_xxsubinterpreters"))

    def test_the_guard_does_not_start_after_a_subinterpreter_module(self):
        result, lines, markers = probe("test_meta", "subprocess.run", first="a subinterpreter module loaded")
        self.assertEqual((lines, markers), ({}, []))
        self.assertEqual(result.returncode, 1, result.stderr[-2000:])
        self.assertRegex(result.stderr, r"ProcessGuardError: _\w+ was imported before process_guard")

    def test_on_native_windows_the_tests_stop_as_the_scripts_do(self):
        with mock.patch.object(os, "name", "nt"), self.assertRaises(SystemExit) as stop:
            runpy.run_path(str(SCRIPT_DIR / "convert.py"), run_name="native_windows")
        result, lines, markers = probe("test_meta", "subprocess.run", first="native Windows")
        self.assertEqual((result.returncode, result.stdout, markers), (1, "", []))
        self.assertEqual(result.stderr, f"{stop.exception.code}\n")

    def test_a_runner_starts_only_the_process_it_set_up(self):
        result, lines, markers = probe("test_meta", *process_guard_probe.REPLACED_LAUNCHES)
        for attempt in process_guard_probe.REPLACED_LAUNCHES:
            with self.subTest(attempt=attempt):
                self.assert_refused(lines.get(attempt), result)
        self.assertEqual((markers, result.returncode), ([], 70))

    def test_a_test_that_swallows_a_refusal_fails_the_run(self):
        result, lines, _ = probe("test_meta")
        self.assertEqual((result.returncode, lines), (0, {}), result.stderr[-2000:])

        result, lines, markers = probe("test_meta", "except Exception in a test")
        self.assertEqual(lines["except Exception in a test"]["test"]["errors"], 1)
        self.assertEqual((markers, result.returncode), ([], 70))

        # Caught as a BaseException the refusal does not fail the test, but it is
        # recorded, and the run exits with 70 once unittest has reported.
        result, lines, markers = probe("test_meta", "except BaseException in a test")
        self.assertTrue(lines["except BaseException in a test"]["test"]["successful"])
        self.assertEqual((markers, result.returncode), ([], 70))
        self.assertIn("process_guard: 1 refused attempt(s)", result.stderr)


class RunnerTest(unittest.TestCase):
    def test_a_runner_process_gets_an_environment_of_its_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            hook = Path(tmp, "hook.sh")
            hook.write_text(f'touch "{tmp}/hooked"\n', encoding="utf-8")
            outside = {"BASH_ENV": str(hook), "ENV": str(hook), "CDPATH": tmp, "ZDOTDIR": tmp, "HOME": tmp,
                       "TMPDIR": tmp, "XDG_CONFIG_HOME": tmp, "PYTHONSTARTUP": str(hook), "PYTHONPATH": tmp,
                       "LD_PRELOAD": str(hook)}
            with mock.patch.dict(os.environ, outside):
                result, lines, _ = probe("-", "environment")
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            environment, cwd = lines["environment"]["environment"], lines["environment"]["cwd"]
            # In the C locale Python sets LC_CTYPE for itself (PEP 538); the runner did not give it.
            environment.pop("LC_CTYPE", None)
            if sys.platform == "darwin":
                # CoreFoundation sets __CF_USER_TEXT_ENCODING in each process that loads it; the runner did not give it.
                environment.pop("__CF_USER_TEXT_ENCODING", None)
            self.assertLessEqual(set(environment), process_guard.ENVIRONMENT_KEYS)
            self.assertEqual(environment["PATH"], process_guard.PATH)
            for key in process_guard.UNDER_ROOT:
                with self.subTest(key=key):
                    self.assertTrue(environment[key].startswith(process_guard.TEST_ROOT + os.sep))
                    self.assertNotEqual(environment[key], tmp)
            for key, below in process_guard.UNDER_HOME.items():
                self.assertEqual(environment[key], os.path.join(environment["HOME"], below))
            self.assertEqual(environment["ZDOTDIR"], environment["HOME"])
            self.assertTrue(os.path.realpath(cwd).startswith(process_guard.TEST_ROOT + os.sep))
            self.assertIsNone(process_guard.launch_problem(environment, cwd))
            self.assertFalse(Path(tmp, "hooked").exists())

    def test_launch_problem_names_what_is_wrong(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            home, cwd = Path(tmp, "home"), Path(tmp, "cwd")
            home.mkdir()
            cwd.mkdir()
            outside_link = Path(tmp, "link")
            outside_link.symlink_to(os.path.dirname(process_guard.TEST_ROOT))
            good = {"PATH": process_guard.PATH, "HOME": str(home), "ZDOTDIR": str(home), "TMPDIR": other,
                    **{key: str(home / below) for key, below in process_guard.UNDER_HOME.items()},
                    **process_guard.PYTHON_SETTINGS}
            self.assertIsNone(process_guard.launch_problem(good, cwd))
            self.assertIsNone(process_guard.launch_problem({**good, "FAKE_EXIT": "1", "RECORD_X": "y"}, cwd))
            cases = {
                "BASH_ENV is set": ({**good, "BASH_ENV": str(home)}, cwd),
                "ENV is set": ({**good, "ENV": str(home)}, cwd),
                "CDPATH is set": ({**good, "CDPATH": str(home)}, cwd),
                "PYTHONSTARTUP is not one": ({**good, "PYTHONSTARTUP": str(home)}, cwd),
                "LD_PRELOAD is not one": ({**good, "LD_PRELOAD": str(home)}, cwd),
                "HOME is not under": ({**good, "HOME": "/"}, cwd),
                "ZDOTDIR is not under": ({**good, "ZDOTDIR": str(outside_link)}, cwd),
                "XDG_CONFIG_HOME is not under": ({**good, "XDG_CONFIG_HOME": os.path.dirname(tmp) + "x"}, cwd),
                "TMPDIR is not under": ({key: value for key, value in good.items() if key != "TMPDIR"}, cwd),
                "the working directory": (good, "/"),
                "no environment": (None, cwd),
            }
            for expected, (env, directory) in cases.items():
                with self.subTest(expected=expected):
                    self.assertIn(expected, process_guard.launch_problem(env, directory) or "")

    def test_runners_refuse_what_they_do_not_run(self):
        calls = [
            lambda: process_guard.run_script("../SKILL.md"),
            lambda: process_guard.run_script(os.path.join("..", "scripts", "convert.py")),
            lambda: process_guard.run_script("convert"),
            lambda: process_guard.run_script("missing.py"),
            lambda: process_guard.run_script("convert.py", env={"BASH_ENV": "x"}),
            lambda: process_guard.run_script("convert.py", env={"PATH": "x"}),
            lambda: process_guard.run_script("convert.py", cwd="/"),
            lambda: process_guard.run_tool("sh", "-c", "true"),
            lambda: process_guard.run_tool("python3"),
        ]
        for index, call in enumerate(calls):
            with self.subTest(index=index), self.assertRaises(AssertionError):
                call()
        for programs, env in [(("sh",), None), ((), None), (("pandoc",), {"BASH_ENV": "x"})]:
            with self.subTest(programs=programs, env=env), self.assertRaises(AssertionError):
                with process_guard.library_processes(*programs, env=env):
                    pass

    def test_library_processes_lends_the_block_an_environment_of_its_own(self):
        before, cwd = dict(os.environ), os.getcwd()
        with process_guard.library_processes("pandoc", path_dirs=["first"], env={"FAKE_X": "1"}):
            self.assertLessEqual(set(os.environ), process_guard.ENVIRONMENT_KEYS | {"FAKE_X"})
            self.assertTrue(os.environ["PATH"].startswith("first" + os.pathsep))
            self.assertIsNone(process_guard.launch_problem(os.environ, os.getcwd()))
        self.assertEqual((dict(os.environ), os.getcwd()), (before, cwd))

    def test_code_under_test_starts_only_the_programs_named(self):
        with process_guard.refusal_expected():
            with self.assertRaises(process_guard.ProcessGuardError):
                merge_and_build.check_pandoc_available()
            with self.assertRaises(process_guard.ProcessGuardError), process_guard.library_processes("docling"):
                merge_and_build.check_pandoc_available()
            with tempfile.TemporaryDirectory() as tmp:
                pdf, bin_dir = Path(tmp, "book.pdf"), Path(tmp, "bin")
                pdf.write_bytes(b"%PDF-1.4\n")
                bin_dir.mkdir()
                Path(bin_dir, "docling").write_text(f'#!/bin/sh\ntouch "{tmp}/ran"\n', encoding="utf-8")
                Path(bin_dir, "docling").chmod(0o755)
                with self.assertRaises(process_guard.ProcessGuardError), contextlib.redirect_stdout(io.StringIO()), \
                        process_guard.library_processes("pandoc", path_dirs=[bin_dir]):
                    convert.run_parser("docling", str(pdf), tmp)
                self.assertFalse(Path(tmp, "ran").exists())


# What a test file may not name: ways of starting a process other than the
# runners of process_guard, and ways of reaching or changing the guard.
SUBPROCESS_CONSTANTS = frozenset({"subprocess.DEVNULL", "subprocess.PIPE", "subprocess.STDOUT",
                                  "subprocess.CalledProcessError", "subprocess.TimeoutExpired",
                                  "subprocess.CompletedProcess", "subprocess.SubprocessError"})
PROCESS_NAMES = re.compile(
    r"(?:subprocess|_posixsubprocess|pty|multiprocessing|ctypes|cffi|importlib|builtins|__builtins__|_imp"
    r"|_xxsubinterpreters|_interpreters|_testcapi|_testinternalcapi)(?:\.\w+)*"
    r"|os\.(?:system|popen|exec\w*|spawn\w*|posix_spawn\w*|fork\w*|startfile)"
    r"|asyncio\.(?:subprocess(?:\.\w+)*|create_subprocess_\w+)"
    r"|concurrent\.(?:interpreters|futures\.(?:ProcessPoolExecutor|InterpreterPoolExecutor|process|interpreter))"
    r"(?:\.\w+)*|test\.support\.interpreters(?:\.\w+)*"
    r"|sys\.(?:modules|meta_path|path_hooks|path_importer_cache|remote_exec)(?:\.\w+)*|exec|eval|compile|__import__"
    r"|\w+(?:\.\w+)*\.(?:subprocess|_posixsubprocess|pty|multiprocessing|ctypes|os)\.\w+(?:\.\w+)*"
    r"|(?:\w+\.)*(?:_fork_exec|__loader__|__spec__)(?:\.\w+)*"
)
REFLECTION = frozenset({"getattr", "setattr", "delattr", "vars"})
REACHES_PROCESSES = frozenset({"os", "subprocess", "_posixsubprocess", "pty", "asyncio", "multiprocessing",
                               "concurrent", "ctypes", "importlib", "sys", "builtins", "process_guard", "_imp",
                               "test", *process_guard.SUBINTERPRETER_EXTENSIONS})
# Public names of process_guard that one test file only may use; the probe
# module is for test_process_guard.py only as well.
ONLY_IN = {"run_skill_command": "test_packaging.py", "refusal_expected": "test_process_guard.py",
           "ProcessGuardError": "test_process_guard.py", "run_probe": "test_process_guard.py"}
PROBE_USER = "test_process_guard.py"


def _imported_names(tree):
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    names[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _dotted(node, names):
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, names)
        return base and f"{base}.{node.attr}"
    return None


def _docstrings(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _hides_modules(node, parent, names):
    """Whether node is sys.modules in mock.patch.dict(sys.modules, {...: None, ...}), which only hides modules."""
    return isinstance(parent, ast.Call) and _dotted(parent.func, names) == "unittest.mock.patch.dict" \
        and len(parent.args) == 2 and not parent.keywords and parent.args[0] is node and isinstance(parent.args[1], ast.Dict) \
        and all(isinstance(value, ast.Constant) and value.value is None for value in parent.args[1].values)


def process_problems(path):
    """(line, reason) for each place in a test file that may start a process
    some other way than through the runners of process_guard, or reach the guard.

    The audit hook of process_guard refuses such processes at run time; this
    check names them before a test runs, and also catches what the hook does not
    see: changes to the guard and ways around it (_posixsubprocess loaded
    afresh, ctypes, sys.modules). Only names are followed: a call built at run
    time from strings is outside what this finds, which is why the modules and
    builtins for that are refused outright.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = _imported_names(tree)
    problems = []
    first = tree.body[0] if tree.body else None
    if not (isinstance(first, ast.Import) and [(alias.name, alias.asname) for alias in first.names]
            == [("process_guard", None)]):
        problems.append((getattr(first, "lineno", 1), "the first statement is not `import process_guard`"))
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    docstrings = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "process_guard":
            for alias in node.names:
                only_in = ONLY_IN.get(alias.name)
                if alias.name == "*" or alias.name.startswith("_") or (only_in and path.name != only_in):
                    problems.append((node.lineno, f"imports process_guard.{alias.name}"))
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [alias.name for alias in node.names] + [getattr(node, "module", None) or ""]
            if isinstance(node, ast.ImportFrom) and node.module:
                modules += [f"{node.module}.{alias.name}" for alias in node.names]
            for module in modules:
                if module in process_guard.SUBINTERPRETER_MODULES \
                        or module.rpartition(".")[2] in process_guard.SUBINTERPRETER_EXTENSIONS:
                    problems.append((node.lineno, f"imports {module}, which runs code in a subinterpreter"))
                if module.split(".")[0] == "process_guard_probe" and path.name != PROBE_USER:
                    problems.append((node.lineno, "imports process_guard_probe"))
                if module.split(".")[0] == "test_packaging" and path.name != "test_packaging.py":
                    problems.append((node.lineno, "imports test_packaging"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            if re.fullmatch(r"(?:tests\.)?process_guard(?:\.\w+)+", node.value):
                problems.append((node.lineno, f"names {node.value}, as mock.patch does"))
            if "SKILL" in node.value and path.name not in ("test_packaging.py", "test_process_guard.py"):
                problems.append((node.lineno, "only test_packaging.py reads SKILL.md"))
        if not isinstance(node, (ast.Name, ast.Attribute)):
            continue
        name = _dotted(node, names)
        parent = parents.get(id(node))
        if isinstance(parent, ast.Attribute) and parent.value is node:
            continue
        if not name:
            continue
        if name == "process_guard" and isinstance(node, ast.Name):
            problems.append((node.lineno, "hands process_guard itself to other code"))
        elif name.startswith("process_guard."):
            attribute = name.split(".")[1]
            if attribute.startswith("_") or not isinstance(node.ctx, ast.Load):
                problems.append((node.lineno, f"reaches into or changes {name}"))
            only_in = ONLY_IN.get(attribute)
            if only_in and path.name != only_in:
                problems.append((node.lineno, f"{name} outside {only_in}"))
        elif not isinstance(node.ctx, ast.Load):
            continue
        elif name not in SUBPROCESS_CONSTANTS and PROCESS_NAMES.fullmatch(name) \
                and not (name == "sys.modules" and _hides_modules(node, parent, names)):
            problems.append((node.lineno, f"{name} outside the runners of process_guard"))
        elif name in REFLECTION and isinstance(parent, ast.Call) and parent.args and \
                (_dotted(parent.args[0], names) or "").split(".")[0] in REACHES_PROCESSES:
            problems.append((node.lineno, f"{name} on a module that starts processes or guards them"))
    return problems


class ProcessCheckTest(unittest.TestCase):
    def test_no_test_starts_a_process_another_way(self):
        found = {path.name: process_problems(path) for path in sorted(TESTS_DIR.glob("test_*.py"))}
        self.assertEqual({name: problems for name, problems in found.items() if problems}, {})

    def test_the_process_check_finds_other_ways(self):
        cases = [
            "import subprocess\nsubprocess.run(['sh', '-c', line])",
            "import subprocess\nsubprocess.run([sys.executable, *words], shell=True)",
            "import subprocess\nsubprocess.run([sys.executable, 'x.py'], check=True)",
            "import subprocess\nsubprocess.run(['pandoc', '--from', 'gfm'], input='x')",
            "import subprocess as sp\nsp.check_output(argv)",
            "from subprocess import run\nrun(['bash', '-c', line])",
            "import subprocess\nrunner = subprocess.run\nrunner(argv)",
            "import subprocess\ngetattr(subprocess, 'run')(argv)",
            "import subprocess\nsubprocess._fork_exec(*arguments)",
            "import os\nos.system(line)",
            "import os\ngetattr(os, 'system')(line)",
            "from os import execvp\nexecvp('sh', ['sh'])",
            "import pty\npty.spawn(['sh'])",
            "import asyncio\nasyncio.create_subprocess_shell(line)",
            "exec(code)",
            "import importlib\nimportlib.import_module('subprocess')",
            "sys.modules['subprocess'].run(argv)",
            "sys.modules.get('subprocess').run(argv)",
            "from unittest import mock\nmock.patch.dict(sys.modules, {'subprocess': fake})",
            "from unittest import mock\nmock.patch.dict(sys.modules, {'pypandoc': None}, clear=True)",
            "getattr(sys, 'modules')['subprocess']",
            "import builtins\nbuiltins.__import__('subprocess')",
            "__builtins__['__import__']('subprocess')",
            "import _posixsubprocess\n_posixsubprocess.fork_exec(*arguments)",
            "import convert\nconvert.subprocess.run(argv)",
            "import convert\nconvert.subprocess._fork_exec(*arguments)",
            "import convert\nconvert.os.system(line)",
            "import ctypes\nctypes.CDLL(None).system(line)",
            "from concurrent.futures import ProcessPoolExecutor\nProcessPoolExecutor()",
            "import multiprocessing\nmultiprocessing.Process(target=print).start()",
            "import _xxsubinterpreters as interpreters\ninterp = interpreters.create()\n"
            "interpreters.run_string(interp, code)",
            "import _interpreters",
            "import concurrent.interpreters",
            "from concurrent import interpreters",
            "import concurrent.futures\nconcurrent.futures.InterpreterPoolExecutor()",
            "from concurrent.futures import InterpreterPoolExecutor\nInterpreterPoolExecutor()",
            "from concurrent.futures.interpreter import InterpreterPoolExecutor",
            "from test.support import interpreters",
            "import _testcapi",
            "from _testinternalcapi import run_in_subinterp_with_config",
            "import _imp\n_imp.create_builtin(spec)",
            "sys.meta_path[0].find_spec(name)",
            "sys.__loader__.create_module(spec)",
            "module.__spec__.loader.exec_module(module)",
            "sys.remote_exec(pid, path)",
            "getattr(_testcapi, 'run_in_subinterp')(code)",
            "process_guard._state.pending = (argv, env, cwd)",
            "process_guard.launch_problem = lambda env, cwd: None",
            "from unittest import mock\nmock.patch.object(process_guard, 'launch_problem')",
            "from unittest import mock\nmock.patch('process_guard.launch_problem')",
            "from process_guard import _refuse",
            "vars(process_guard)['PATH'] = ''",
            "setattr(process_guard, 'PATH', '')",
            "with process_guard.refusal_expected():\n    pass",
            "process_guard.run_probe('-', 'x', 'os.system')",
            "import process_guard_probe",
            "from process_guard import run_skill_command",
            "process_guard.run_skill_command(command, shell='sh', cwd=cwd, values={})",
            "text = open('SKILL.md').read()",
            "import test_packaging",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test_other.py"
            for case in cases:
                with self.subTest(case=case):
                    path.write_text("import process_guard\nimport sys\n" + case + "\n", encoding="utf-8")
                    self.assertNotEqual(process_problems(path), [])
            for prologue in ("", "'''A docstring first.'''\nimport process_guard\n", "import sys\nimport process_guard\n",
                             "import process_guard as guard\n", "from process_guard import run_script\n",
                             "import process_guard, sys\n"):
                with self.subTest(prologue=prologue):
                    path.write_text(prologue + "x = 1\n", encoding="utf-8")
                    self.assertIn("the first statement is not `import process_guard`",
                                  [reason for _, reason in process_problems(path)])
            path.write_text("import process_guard\nimport subprocess\nimport sys\nfrom unittest import mock\n\n\n"
                            "def check(test):\n    '''Mentions SKILL.md in a docstring only.'''\n"
                            "    result = process_guard.run_script('convert.py', '--help')\n"
                            "    with process_guard.library_processes('pandoc'), "
                            "mock.patch.dict(sys.modules, {'pypandoc': None}):\n        pass\n"
                            "    test.assertIsInstance(result, subprocess.CompletedProcess)\n", encoding="utf-8")
            self.assertEqual(process_problems(path), [])


if __name__ == "__main__":
    unittest.main()
