"""Provider plugin loader."""
import importlib
import pkgutil
from typing import Dict

try:
    from .base import ProviderPlugin
except Exception:
    from providers.base import ProviderPlugin


def load_plugins() -> Dict[str, ProviderPlugin]:
    plugins = {}
    try:
        import providers as pkg
    except Exception:
        return plugins
    for _, name, is_pkg in pkgutil.iter_modules(pkg.__path__):
        if name in ("base", "loader") or name.startswith("_"):
            continue
        if not is_pkg:
            continue
        try:
            mod = importlib.import_module(f"providers.{name}")
            if hasattr(mod, "get_plugin"):
                p = mod.get_plugin()
                if isinstance(p, ProviderPlugin) and getattr(p, "name", None):
                    plugins[p.name] = p
            elif hasattr(mod, "PLUGIN"):
                p = mod.PLUGIN
                if isinstance(p, ProviderPlugin) and getattr(p, "name", None):
                    plugins[p.name] = p
        except Exception as e:
            print(f"[providers] failed to load {name}: {e}")
    return plugins
