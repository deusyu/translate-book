"""Every process a test starts goes through a runner of this module.

Each test module imports this module as its first statement, so a test file run
on its own is guarded as well. On import the module adds an audit hook
(sys.addaudithook; such a hook cannot be removed again) that stops a process
before it starts, unless one of the runners below starts it:

- subprocess.Popen, however the test reached subprocess: subprocess.run,
  os.popen, asyncio, the module taken from sys.modules or __import__, ...
- os.system, os.exec*, os.spawn*, os.posix_spawn, os.posix_spawnp, os.fork
  and os.forkpty, so pty and multiprocessing's fork as well; os.posix_spawn
  only goes through as the second step of a subprocess.Popen the hook let
  through;
- ctypes loading a library or looking up a function in one;
- importing a module that runs code in a subinterpreter, however the name
  was built: _xxsubinterpreters, _interpreters, _testcapi and
  _testinternalcapi under any package, concurrent.interpreters,
  concurrent.futures.interpreter and test.support.interpreters. A
  subinterpreter has no audit hook of this module, so code there could start
  processes, with the real HOME, that the hook never sees.

_posixsubprocess.fork_exec, which forks and executes for subprocess, raises no
audit event: multiprocessing's spawn and forkserver start methods call it
directly, and subprocess keeps a reference of its own, subprocess._fork_exec.
This module replaces both with a function that calls the original only for the
argument list of the subprocess.Popen the hook has just let through in the
same thread, and refuses otherwise. Making a built-in module raises no audit
event either, and some Python builds have _interpreters built in, so this
module replaces _imp.create_builtin as well with a function that refuses the
modules above. Importing this module fails if one of them is loaded already;
From Python 3.14 on, concurrent.futures imports _interpreters when it can, so
this module imports concurrent.futures first, while it cannot.

A refused attempt raises ProcessGuardError, a BaseException, so `except
Exception` in the code under test does not swallow it and the test errors. It
is also recorded: if a test swallows it anyway, the test run exits with status
70 after unittest has reported.

The runners:

- run_skill_command: a command of SKILL.md, once SKILL.md passes the shape
  check of test_packaging;
- run_script: a script of scripts/, with this Python;
- run_tool: pandoc;
- run_probe: tests/process_guard_probe.py, for the tests of this module;
- library_processes: a with block in which code that a test calls in-process,
  a function of scripts/ or a library, may start the programs it names.

Each gives the process an environment built from nothing: HOME and ZDOTDIR a
new empty directory and TMPDIR another, with XDG_*_HOME inside HOME, all under
the test root, a directory made on import that is tempfile's directory from
then on; PATH; settings for Python; from the test, test-double variables
(FAKE_*, RECORD_*) only. BASH_ENV, ENV and CDPATH are never set. The working
directory is under the test root. The hook checks the environment and the
working directory of every process it lets through.

Tests that run pandoc or zsh use needs() or installed(): without the program
they skip, unless TRANSLATE_BOOK_REQUIRED_TOOLS names it, as CI does; then
they fail. The python-markdown test reads the same list for the package.

translate-book runs on macOS and Linux only: on native Windows importing
this module says so, points to WSL and exits, as the scripts do.

Not covered: code that turns the guard off on purpose, by changing this
module or its state, loading _posixsubprocess afresh, reaching the import
system's loaders past _imp.create_builtin, handing the guard and the import
system objects that answer each differently (a module spec whose name changes
between reads), or calling C through a function pointer taken before this
module was imported or through a C extension other than ctypes; and
sys.remote_exec of Python 3.14, which makes another Python process, one
already running, run code. The static check in test_process_guard refuses
the names for those in the test files.
"""
import atexit
import contextlib
import functools
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

if os.name == "nt":
    sys.stderr.write("translate-book runs on macOS and Linux only. "
                     "On Windows, run it in WSL (Windows Subsystem for Linux).\n")
    sys.stderr.flush()
    os._exit(1)

