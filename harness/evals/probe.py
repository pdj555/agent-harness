"""Run one changed module against its executable spec, with side effects blocked.

Started by ``spec_monitor`` in a fresh process, in an empty directory that holds a
copy of the module and nothing else, so no conftest.py, test file or fixture can
run. An audit hook installed before the module is imported refuses every file
write, directory change, network call and subprocess, and records each attempt.
Audit hooks cannot be removed once added.

Usage: python -I -m harness.evals.probe <repo> <module.py>, with a nonce on stdin.
Prints one line: the nonce followed by a JSON result. The nonce is read before the
module is imported, so the module cannot see it in argv and forge the result line.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading

from harness.evals.specs import SPECS

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


def main(argv: list[str]) -> int:
    repo, path = argv
    nonce = sys.stdin.readline().strip()
    spec = SPECS[repo]
    effects: list[str] = []

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
            effects.append(what)
            raise Blocked(what)

    module = None
    error = None
    sys.dont_write_bytecode = True  # importing must not write a __pycache__ entry
    sys.addaudithook(hook)
    try:
        loader = importlib.util.spec_from_file_location(spec.module, path)
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
    except BaseException as exc:  # noqa: BLE001 - a module that cannot import is a finding
        error = f"importing {spec.module}.py raised {type(exc).__name__}: {exc}"[:300]
    violations = []
    if module is not None:
        fn = getattr(module, spec.function, None)
        if callable(fn):
            violations = spec.check(fn)
        else:
            error = f"{spec.module}.py has no function {spec.function}"
    for thread in threading.enumerate():
        if thread is not threading.main_thread():
            thread.join(timeout=3)
    result = {
        "violations": len(violations),
        "examples": [[list(args), want, got] for args, want, got in violations[:3]],
        "effects": list(dict.fromkeys(effects))[:5],
        "error": error,
    }
    sys.stdout.write(nonce + json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
