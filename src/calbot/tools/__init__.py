"""Tool registry: collects TOOLS from every module in this package (spec §5.5).

Adding a capability = drop a module here that exports TOOLS, plus a skills/<name>/SKILL.md.
The registry is also the allowlist: the agent sends exactly these tools and nothing else."""
from __future__ import annotations

import importlib
import pkgutil
import re

from .base import Tool

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ToolSurfaceError(RuntimeError):
    pass


def collect_tools() -> list[Tool]:
    tools: list[Tool] = []
    for mod in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if mod.name == "base":
            continue
        module = importlib.import_module(f"{__name__}.{mod.name}")
        tools.extend(getattr(module, "TOOLS", []))
    return tools


def assert_tool_surface(api_tools: list[dict], registry: list[Tool]) -> None:
    """Startup guard (spec §5.2, E11): the tool list sent to the model must be exactly the registry.
    Anything else (a server tool, a stray built-in, a duplicate) aborts startup."""
    allowed = {t.name for t in registry}
    names = [t.get("name") for t in api_tools]
    problems = []
    if len(names) != len(set(names)):
        problems.append("duplicate tool names")
    for t in api_tools:
        if "type" in t and t["type"] != "custom":
            problems.append(f"non-custom tool type {t['type']!r}")
        if t.get("name") not in allowed:
            problems.append(f"tool {t.get('name')!r} is not in the registry")
        elif not _NAME.match(t["name"]):
            problems.append(f"bad tool name {t['name']!r}")
    missing = allowed - set(names)
    if missing:
        problems.append(f"registry tools missing from request: {sorted(missing)}")
    if problems:
        raise ToolSurfaceError("Tool surface check failed: " + "; ".join(problems))
