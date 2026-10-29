import json
import sys

try:
    from providers.loader import load_plugins
except Exception:
    from ..loader import load_plugins


def cmd_list():
    plugins = load_plugins()
    print(json.dumps({"plugins": list(plugins.keys())}, ensure_ascii=False, indent=2))


def cmd_probe():
    plugins = load_plugins()
    out = {}
    for name, p in plugins.items():
        try:
            out[name] = p.probe() if hasattr(p, "probe") else {"ok": True}
        except Exception as e:
            out[name] = {"ok": False, "error": str(e)}
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main():
    if len(sys.argv) < 2:
        print("usage: python -m providers.cli list|probe")
        return
    cmd = sys.argv[1]
    if cmd == "list":
        cmd_list()
    elif cmd == "probe":
        cmd_probe()
    else:
        print("usage: python -m providers.cli list|probe")


if __name__ == "__main__":
    main()