import _imp  # noqa: E402
import _posixsubprocess  # noqa: E402  POSIX only, hence after the check above

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
PROBE = TESTS_DIR / "process_guard_probe.py"

TEST_ROOT = os.path.realpath(tempfile.mkdtemp(prefix="translate-book-tests-"))
tempfile.tempdir = TEST_ROOT
PATH = os.environ.get("PATH", os.defpath)

REFUSED_EVENTS = frozenset({
    "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.forkpty", "os.startfile",
    "ctypes.dlopen", "ctypes.dlsym", "ctypes.dlsym/handle", "ctypes.call_function",
})
# An extension module is found by the last part of its name, under any
# package; the modules around them by their full name.
SUBINTERPRETER_EXTENSIONS = frozenset({"_xxsubinterpreters", "_interpreters", "_testcapi", "_testinternalcapi"})
SUBINTERPRETER_MODULES = frozenset({"concurrent.interpreters", "concurrent.futures.interpreter",
                                    "test.support.interpreters"})
LIBRARY_PROGRAMS = frozenset({"pandoc", "mineru-kit", "marker_single", "docling"})
REQUIRED_TOOLS = frozenset(os.environ.get("TRANSLATE_BOOK_REQUIRED_TOOLS", "").split())
UNDER_HOME = {"XDG_CONFIG_HOME": ".config", "XDG_CACHE_HOME": ".cache",
              "XDG_DATA_HOME": ".local/share", "XDG_STATE_HOME": ".local/state"}
UNDER_ROOT = ("HOME", "ZDOTDIR", "TMPDIR", *UNDER_HOME)
PYTHON_SETTINGS = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                   "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
ENVIRONMENT_KEYS = frozenset({"PATH", *UNDER_ROOT, *PYTHON_SETTINGS})
TEST_DOUBLE_KEY = re.compile(r"(?:FAKE|RECORD)_[A-Z0-9_]+")
NEVER_SET = ("BASH_ENV", "ENV", "CDPATH")
SKILL_PLACEHOLDERS = ("{baseDir}", "<temp_dir>", "<NNNN>")
_POSIX_SPAWN_STEP = getattr(subprocess.Popen, "_posix_spawn", None)
_FORK_EXEC = _posixsubprocess.fork_exec
_CREATE_BUILTIN = _imp.create_builtin


class ProcessGuardError(BaseException):
    """A test tried to start a process some other way than through a runner of process_guard."""


_state = threading.local()
_refusals = []


def _refuse(what):
    if not getattr(_state, "expected", False):
        _refusals.append(what)
    raise ProcessGuardError(f"{what}: tests start processes only through the runners of process_guard")


def _runs_subinterpreters(name):
    return type(name) is not str or name in SUBINTERPRETER_MODULES \
        or name.rpartition(".")[2] in SUBINTERPRETER_EXTENSIONS


def _refuse_import(name):
    shown = repr(name) if type(name) is str else f"a {type(name).__name__} name"
    _refuse(f"import of {shown}, a module that runs code in a subinterpreter, where no audit hook watches")


def _under_root(path):
    try:
        real = os.path.realpath(os.fsdecode(path))
    except (TypeError, ValueError):
        return False
    return real == TEST_ROOT or real.startswith(TEST_ROOT + os.sep)


def launch_problem(env, cwd):
    """Why a process with this environment and working directory may not start, or None."""
    if env is None:
        return "no environment is given"
    env = {os.fsdecode(key): value for key, value in env.items()}
    for key in env:
        if key in NEVER_SET:
            return f"{key} is set"
        if key not in ENVIRONMENT_KEYS and not TEST_DOUBLE_KEY.fullmatch(key):
            return f"{key} is not one of the variables the runners set"
    for key in UNDER_ROOT:
        if key not in env or not _under_root(env[key]):
            return f"{key} is not under the test root"
    if cwd is None or not _under_root(cwd):
        return f"the working directory {cwd!r} is not under the test root"
    return None


