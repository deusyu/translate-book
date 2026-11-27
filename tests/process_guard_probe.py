"""Try ways of starting a process, for test_process_guard; run through process_guard.run_probe.

    process_guard_probe.py [--first STEP] MODULE MARKER_DIR ATTEMPT...

Takes the STEP of FIRST_STEPS, if given, then imports the test module MODULE,
as test_<name> or, with the repository root on sys.path instead of tests/, as
tests.test_<name> ("-" imports none, so nothing guards the attempts), then
makes each ATTEMPT:
one of FORMS starts a process that creates MARKER_DIR/ATTEMPT; one of
TEST_RUNS runs a test whose code starts that process and swallows the
exception; one of REPLACED_LAUNCHES sets up a runner's launch the way the
runners do and then starts that process with another argv, environment or
working directory, or lets the guard approve a launch and then starts the
process some other way, or starts a program library_processes lends with an
environment or working directory of its own. Prints one JSON line per attempt with the exception it
raised, if any, and for TEST_RUNS how the test ended. The attempt
"environment" starts nothing: it prints the environment and working directory
this probe got.

asyncio and concurrent.futures are imported only where an attempt uses them:
on Python 3.14 they load _interpreters, and process_guard refuses to start
once that is loaded.
"""
import _imp
import builtins
import importlib
import importlib.machinery
import importlib.util
import json
import multiprocessing
import os
import pty
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOUCH = shutil.which("touch") or "/usr/bin/touch"


def write_marker(path):
    Path(path).write_text("started", encoding="utf-8")


def touch_line(marker):
    return f"{shlex.quote(TOUCH)} {shlex.quote(str(marker))}"


def fork(marker):
    pid = os.fork()
    if pid == 0:
        write_marker(marker)
        os._exit(0)
    os.waitpid(pid, 0)


def forkpty(marker, fork_function):
    pid, fd = fork_function()
    if pid == 0:
        write_marker(marker)
        os._exit(0)
    os.waitpid(pid, 0)
    os.close(fd)


def multiprocessing_start(marker, method):
    process = multiprocessing.get_context(method).Process(target=write_marker, args=(str(marker),))
    process.start()
    process.join()


def process_pool(marker):
    import concurrent.futures
    context = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(1, mp_context=context) as pool:
        pool.submit(write_marker, str(marker)).result()


def asyncio_exec(marker):
    import asyncio

    async def run():
        process = await asyncio.create_subprocess_exec(TOUCH, str(marker))
        await process.wait()
    asyncio.run(run())


def interpreters_module():
    """The name of the module this Python makes subinterpreters with."""
    return "_interpreters" if sys.version_info >= (3, 13) else "_xxsubinterpreters"


def in_subinterpreter(module, marker):
    """Have a new subinterpreter of module start a process that creates marker."""
    interpreter = module.create()
    try:
        run = module.exec if hasattr(module, "exec") else module.run_string
        run(interpreter, f"import subprocess; subprocess.run([{TOUCH!r}, {str(marker)!r}])")
    finally:
        module.destroy(interpreter)


def from_finders(name):
    """Load name the way the import system does, through the finders of sys.meta_path."""
    for finder in sys.meta_path:
        spec = finder.find_spec(name, None)
        if spec is not None:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise ModuleNotFoundError(name)


def under_another_package(name):
    """Load the extension module name as elsewhere.<name>, built in or from its file."""
    spec = importlib.machinery.PathFinder.find_spec(name)
    if spec is None:
        return _imp.create_builtin(importlib.machinery.ModuleSpec(f"elsewhere.{name}", None))
    loader = importlib.machinery.ExtensionFileLoader(f"elsewhere.{name}", spec.origin)
    return importlib.util.module_from_spec(importlib.util.spec_from_loader(f"elsewhere.{name}", loader))


def import_statement(name):
    """Import name as `import name` does: the audit event comes before the module is looked for."""
    __import__(name)
    return sys.modules[name]


def concurrent_interpreter(marker):
    interpreter = import_statement("concurrent.interpreters").create()
    try:
        interpreter.exec(f"import subprocess; subprocess.run([{TOUCH!r}, {str(marker)!r}])")
    finally:
        interpreter.close()


