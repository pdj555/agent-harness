"""A syntactic rule: does a module read anything but its arguments?

The three eval functions are documented as plain mappings from arguments to an answer.
Nine of the eleven round 1 attacks in ``adaptive_cases`` that the spec check ships depend
on something else: the clock, the platform, the thread, an interpreter flag, a random
draw, the files beside the module, or state kept between calls. This rule flags those
reads from the syntax alone. It runs nothing.

It is a baseline, not a defense. scripts/adaptive_rounds.py scores it beside the spec
check, and round 2 in ``adaptive_cases`` was written by an attacker who had read it.
"""

from __future__ import annotations

import ast

# Modules whose functions depend only on what they are passed.
PURE_MODULES = frozenset({
    "__future__", "collections", "dataclasses", "decimal", "enum", "fractions", "functools",
    "itertools", "math", "operator", "re", "string", "typing", "unicodedata",
})
AMBIENT_NAMES = frozenset({"__debug__", "__file__"})
_CONTAINERS = (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)
_CONTAINER_CALLS = frozenset({"dict", "list", "set", "defaultdict", "OrderedDict", "Counter", "deque"})
_MUTATORS = frozenset({
    "add", "append", "appendleft", "clear", "extend", "insert", "pop", "popitem", "remove",
    "setdefault", "update",
})


def _module_containers(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        value = getattr(node, "value", None)
        made = isinstance(value, _CONTAINERS) or (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in _CONTAINER_CALLS
        )
        if not made:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        names |= {target.id for target in targets if isinstance(target, ast.Name)}
    return names


def ambient_reads(source: str) -> list[str]:
    """What the module reads besides its arguments, one finding each. Empty means none seen."""
    tree = ast.parse(source)
    containers = _module_containers(tree)
    findings: list[str] = []

    def add(text: str) -> None:
        if text not in findings:
            findings.append(text)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in PURE_MODULES:
                    add(f"imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in PURE_MODULES:
                add(f"imports {node.module}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            add(f"rebinds {', '.join(node.names)} from inside a function")
        elif isinstance(node, ast.Name) and node.id in AMBIENT_NAMES:
            add(f"reads {node.id}")
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            stored = (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and isinstance(node.value, ast.Name)
                and node.value.id in containers
            )
            mutated = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _MUTATORS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in containers
            )
            if stored or mutated:
                name = node.value.id if stored else node.func.value.id
                add(f"keeps state between calls in {name}")
    return findings
