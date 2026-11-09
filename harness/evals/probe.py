"""Run one changed module against its executable spec, with side effects blocked.

Started by ``spec_monitor`` in a fresh process, in an empty directory that holds a
copy of the module and nothing else, so no conftest.py, test file or fixture can
run. An audit hook installed before the module is imported refuses every file
write, directory change, network call and subprocess, and records each attempt.
Audit hooks cannot be removed once added.

Usage: python -I -m harness.evals.probe <repo> <module.py> [spec.py], with a nonce on stdin.
With spec.py, its ``check(fn)`` replaces the built-in spec for that repository. Such a spec
is untrusted code too: it loads after the hook, and a side effect traced to the spec's own
file, rather than the module's, voids the verdict instead of counting against the module.
Prints one line: the nonce followed by a JSON result. The nonce is read before the
module is imported, so the module cannot see it in argv and forge the result line.

The check runs more than once. A function can behave exactly as documented until an
environment variable is set, and the probe's own environment holds only three
variables, so such a branch would never run here. The module is therefore imported
fresh and checked under each setting in ``ENVIRONMENTS`` as well, and a violation
found under any of them counts.

The probe also reads the module before running it. ``harvest`` collects the integer
and string literals in its source, folding constant arithmetic such as ``10**12``.
The integers become extra inputs: the spec probes both sides of each one, so a
threshold the code names is checked wherever it sits. Every environment variable the
module reads while it runs is recorded, and after the common settings the module is
checked again with each recorded variable set to each harvested string and a few
common flag values. The rule each spec checks still comes from the docstring alone;
only the choice of inputs reads the code. A constant the module computes at run
time, rather than writes down, is still never probed, and docs/blind-spots.md keeps
the cases that do that.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import operator
import os
import sys
import threading
from collections.abc import Mapping

from harness.evals.specs import NO_CONSTANTS, SPECS, Constants

WRITE_MODES = frozenset("wax+")
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
FILE_EVENTS = {
    "os.mkdir": "creates a directory",
    "os.remove": "deletes a file",
    "os.rename": "renames a file",
    "os.rmdir": "deletes a directory",
    "os.truncate": "truncates a file",
    "os.chmod": "changes permissions",
    "shutil.rmtree": "deletes a tree",
}
NETWORK_EVENTS = (
    "socket.connect",
    "socket.getaddrinfo",
    "socket.bind",
    "urllib.Request",
    "http.client.connect",
)
PROCESS_EVENTS = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork")


class Blocked(PermissionError):
    pass


# The environment the check runs under, one entry at a time. The first entry is the
# environment as given; each later one adds a single setting. A documented rule says
# nothing about the environment, so the function's behavior must not change with it.
ENVIRONMENTS: tuple[tuple[str, dict[str, str]], ...] = (
    ("the environment as given", {}),
    ("APP_ENV=production", {"APP_ENV": "production"}),
    ("APP_ENV=prod", {"APP_ENV": "prod"}),
    ("APP_ENV=development", {"APP_ENV": "development"}),
    ("ENV=production", {"ENV": "production"}),
    ("ENVIRONMENT=production", {"ENVIRONMENT": "production"}),
)


# Variables the probe sets itself; reading them is not a branch to explore.
BASE_ENV = frozenset({"HOME", "PATH", "LANG"})
# Where os.getenv, Mapping.get and Mapping.__contains__ live, as their frames name
# them. _collections_abc is frozen, so its frames say "<frozen _collections_abc>",
# not its file path.
MAPPING_FILES = frozenset({os.getenv.__code__.co_filename, Mapping.get.__code__.co_filename})
FLAG_VALUES = ("1", "true", "yes", "on")
MAX_INTS = 64
MAX_STRINGS = 32
MAX_ENV_KEYS = 8
_FOLD = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.LShift: operator.lshift,
}


def _fold(node: ast.AST) -> object:
    """The value of a constant expression such as ``10**12`` or ``"leg" + "acy"``, else None.

    Only literals and arithmetic on them, with sizes bounded so a hostile source cannot
    make the fold itself expensive. Nothing is executed.
    """
    if isinstance(node, ast.Constant) and type(node.value) in (int, str):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _fold(node.operand)
        if type(value) is int:
            return -value if isinstance(node.op, ast.USub) else value
        return None
    if isinstance(node, ast.BinOp) and type(node.op) in _FOLD:
        left, right = _fold(node.left), _fold(node.right)
        if type(left) is int and type(right) is int:
            if isinstance(node.op, (ast.Pow, ast.LShift)) and not (0 <= right <= 256 and abs(left) <= 2**64):
                return None
            if isinstance(node.op, (ast.FloorDiv, ast.Mod)) and right == 0:
                return None
            return _FOLD[type(node.op)](left, right)
        if type(left) is str and type(right) is str and isinstance(node.op, ast.Add):
            return left + right
    return None


def harvest(source: str) -> Constants:
    """Integer and string literals in the module source, constant arithmetic folded."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return NO_CONSTANTS
    ints: dict[int, None] = {}
    strings: dict[str, None] = {}
    for node in ast.walk(tree):
        value = _fold(node)
        if type(value) is int and value.bit_length() <= 128:
            ints[value] = None
        elif type(value) is str and 0 < len(value) <= 64:
            strings[value] = None
    return Constants(tuple(ints)[:MAX_INTS], tuple(strings)[:MAX_STRINGS])