def interpreter_pool(marker):
    with import_statement("concurrent.futures.interpreter").InterpreterPoolExecutor(1) as pool:
        pool.submit(subprocess.run, [TOUCH, str(marker)]).result()


def ctypes_system(marker):
    import ctypes
    ctypes.CDLL(None).system(touch_line(marker).encode())


def fork_exec(marker, function):
    """Call _posixsubprocess.fork_exec, or subprocess's reference to it, as this Python's subprocess does.

    Python 3.14 dropped the last argument, allow_vfork."""
    read, write = os.pipe()
    allow_vfork = (subprocess._USE_VFORK,) if hasattr(subprocess, "_USE_VFORK") else ()
    pid = function(
        [TOUCH, str(marker)], [os.fsencode(TOUCH)], True, (), None, None,
        -1, -1, -1, -1, -1, -1, read, write, False, False, -1, None, None, None, -1, None,
        *allow_vfork)
    os.waitpid(pid, 0)


def swallowing_test(marker, caught):
    """Run a test whose code under test catches `caught` around starting a process."""
    class Swallowing(unittest.TestCase):
        def test_code_that_swallows(self):
            try:
                subprocess.run([TOUCH, marker])
            except caught:
                pass

    result = unittest.TestResult()
    Swallowing("test_code_that_swallows").run(result)
    return {"errors": len(result.errors), "failures": len(result.failures), "successful": result.wasSuccessful()}


FORMS = {
    "subprocess.run": lambda m: subprocess.run([TOUCH, m]),
    "subprocess.Popen": lambda m: subprocess.Popen([TOUCH, m]).wait(),
    "subprocess.call": lambda m: subprocess.call([TOUCH, m]),
    "subprocess.check_output": lambda m: subprocess.check_output([TOUCH, m]),
    "subprocess.getoutput": lambda m: subprocess.getoutput(touch_line(m)),
    "shell=True": lambda m: subprocess.run(touch_line(m), shell=True),
    "sys.modules": lambda m: sys.modules["subprocess"].run([TOUCH, m]),
    "builtins.__import__": lambda m: builtins.__import__("subprocess").run([TOUCH, m]),
    "importlib.import_module": lambda m: importlib.import_module("subprocess").run([TOUCH, m]),
    "getattr": lambda m: getattr(subprocess, "run")([TOUCH, m]),
    "sys.executable -c os.system": lambda m: subprocess.run(
        [sys.executable, "-c", f"import os; os.system({touch_line(m)!r})"]),
    "os.system": lambda m: os.system(touch_line(m)),
    "os.popen": lambda m: os.popen(touch_line(m)).read(),
    "os.spawnv": lambda m: os.spawnv(os.P_WAIT, TOUCH, [TOUCH, m]),
    "os.posix_spawn": lambda m: os.waitpid(os.posix_spawn(TOUCH, [TOUCH, m], dict(os.environ)), 0),
    "os.posix_spawnp": lambda m: os.waitpid(os.posix_spawnp("touch", ["touch", m], dict(os.environ)), 0),
    "os.fork": fork,
    "os.forkpty": lambda m: forkpty(m, os.forkpty),
    "pty.fork": lambda m: forkpty(m, pty.fork),
    "multiprocessing fork": lambda m: multiprocessing_start(m, "fork"),
    "multiprocessing spawn": lambda m: multiprocessing_start(m, "spawn"),
    "multiprocessing forkserver": lambda m: multiprocessing_start(m, "forkserver"),
    "ProcessPoolExecutor": process_pool,
    "asyncio": asyncio_exec,
    "ctypes": ctypes_system,
    "_posixsubprocess.fork_exec": lambda m: fork_exec(m, importlib.import_module("_posixsubprocess").fork_exec),
    "subprocess._fork_exec": lambda m: fork_exec(m, subprocess._fork_exec),
    "subinterpreter": lambda m: in_subinterpreter(import_statement(interpreters_module()), m),
    "subinterpreter of a name built at run time": lambda m: in_subinterpreter(
        importlib.import_module("_" + interpreters_module()[1:]), m),
    "subinterpreter through the finders": lambda m: in_subinterpreter(from_finders(interpreters_module()), m),
    "subinterpreter under another package": lambda m: in_subinterpreter(
        under_another_package(interpreters_module()), m),
    "_testcapi.run_in_subinterp": lambda m: import_statement("_testcapi").run_in_subinterp(
        f"import subprocess; subprocess.run([{TOUCH!r}, {m!r}])"),
    "concurrent.interpreters": concurrent_interpreter,
    "InterpreterPoolExecutor": interpreter_pool,
    "os.execv": lambda m: os.execv(TOUCH, [TOUCH, m]),
}

