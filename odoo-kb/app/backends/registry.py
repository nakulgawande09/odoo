"""Backend factory/registry. Maps backend names to constructors."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from app.core.interfaces import SearchBackend
    from config.settings import Settings

_REGISTRY: dict[str, Callable[..., Any]] = {}


def register_backend(name: str):
    """Decorator to register a backend class."""
    def decorator(cls):
        _REGISTRY[name] = cls
        return cls
    return decorator


def create_backend(settings: "Settings") -> "SearchBackend":
    """Create a backend instance from settings."""
    factory = _REGISTRY.get(settings.search_backend)
    if factory is None:
        available = ", ".join(_REGISTRY.keys()) or "(none)"
        raise ValueError(
            f"Unknown search backend: {settings.search_backend!r}. "
            f"Available: {available}"
        )
    return factory(settings)


def available_backends() -> list[str]:
    return list(_REGISTRY.keys())
