#!/usr/bin/env python3
"""free-pool CLI（最小可复用入口）"""
import argparse
import json
import os
import sys

def cmd_sync(args):
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "sync_free_models.py")
    cmd = [sys.executable, script]
    if args.apply:
        cmd.append("--apply")
    if args.fast:
        cmd.append("--fast")
    subprocess.run(cmd)

def cmd_providers(args):
    try:
        from providers.loader import load_plugins
    except Exception:
        from providers.loader import load_plugins
    plugins = load_plugins()
    if args.cmd == "list":
        print(json.dumps({"plugins": sorted(plugins.keys())}, ensure_ascii=False, indent=2))
    elif args.cmd == "probe":
        out = {}
        for name, p in plugins.items():
            try:
                out[name] = p.probe(os.environ.get(getattr(p, "api_key_env", "") or "", "")) if hasattr(p, "probe") else {"ok": True}
            except Exception as e:
                out[name] = {"ok": False, "error": str(e)}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif args.cmd == "health":
        from providers.cli.health import get_provider_health
        print(json.dumps(get_provider_health(), ensure_ascii=False, indent=2))

def main():
    parser = argparse.ArgumentParser(prog="free-pool", description="Free model pool aggregator")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="Sync free models from providers")
    s.add_argument("--apply", action="store_true")
    s.add_argument("--fast", action="store_true")
    s.set_defaults(func=cmd_sync)
    p = sub.add_parser("providers", help="Provider plugins")
    p.add_argument("action", choices=["list", "probe", "health"])
    p.set_defaults(func=cmd_providers)
    args = parser.parse_args()
    if hasattr(args, "action"):
        args.cmd = args.action
    args.func(args)

if __name__ == "__main__":
    main()