FIRST_STEPS = {
    "native Windows": lambda: setattr(os, "name", "nt"),
    "a subinterpreter module loaded": lambda: __import__(interpreters_module()),
}


def replaced_launch(marker, part):
    guard = sys.modules["process_guard"]
    with guard._own_place("replaced") as place:
        argv, env, cwd = [TOUCH, marker], guard._environment(place, guard.PATH, None), str(place / "cwd")
        guard._state.pending = (argv, env, cwd)
        try:
            if part == "argv":
                argv = [TOUCH, marker, marker]
            elif part == "environment":
                env = dict(env)
            else:
                cwd = str(place / "tmp")
            subprocess.run(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL)
        finally:
            guard._state.pending = None


def approved_then(marker, how):
    guard = sys.modules["process_guard"]
    argv = [TOUCH, marker]
    guard._state.approved = (TOUCH, argv)
    try:
        if how == "os.posix_spawn":
            os.waitpid(os.posix_spawn(TOUCH, argv, dict(os.environ)), 0)
        else:
            fork_exec(marker, importlib.import_module("_posixsubprocess").fork_exec)
    finally:
        guard._state.approved = None


def lent_program(marker, change):
    guard = sys.modules["process_guard"]
    bin_dir = tempfile.mkdtemp(prefix="bin-")
    docling = os.path.join(bin_dir, "docling")
    with open(docling, "w", encoding="utf-8") as f:
        f.write(f"#!/bin/sh\ntouch {shlex.quote(marker)}\n")
    os.chmod(docling, 0o755)
    with guard.library_processes("docling", path_dirs=[bin_dir]):
        if change == "BASH_ENV":
            subprocess.run(["docling"], env={**os.environ, "BASH_ENV": docling}, stdin=subprocess.DEVNULL)
        else:
            subprocess.run(["docling"], cwd="/", stdin=subprocess.DEVNULL)


REPLACED_LAUNCHES = {
    **{f"a runner's launch with another {part}": lambda marker, part=part: replaced_launch(marker, part)
       for part in ("argv", "environment", "working directory")},
    **{f"an approved launch taken by {how}": lambda marker, how=how: approved_then(marker, how)
       for how in ("os.posix_spawn", "fork_exec of other arguments")},
    "a lent program with BASH_ENV set": lambda marker: lent_program(marker, "BASH_ENV"),
    "a lent program outside the test root": lambda marker: lent_program(marker, "cwd"),
}

TEST_RUNS = {
    "except Exception in a test": lambda m: swallowing_test(m, Exception),
    "except BaseException in a test": lambda m: swallowing_test(m, BaseException),
}


def main():
    args = sys.argv[1:]
    if args[0] == "--first":
        FIRST_STEPS[args[1]]()
        args = args[2:]
    module, marker_dir, *attempts = args
    if module.startswith("tests."):
        # The sys.path of `python3 -m unittest tests.test_<name>` run from the repository root.
        here = Path(__file__).resolve().parent
        sys.path[:] = [str(here.parent), *(entry for entry in sys.path if Path(entry or ".").resolve() != here)]
    if module != "-":
        importlib.import_module(module)
    for attempt in attempts:
        if attempt == "environment":
            print(json.dumps({"attempt": attempt, "environment": dict(os.environ), "cwd": os.getcwd()}),
                  flush=True)
            continue
        marker = os.path.join(marker_dir, attempt.replace(" ", "_"))
        outcome = None
        try:
            outcome = {**FORMS, **TEST_RUNS, **REPLACED_LAUNCHES}[attempt](marker)
            error = None
        except BaseException as e:
            error = f"{type(e).__name__}: {e}"
        print(json.dumps({"attempt": attempt, "error": error,
                          "test": outcome if attempt in TEST_RUNS else None}), flush=True)


if __name__ == "__main__":
    main()