def _audit(event, args):
    if event == "subprocess.Popen":
        executable, argv, cwd, env = args
        _state.approved = None
        pending, _state.pending = getattr(_state, "pending", None), None
        if pending is not None:
            if argv != pending[0] or env is not pending[1] or cwd != pending[2]:
                _refuse(f"subprocess.Popen of {argv!r}, which is not the process the runner was to start")
            problem = launch_problem(env, cwd)
        else:
            programs = getattr(_state, "programs", None)
            name = os.path.basename(os.fsdecode(executable))
            name = name[:-len(".exe")] if name.endswith(".exe") else name
            if programs is None or name not in programs:
                _refuse(f"subprocess.Popen of {argv!r}")
            problem = launch_problem(os.environ if env is None else env,
                                     os.getcwd() if cwd is None else cwd)
        if problem:
            _refuse(f"subprocess.Popen of {argv!r}: {problem}")
        _state.approved = (executable, argv)
    elif event == "os.posix_spawn":
        approved, _state.approved = getattr(_state, "approved", None), None
        caller = sys._getframe(1).f_code
        if approved is None or _POSIX_SPAWN_STEP is None or caller is not _POSIX_SPAWN_STEP.__code__ \
                or args[0] != approved[0] or args[1] != approved[1]:
            _refuse(f"os.posix_spawn of {args[1]!r}")
    elif event == "import":
        if _runs_subinterpreters(args[0]):
            _refuse_import(args[0])
    elif event in REFUSED_EVENTS:
        _refuse(event)


def _guarded_fork_exec(args, *rest, **kwargs):
    approved, _state.approved = getattr(_state, "approved", None), None
    if approved is None or args is not approved[1]:
        _refuse(f"_posixsubprocess.fork_exec of {args!r}")
    return _FORK_EXEC(args, *rest, **kwargs)


def _guarded_create_builtin(spec):
    name = getattr(spec, "name", None)
    if _runs_subinterpreters(name):
        _refuse_import(name)
    return _CREATE_BUILTIN(spec)


def _report_and_clean_up():
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    if _refusals:
        sys.stdout.flush()
        print(f"process_guard: {len(_refusals)} refused attempt(s) to start a process, the first: "
              f"{_refusals[0]}", file=sys.stderr, flush=True)
        os._exit(70)


_loaded = sorted(name for name, module in list(sys.modules.items())
                 if module is not None and isinstance(name, str) and _runs_subinterpreters(name))
if _loaded:
    raise ProcessGuardError(f"{', '.join(_loaded)} was imported before process_guard, so code could run in a "
                            "subinterpreter, where no audit hook watches; import process_guard first")
_hidden = [name for name in SUBINTERPRETER_EXTENSIONS if name not in sys.modules]
sys.modules.update(dict.fromkeys(_hidden, None))
try:
    import concurrent.futures  # noqa: E402,F401
finally:
    for _name in _hidden:
        if sys.modules.get(_name, False) is None:
            del sys.modules[_name]

sys.addaudithook(_audit)
_posixsubprocess.fork_exec = _guarded_fork_exec
subprocess._fork_exec = _guarded_fork_exec
_imp.create_builtin = _guarded_create_builtin
atexit.register(_report_and_clean_up)


@contextlib.contextmanager
def refusal_expected():
    """For the tests of this module: refusals in the block are not counted as swallowed."""
    _state.expected = True
    try:
        yield
    finally:
        _state.expected = False


@contextlib.contextmanager
def _own_place(kind):
    """A new directory under the test root with an empty home, tmp and cwd in it."""
    place = Path(tempfile.mkdtemp(prefix=f"{kind}-", dir=TEST_ROOT))
    for name in ("home", "tmp", "cwd", "bin"):
        (place / name).mkdir()
    try:
        yield place
    finally:
        shutil.rmtree(place, ignore_errors=True)


def _environment(place, path, extra):
    if any(not TEST_DOUBLE_KEY.fullmatch(name) for name in extra or {}):
        raise AssertionError(f"a test may add FAKE_ and RECORD_ variables only, not {sorted(extra)}")
    home = place / "home"
    return {"PATH": path, "HOME": str(home), "ZDOTDIR": str(home), "TMPDIR": str(place / "tmp"),
            **{key: str(home / below) for key, below in UNDER_HOME.items()},
            **PYTHON_SETTINGS, **(extra or {})}


