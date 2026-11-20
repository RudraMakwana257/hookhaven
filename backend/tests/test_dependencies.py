"""Guards against 'works on my machine': everything `app/` imports must be a *runtime* dependency.

Tests run with the dev extras installed, so a library that is only listed under `dev` imports fine
here and then crashes the production image on start (this happened with httpx and the worker).
"""

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# Installed by one of our direct dependencies and part of its public contract.
PROVIDED_BY_A_DIRECT_DEPENDENCY = {
    "starlette": "fastapi",
    "pydantic": "fastapi / pydantic-settings",
}


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _runtime_dependencies() -> set[str]:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text())["project"]
    return {
        _normalise(re.split(r"[\[<>=!~ ;]", spec, maxsplit=1)[0])
        for spec in project["dependencies"]
    }


def _third_party_imports() -> dict[str, set[str]]:
    """{top-level module: files importing it}, ignoring the stdlib and our own package."""
    found: dict[str, set[str]] = {}
    for path in (BACKEND / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module]
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
                else []
            )
            for module in modules:
                top = module.split(".")[0]
                if top != "app" and top not in sys.stdlib_module_names:
                    found.setdefault(top, set()).add(str(path.relative_to(BACKEND)))
    return found


def test_every_third_party_import_in_app_is_a_runtime_dependency() -> None:
    declared = _runtime_dependencies()
    distributions = packages_distributions()
    missing: list[str] = []
    for module, files in sorted(_third_party_imports().items()):
        if module in PROVIDED_BY_A_DIRECT_DEPENDENCY:
            continue
        owners = {_normalise(d) for d in distributions.get(module, [module])}
        if not owners & declared:
            missing.append(
                f"{module} (imported by {sorted(files)[0]}; provided by {sorted(owners)})"
            )
    assert not missing, "not listed under [project].dependencies: " + "; ".join(missing)
