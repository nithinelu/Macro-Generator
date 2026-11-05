"""Macro Generator - build Cisco RoomOS macros from templates, or install Cisco's sample macros.

    python main.py                       open the GUI
    python main.py list                  list the generated templates
    python main.py samples [words] [--tag T]          list Cisco's sample macros
    python main.py init <template|sample-id> <profile.json>   write a starter profile
    python main.py render <profile.json> [-o out]     generate a macro (.js) / export a sample (folder)
    python main.py push <profile.json> --host IP [--host IP ...] --user admin

Samples that need files from other (unlicensed) Cisco repos need --download on render / push.

Author : Nithin Eluvathingal
License: MIT (see LICENSE)
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path

from macro_generator.about import APP_NAME, AUTHOR, LICENSE, VERSION, YEAR
from macro_generator.device import push_bundle
from macro_generator.profile import generate, load_profile, save_profile
from macro_generator.samples import SAMPLE_PREFIX, RemoteError, SampleSession, download_remote, get_sample, load_catalog, search
from macro_generator.templates import BY_ID, TEMPLATES


def cmd_list(_args) -> int:
    for t in TEMPLATES:
        print(f"{t.id:15} {t.name}")
    return 0


def cmd_samples(args) -> int:
    found = search(" ".join(args.words), args.tag or "")
    for s in found:
        flags = []
        if s.remotes:
            flags.append("needs --download")
        if s.params:
            flags.append(f"{len(s.params)} setting(s)")
        print(f"{s.id:42} {s.title}" + (f"  [{', '.join(flags)}]" if flags else ""))
    print(f"\n{len(found)} of {len(load_catalog())} samples", file=sys.stderr)
    return 0


def cmd_init(args) -> int:
    name = args.template
    sample_id = name[len(SAMPLE_PREFIX):] if name.startswith(SAMPLE_PREFIX) else name
    if name in BY_ID:
        template = BY_ID[name]
        save_profile(args.profile, template, template.default_macro_name, template.defaults())
    else:
        try:
            session = SampleSession(get_sample(sample_id))
        except KeyError:
            print(f"Unknown template or sample '{name}'. Run 'list' or 'samples' to see the options.", file=sys.stderr)
            return 2
        Path(args.profile).write_text(json.dumps(session.to_profile(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {args.profile} - edit it, then run: python main.py render {args.profile}")
    return 0


def _read_profile(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _open_sample(data: dict, download: bool) -> SampleSession:
    session = SampleSession(get_sample(data["template"][len(SAMPLE_PREFIX):]))
    for problem in session.apply_profile(data):
        print(f"warning: {problem}", file=sys.stderr)
    if session.missing_remote():
        if not download:
            raise ValueError("this sample needs files from other Cisco repos (not bundled); re-run with --download")
        print("Downloading required files (pinned commit, SHA-256 checked)...", file=sys.stderr)
        try:
            session.load_remote(download_remote)
        except RemoteError as exc:
            raise ValueError(str(exc)) from None
        for problem in session.apply_profile({"values": data.get("values", {})}):
            print(f"warning: {problem}", file=sys.stderr)
    return session


def _report(errors: list[str]) -> int:
    print("Fix these first:\n  " + "\n  ".join(errors), file=sys.stderr)
    return 1


def cmd_render(args) -> int:
    data = _read_profile(args.profile)
    if str(data.get("template", "")).startswith(SAMPLE_PREFIX):
        session = _open_sample(data, args.download)
        bundle = session.bundle()
        if bundle.errors:
            return _report(bundle.errors)
        target = Path(args.output or session.sample.id)
        target.mkdir(parents=True, exist_ok=True)
        files = session.export_files(bundle)
        for name, text in files.items():
            (target / re.sub(r'[<>:"/\\|?*]', "_", name)).write_text(text, encoding="utf-8", newline="\n")
        for w in bundle.warnings:
            print(f"warning: {w}", file=sys.stderr)
        print(f"Wrote {len(files)} file(s) to {target}")
        return 0

    template, macro_name, values = load_profile(args.profile)
    js, errors = generate(template, macro_name, values)
    if errors:
        return _report(errors)
    if args.output:
        Path(args.output).write_text(js, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(js)
    return 0


def cmd_push(args) -> int:
    data = _read_profile(args.profile)
    activate = not args.no_activate
    if str(data.get("template", "")).startswith(SAMPLE_PREFIX):
        session = _open_sample(data, args.download)
        bundle = session.bundle()
        if bundle.errors:
            return _report(bundle.errors)
        for w in bundle.warnings:
            print(f"warning: {w}", file=sys.stderr)
        macros = [(m.name, m.code, m.activate and activate) for m in bundle.macros]
        panels = [(p.panel_id, p.xml) for p in bundle.panels]
    else:
        template, macro_name, values = load_profile(args.profile)
        js, errors = generate(template, macro_name, values)
        if errors:
            return _report(errors)
        macros, panels = [(macro_name, js, activate)], []

    password = os.environ.get("DEVICE_PASSWORD") or getpass.getpass(f"Password for {args.user}: ")
    failed = 0
    for host in args.host:
        res = push_bundle(host, args.user, password, macros, panels, verify_tls=args.verify_tls)
        print(f"{host}: {'OK - ' + ', '.join(res.steps) if res.ok else 'FAILED - ' + res.error}")
        failed += 0 if res.ok else 1
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION} - (c) {YEAR} {AUTHOR} - {LICENSE}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list").set_defaults(func=cmd_list)

    p = sub.add_parser("samples")
    p.add_argument("words", nargs="*", help="search words")
    p.add_argument("--tag")
    p.set_defaults(func=cmd_samples)

    p = sub.add_parser("init")
    p.add_argument("template", help="a template id or a sample id")
    p.add_argument("profile")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("render")
    p.add_argument("profile")
    p.add_argument("-o", "--output", help="file (templates) or folder (samples)")
    p.add_argument("--download", action="store_true", help="fetch files a sample needs from other Cisco repos")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("push")
    p.add_argument("profile")
    p.add_argument("--host", action="append", required=True, help="device IP / hostname (repeatable)")
    p.add_argument("--user", default="admin")
    p.add_argument("--no-activate", action="store_true")
    p.add_argument("--download", action="store_true", help="fetch files a sample needs from other Cisco repos")
    p.add_argument("--verify-tls", action="store_true", help="validate the device certificate (devices ship self-signed)")
    p.set_defaults(func=cmd_push)

    args = parser.parse_args(argv)
    if not args.command:
        from macro_generator.gui import run
        run()
        return 0
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:   # unreadable / malformed profile, unwritable output, missing download
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:                # profile names a sample / template that does not exist
        print(f"Error: unknown sample or template {exc}. Run 'list' or 'samples'.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