def _launch(argv, env, cwd, *, input=None, timeout=None):
    """Start argv as the runners do; the audit hook lets exactly this process through."""
    cwd = os.fspath(cwd)
    problem = launch_problem(env, cwd)
    if problem:
        raise AssertionError(f"not run: {problem}")
    _state.pending = (argv, env, cwd)
    try:
        return subprocess.run(argv, cwd=cwd, env=env, input=input,
                              stdin=subprocess.DEVNULL if input is None else None,
                              capture_output=True, text=True, timeout=timeout)
    finally:
        _state.pending = None
        _state.approved = None


def _path(path_dirs, rest):
    return os.pathsep.join([*map(str, path_dirs), rest])


def run_script(script, *args, cwd=None, path_dirs=(), env=None, input=None, timeout=300):
    """Run scripts/<script> with this Python and return the CompletedProcess.

    cwd defaults to a new empty directory; path_dirs go before the PATH the
    test process started with; env may add FAKE_ and RECORD_ variables.
    """
    path = SCRIPTS_DIR / script
    if os.sep in script or path.suffix != ".py" or not path.is_file():
        raise AssertionError(f"{script!r} is not a script of scripts/")
    with _own_place("script") as place:
        environment = _environment(place, _path(path_dirs, PATH), env)
        return _launch([sys.executable, str(path), *map(str, args)], environment,
                       place / "cwd" if cwd is None else cwd, input=input, timeout=timeout)


def run_tool(program, *args, cwd=None, input=None, timeout=300):
    """Run pandoc, found on the PATH the test process started with, and return the CompletedProcess."""
    if program != "pandoc":
        raise AssertionError(f"run_tool runs pandoc only, not {program!r}")
    executable = shutil.which(program, path=PATH)
    if not executable:
        raise AssertionError(f"{program} is not installed")
    with _own_place("tool") as place:
        return _launch([executable, *map(str, args)], _environment(place, PATH, None),
                       place / "cwd" if cwd is None else cwd, input=input, timeout=timeout)


def installed(program):
    """Whether program is on the PATH the test process started with.

    When TRANSLATE_BOOK_REQUIRED_TOOLS (a space-separated list; CI sets
    "pandoc zsh", and python-markdown in the job that installs it) names a
    program that is not, raises AssertionError instead,
    so the test that asked fails rather than skips.
    """
    if shutil.which(program, path=PATH):
        return True
    if program in REQUIRED_TOOLS:
        raise AssertionError(f"{program} is not installed, and TRANSLATE_BOOK_REQUIRED_TOOLS requires it")
    return False


def needs(program):
    """Decorator for a test, or a TestCase class, that runs program: skips it
    when program is not installed, or fails it as installed() does."""
    try:
        if installed(program):
            return lambda test: test
    except AssertionError as error:
        reason = str(error)

        def fail(self, *args, **kwargs):
            self.fail(reason)

        def failing(test):
            if isinstance(test, type):
                test.setUp = fail
                return test
            return functools.wraps(test)(fail)
        return failing
    return unittest.skip(f"{program} is not installed")


def run_probe(*args, timeout=120):
    """Run tests/process_guard_probe.py with this Python, for the tests of this module."""
    with _own_place("probe") as place:
        return _launch([sys.executable, str(PROBE), *map(str, args)], _environment(place, PATH, None),
                       place / "cwd", timeout=timeout)


