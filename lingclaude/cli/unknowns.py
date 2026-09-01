"""Phase 4 / Wieman C13 — `lingclaude unknowns` CLI subcommand.

Lists, adds, and resolves known unknowns declared in LACP plugin
manifests. This is the user-visible interface to the C13 mechanism:
without it, the manifest field is decoration; with it, unknowns are
queryable and trackable.

Usage:
    lingclaude unknowns list                       # show all unknowns across plugins
    lingclaude unknowns list --plugin <name>       # filter to one plugin
    lingclaude unknowns list --severity block      # only blockers
    lingclaude unknowns add --plugin <p> --claim "..."
    lingclaude unknowns resolve --plugin <p> --claim-substring "..."

The CLI scans <repo_root>/.lacp/plugins/*.yaml (or wherever manifests
are stored) for declared Unknowns. In Phase 4 trimmed version, we
support loading from a single in-memory dict (for tests) and from
the repo's manifest dir.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from lingclaude.lacp.manifest import Plugin, Unknown


# ─────────────────────────────────────────────────────────────
# Core operations (CLI-independent — testable)
# ─────────────────────────────────────────────────────────────


def collect_unknowns(plugins: Iterable[Plugin]) -> list[tuple[str, Unknown]]:
    """Flatten (plugin_name, Unknown) pairs across plugins."""
    out: list[tuple[str, Unknown]] = []
    for p in plugins:
        for u in p.known_unknowns:
            out.append((p.name, u))
    return out


def filter_unknowns(
    items: list[tuple[str, Unknown]],
    *,
    plugin: str | None = None,
    severity: str | None = None,
    category: str | None = None,
) -> list[tuple[str, Unknown]]:
    """Apply CLI filters."""
    result = items
    if plugin is not None:
        result = [(p, u) for p, u in result if p == plugin]
    if severity is not None:
        result = [(p, u) for p, u in result if u.severity == severity]
    if category is not None:
        result = [(p, u) for p, u in result if u.category == category]
    return result


def format_unknowns(items: list[tuple[str, Unknown]]) -> str:
    """Render unknowns as a human-readable table."""
    if not items:
        return "(no known unknowns declared)"
    lines = ["plugin               | severity | category   | owner       | claim"]
    lines.append("-" * 90)
    for plugin_name, u in items:
        claim = u.claim[:50] + ("..." if len(u.claim) > 50 else "")
        lines.append(
            f"{plugin_name[:20]:<20} | {u.severity:<8} | {u.category:<10} | "
            f"{u.owner[:11]:<11} | {claim}"
        )
    return "\n".join(lines)


def find_unknown(
    items: list[tuple[str, Unknown]],
    plugin_name: str,
    claim_substring: str,
) -> tuple[str, Unknown] | None:
    """Find an unknown by plugin name + claim substring match."""
    for p, u in items:
        if p == plugin_name and claim_substring in u.claim:
            return (p, u)
    return None


# ─────────────────────────────────────────────────────────────
# Manifest discovery — load from <repo>/.lacp/plugins/*.yaml
# ─────────────────────────────────────────────────────────────


def load_plugins_from_dir(manifest_dir: Path) -> list[Plugin]:
    """Load all plugin manifests from a directory.

    Phase 4 trimmed: only supports the .yaml subset that LACP actually
    uses. Full YAML schema parser lives in lacp/marketplace.py; here
    we use a lightweight regex pass to extract known_unknowns blocks.
    """
    import re

    plugins: list[Plugin] = []
    if not manifest_dir.exists():
        return plugins

    yaml_block_re = re.compile(r"^known_unknowns:\s*$", re.MULTILINE)
    item_re = re.compile(
        r"^\s*-\s+claim:\s*['\"]?(?P<claim>[^'\"]+?)['\"]?\s*$",
        re.MULTILINE,
    )

    for path in sorted(manifest_dir.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if not yaml_block_re.search(text):
            continue
        # Crude parse — extract a list of claims. Other fields (severity
        # / owner) require a real YAML parser; deferred to a later phase.
        plugin_name = path.stem
        claims = item_re.findall(text)
        if not claims:
            continue
        unknowns = tuple(
            Unknown(claim=c.strip(), category="general", owner="", severity="info")
            for c in claims
        )
        # Construct a minimal Plugin shim (not the full LACP validation)
        from lingclaude.lacp.manifest import (
            Interface, Transport, OutputRecipient, Replaceable, SCHEMA_VERSION,
        )
        plugins.append(
            Plugin(
                name=plugin_name,
                version="0.0.0",  # unknown — caller should re-load full manifest
                owner="unknown",   # unknown
                description=f"discovered from {path.name}",
                interface=Interface(input_schema="{}", output_schema="{}", error_format="{}"),
                transports=[Transport.UI],
                output_recipient=OutputRecipient.LINGBUS,
                replaceable=Replaceable.FALSE,
                schema_version=SCHEMA_VERSION,
                known_unknowns=unknowns,
            )
        )
    return plugins


# ─────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lingclaude unknowns",
        description="List / add / resolve known unknowns in LACP plugin manifests",
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List known unknowns")
    p_list.add_argument("--plugin", help="filter to a single plugin")
    p_list.add_argument("--severity", choices=["info", "warn", "block"])
    p_list.add_argument("--category", help="filter by category")
    p_list.add_argument(
        "--json", action="store_true", help="output as JSON instead of table",
    )
    p_list.add_argument(
        "--manifest-dir",
        type=Path,
        default=Path(".lacp/plugins"),
        help="directory to scan for manifests (default: .lacp/plugins)",
    )

    p_add = sub.add_parser("add", help="Add a known unknown (interactive)")
    p_add.add_argument("--plugin", required=True)
    p_add.add_argument("--claim", required=True)
    p_add.add_argument("--severity", choices=["info", "warn", "block"], default="info")
    p_add.add_argument("--category", default="general")
    p_add.add_argument("--owner", default="")

    p_resolve = sub.add_parser("resolve", help="Mark a known unknown as resolved")
    p_resolve.add_argument("--plugin", required=True)
    p_resolve.add_argument("--claim-substring", required=True)

    return p


def cmd_list(args: argparse.Namespace) -> int:
    plugins = load_plugins_from_dir(args.manifest_dir)
    items = collect_unknowns(plugins)
    items = filter_unknowns(
        items, plugin=args.plugin, severity=args.severity, category=args.category,
    )
    if args.json:
        out = [
            {"plugin": p, **asdict(u)}
            for p, u in items
        ]
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(format_unknowns(items))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """Print the addition suggestion. In Phase 4 trimmed version this
    doesn't write back to the manifest file — it prints a YAML snippet
    the user can paste in. Full write-back requires manifest re-validation."""
    snippet = (
        f"  - claim: {args.claim!r}\n"
        f"    category: {args.category}\n"
        f"    severity: {args.severity}\n"
        f"    owner: {args.owner!r}\n"
    )
    print(f"# Add the following to plugin '{args.plugin}':\n")
    print(f"known_unknowns:\n{snippet}")
    print(
        "# (Phase 4 trimmed: copy-paste into the manifest. "
        "Auto-rewrite coming in a follow-up phase.)"
    )
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    """Print which unknown would be removed. Phase 4 trimmed version."""
    plugins = load_plugins_from_dir(Path(".lacp/plugins"))
    items = collect_unknowns(plugins)
    target = find_unknown(items, args.plugin, args.claim_substring)
    if target is None:
        print(
            f"# No unknown in plugin {args.plugin!r} matching {args.claim_substring!r}",
            file=sys.stderr,
        )
        return 1
    _plugin_name, u = target
    print(f"# Resolving unknown: {u.claim!r}")
    print(
        "# (Phase 4 trimmed: manual edit required to remove from manifest. "
        "Auto-rewrite coming in a follow-up phase.)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "list":
        return cmd_list(args)
    if args.command == "add":
        return cmd_add(args)
    if args.command == "resolve":
        return cmd_resolve(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