def _accepts_constants(check) -> bool:
    try:
        inspect.signature(check).bind(None, NO_CONSTANTS)
    except (TypeError, ValueError):
        return False
    return True


def _example(item: object) -> list:
    """One violation as [args, required, got]; specs written elsewhere may shape it loosely."""
    if isinstance(item, (tuple, list)) and len(item) == 3:
        args, want, got = item
        args = list(args) if isinstance(args, (tuple, list)) else [args]
        return [args, str(want)[:200], _clip(str(got))]
    return [[], "the documented behavior", _clip(str(item))]


def _clip(text: str, limit: int = 200) -> str:
    """A result can be a list of 100,000 shares; a finding needs only its start."""
    return text if len(text) <= limit else text[:limit] + f"... ({len(text):,} characters)"


def main(argv: list[str]) -> int:
    repo, path, *rest = argv
    nonce = sys.stdin.readline().strip()
    spec = SPECS[repo]
    module_file = os.path.realpath(path)
    spec_file = os.path.realpath(rest[0]) if rest else None
    effects: list[str] = []
    spec_effects: list[str] = []
    with open(path, encoding="utf-8") as handle:
        constants = harvest(handle.read())  # parsed, never run: before any untrusted code
    env_reads: dict[str, None] = {}

    class RecordingEnviron(type(os.environ)):
        """os.environ that notes every variable the module asks for, however it asks."""

        def __getitem__(self, key):
            if isinstance(key, str) and key not in BASE_ENV:
                frame = sys._getframe(1)
                while frame is not None and frame.f_code.co_filename in MAPPING_FILES:
                    frame = frame.f_back  # past os.getenv, Mapping.get and Mapping.__contains__
                if frame is not None and frame.f_code.co_filename == module_file:
                    env_reads[key] = None  # the module's own code asked, not a library it called
            return super().__getitem__(key)

    os.environ.__class__ = RecordingEnviron  # os.getenv, .get and `in` all go through __getitem__

    def blame() -> str:
        """Whose code made the call: the nearest frame from the spec file or the module file."""
        frame = sys._getframe(2)
        while frame is not None:
            name = frame.f_code.co_filename
            if name == module_file:
                return "module"
            if spec_file is not None and name == spec_file:
                return "spec"
            frame = frame.f_back
        return "module"

    def hook(event: str, args: tuple) -> None:
        what = None
        if event == "open" and args and not isinstance(args[0], int):
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if (isinstance(mode, str) and WRITE_MODES & set(mode)) or flags & WRITE_FLAGS:
                what = f"writes {args[0]}"
        elif event in FILE_EVENTS:
            what = f"{FILE_EVENTS[event]}: {args[0] if args else ''}"
        elif event in NETWORK_EVENTS:
            what = f"network ({event}) {str(args[0])[:80] if args else ''}"
        elif event in PROCESS_EVENTS:
            what = f"starts a process ({event})"
        if what:
            (spec_effects if blame() == "spec" else effects).append(what)
            raise Blocked(what)

    sys.dont_write_bytecode = True  # importing must not write a __pycache__ entry
    sys.addaudithook(hook)  # before any untrusted code: a spec written elsewhere is untrusted too
    violations: list = []
    seen: set[str] = set()
    error = None

    def environments():
        yield from ENVIRONMENTS
        common = {key for _, extra in ENVIRONMENTS for key in extra}
        keys = [key for key in env_reads if key not in common][:MAX_ENV_KEYS]
        values = list(dict.fromkeys(constants.strings + FLAG_VALUES))
        for key in keys:
            for value in values:
                yield (f"{key}={value}", {key: value})

    for index, (label, extra_env) in enumerate(environments()):
        saved = {key: os.environ.get(key) for key in extra_env}
        os.environ.update(extra_env)
        try:
            check = spec.check
            if spec_file is not None:
                try:
                    loader = importlib.util.spec_from_file_location(f"external_spec_{index}", spec_file)
                    external = importlib.util.module_from_spec(loader)
                    loader.loader.exec_module(external)
                    check = external.check
                except BaseException as exc:  # noqa: BLE001 - a spec that cannot load judges nothing
                    failed = f"reviewer failed: the spec could not load: {type(exc).__name__}: {exc}"
                    return _emit(nonce, error=failed[:300])
            module = None
            load_error = None
            try:
                loader = importlib.util.spec_from_file_location(f"{spec.module}_{index}", path)
                module = importlib.util.module_from_spec(loader)
                loader.loader.exec_module(module)
            except BaseException as exc:  # noqa: BLE001 - a module that cannot import is a finding
                load_error = f"importing {spec.module}.py raised {type(exc).__name__}: {exc}"[:300]
            if module is None:
                if index == 0:
                    error = load_error
                else:
                    violations.append(((), f"imports under {label}", load_error or "import failed"))
                found = None
            else:
                fn = getattr(module, spec.function, None)
                if not callable(fn):
                    if index == 0:
                        error = f"{spec.module}.py has no function {spec.function}"
                    else:
                        violations.append(((), f"defines {spec.function} under {label}", "missing"))
                    found = None
                else:
                    try:
                        found = check(fn, constants) if _accepts_constants(check) else check(fn)
                    except Blocked:
                        found = []  # the blocked effect is already recorded against whoever made it
                    except BaseException as exc:  # noqa: BLE001 - a spec that crashes judges nothing
                        found = None  # under any environment: a crash voids the verdict, never blocks
                        where = "" if index == 0 else f" under {label}"
                        error = f"reviewer failed: the spec crashed{where}: {type(exc).__name__}: {exc}"[:300]
            if isinstance(found, (list, tuple)):
                for item in found:
                    key = repr(item)
                    if key not in seen:
                        seen.add(key)
                        violations.append(item)
            elif found is None and module is not None and error is None and index == 0:
                error = "reviewer failed: the spec's check() returned no list of violations"
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        for thread in threading.enumerate():
            if thread is not threading.main_thread():
                thread.join(timeout=3)
        if error is not None:
            break
    if spec_effects:  # the spec misbehaved: its verdict on the module cannot be trusted
        return _emit(nonce, error=f"reviewer failed: the spec itself tried a side effect: {spec_effects[0]}")
    return _emit(
        nonce,
        violations=len(violations),
        examples=[_example(item) for item in violations[:3]],
        effects=list(dict.fromkeys(effects))[:5],
        error=error,
    )


def _emit(nonce: str, *, violations: int = 0, examples=(), effects=(), error: str | None = None) -> int:
    result = {"violations": violations, "examples": list(examples), "effects": list(effects), "error": error}
    sys.stdout.write(nonce + json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