@contextlib.contextmanager
def library_processes(*programs, path_dirs=(), env=None):
    """Let the code a test calls in the block start the programs named.

    The programs are among LIBRARY_PROGRAMS: what scripts/ and its libraries
    start (pypandoc probes for pandoc.exe as well, which counts as pandoc).
    For the block, os.environ is an environment built like the runners' (with
    path_dirs before PATH and env's FAKE_ and RECORD_ variables) and the
    working directory a new empty one; both are restored afterwards.
    """
    if not programs or set(programs) - LIBRARY_PROGRAMS:
        raise AssertionError(f"programs code under test may start: {sorted(LIBRARY_PROGRAMS)}, "
                             f"not {sorted(set(programs) - LIBRARY_PROGRAMS) or 'none'}")
    with _own_place("library") as place:
        environment = _environment(place, _path(path_dirs, PATH), env)
        saved_environment, saved_cwd = dict(os.environ), os.getcwd()
        os.environ.clear()
        os.environ.update(environment)
        os.chdir(place / "cwd")
        _state.programs = frozenset(programs)
        try:
            yield
        finally:
            _state.programs = None
            _state.approved = None
            os.chdir(saved_cwd)
            os.environ.clear()
            os.environ.update(saved_environment)


def _test_packaging():
    """test_packaging as loaded: the module that tests patch, also when it runs as __main__."""
    for name in ("test_packaging", "tests.test_packaging", "__main__"):
        module = sys.modules.get(name)
        if module is not None and getattr(module, "__file__", None) \
                and Path(module.__file__).resolve() == TESTS_DIR / "test_packaging.py":
            return module
    import test_packaging
    return test_packaging


def run_skill_command(command, *, shell, cwd, values, path_dirs=(), env=None, timeout=120):
    """Run one SKILL.md command the way an agent runs it, and return the CompletedProcess.

    The one place where tests start a process from SKILL.md; test_packaging's
    SkillCommandRunGuardTest checks that every test that runs SKILL.md
    commands comes here. It fails the test, running nothing, unless SKILL.md
    as it is now passes skill_command_problems, the command is one of its
    commands, and each placeholder in it has a value that double quotes keep
    literal. The command runs with cwd under the test root and an environment
    built from nothing, as the other runners' are, except PATH: path_dirs,
    then a python3 that runs this Python, then the system directories. HOME
    must stay empty; env may add RECORD_ variables only. shell is sh, bash or
    zsh; with None the command's words run directly, without its < redirect,
    with nothing on standard input.
    """
    packaging = _test_packaging()
    if command not in packaging._checked_commands(packaging.skill_text()):
        raise AssertionError(f"not a command of SKILL.md, so it is not run: {command!r}")
    if set(values) - set(SKILL_PLACEHOLDERS):
        raise AssertionError(f"unknown placeholders {sorted(set(values) - set(SKILL_PLACEHOLDERS))}")
    line = command
    for placeholder in SKILL_PLACEHOLDERS:
        if placeholder in line:
            value = values.get(placeholder)
            if value is None or re.search(r'["$`\\\n\0]', value):
                raise AssertionError(f"{placeholder} needs a value that double quotes keep literal, not {value!r}")
            line = line.replace(placeholder, value)
    if any(not re.fullmatch(r"RECORD_[A-Z_]+", name) for name in env or {}):
        raise AssertionError(f"env may add RECORD_ variables only, not {sorted(env)}")
    if not _under_root(cwd):
        raise AssertionError(f"a SKILL.md command runs under the test root, not in {cwd}")
    if shell is None:
        words = shlex.split(line)
        argv = words[:words.index("<")] if "<" in words else words
    else:
        executable = shutil.which(shell, path=PATH)
        if not executable:
            raise AssertionError(f"{shell} is not installed")
        argv = [executable, "-c", line]
    with _own_place("skill-command") as place:
        home, bin_dir = place / "home", place / "bin"
        (bin_dir / "python3").write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n',
                                         encoding="utf-8")
        (bin_dir / "python3").chmod(0o755)
        system_dirs = [directory for directory in os.defpath.split(os.pathsep) if directory]
        environment = _environment(place, os.pathsep.join([*map(str, path_dirs), str(bin_dir), *system_dirs]),
                                   env)
        result = _launch(argv, environment, cwd, timeout=timeout)
        written = sorted(path.name for path in home.iterdir())
        if written:
            raise AssertionError(f"{command!r} wrote into HOME: {written}")
    return result
