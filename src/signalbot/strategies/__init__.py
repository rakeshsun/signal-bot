"""Strategy registry. Every module in this folder that doesn't start with "_" is loaded,
and each Strategy subclass decorated with @register becomes available by name."""

from __future__ import annotations

import importlib
import pkgutil

from .base import Context, Strategy

_REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    if cls.name in _REGISTRY and _REGISTRY[cls.name] is not cls:
        raise ValueError(f"duplicate strategy name {cls.name!r}")
    _REGISTRY[cls.name] = cls
    return cls


def _load_all() -> None:
    for m in pkgutil.iter_modules(__path__):
        if not m.name.startswith("_") and m.name != "base":
            importlib.import_module(f"{__name__}.{m.name}")


def get(name: str) -> type[Strategy]:
    _load_all()
    if name not in _REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; available: {', '.join(sorted(_REGISTRY))}")
    return _REGISTRY[name]


def available() -> dict[str, type[Strategy]]:
    _load_all()
    return dict(sorted(_REGISTRY.items()))


__all__ = ["Context", "Strategy", "available", "get", "register"]
