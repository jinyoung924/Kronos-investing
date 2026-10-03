"""One strategy = one file. The registry imports every module of the D_strategy package except base, registry
and run_strategy, collects the classes decorated with @register, and checks that class name attribute ==
file name == configs strategies key. get_strategy(name, cfg) builds an instance from cfg.strategies.<name>.
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from common.config import cfg_get
from D_strategy.base import Strategy

PACKAGE = "D_strategy"
NON_STRATEGY_MODULES = {"__init__", "base", "registry", "run_strategy"}
_REGISTRY: dict[str, type[Strategy]] = {}
_discovered = False


def register(cls: type[Strategy]) -> type[Strategy]:
    module_stem = cls.__module__.rsplit(".", 1)[-1]
    if not cls.name:
        raise ImportError(f"{cls.__module__}.{cls.__name__} has no `name`")
    if cls.name != module_stem:
        raise ImportError(f"strategy name {cls.name!r} must equal its file name {module_stem!r} ({cls.__module__})")
    if cls.name in _REGISTRY and _REGISTRY[cls.name] is not cls:
        raise ImportError(f"strategy {cls.name!r} registered twice")
    _REGISTRY[cls.name] = cls
    return cls


def strategy_module_names() -> list[str]:
    """Module stems in the package folder that should each hold one registered strategy."""
    pkg_dir = Path(__file__).resolve().parent
    return sorted(m.name for m in pkgutil.iter_modules([str(pkg_dir)]) if m.name not in NON_STRATEGY_MODULES and not m.ispkg)


def discover() -> dict[str, type[Strategy]]:
    """Import every strategy module once; a module without a registered class is an error."""
    global _discovered
    if not _discovered:
        for stem in strategy_module_names():
            importlib.import_module(f"{PACKAGE}.{stem}")
            if stem not in _REGISTRY:
                raise ImportError(f"{PACKAGE}/{stem}.py defines no @register-ed Strategy named {stem!r}")
        _discovered = True
    return dict(_REGISTRY)


def available() -> list[str]:
    return sorted(discover())


def resolve(spec: str) -> list[str]:
    """'all' -> every registered strategy; 'a,b' -> those names (unknown names raise)."""
    names = available()
    if spec is None or spec.strip() == "" or spec.strip() == "all":
        return names
    out = []
    for n in (x.strip() for x in spec.split(",")):
        if not n:
            continue
        if n not in names:
            raise ValueError(f"unknown strategy {n!r}; available: {names}")
        if n not in out:
            out.append(n)
    return out


def get_strategy(name: str, cfg: dict) -> Strategy:
    cls = discover().get(name)
    if cls is None:
        raise ValueError(f"unknown strategy {name!r}; available: {available()}")
    params = cfg_get(cfg, f"strategies.{name}")
    if not isinstance(params, dict):
        raise ValueError(f"configs strategies.{name} is missing (every strategy needs profile, schedule and its parameters)")
    return cls(params, seed=int(cfg_get(cfg, "project.seed", 0)))
