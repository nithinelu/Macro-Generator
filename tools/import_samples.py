"""Build macro_generator/samples_data from a clone of CiscoDevNet/roomdevices-macros-samples.

    git clone --depth 1 https://github.com/CiscoDevNet/roomdevices-macros-samples.git <dir>
    py tools/import_samples.py <dir>

Only text is read; nothing from the repo is executed. Images, zips and PDFs are not copied.
Files that a manifest references on *other* Cisco repos (which declare no licence) are not copied
either: the importer downloads them once to pin a commit + SHA-256, and the tool re-downloads
and verifies them when a user needs them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_generator.samples import DATA_DIR, MAX_REMOTE_BYTES, REMOTE_HOSTS, read_param  # noqa: E402

UPSTREAM = "https://github.com/CiscoDevNet/roomdevices-macros-samples"
# repo -> commit that was reviewed. Bump deliberately; the SHA-256 recorded below changes with it.
REMOTE_PINS = {
    "cisco-ce/guido": "05cf0d06011f3cf687e384c5d035083e160d998a",
    "cisco-ce/roomos-samples": "fe6b9d206e9f611035d0694134b6e97e6dbde69d",
}
SECRET_PARAM = re.compile(r"password|token|secret|apikey|api_key", re.I)
CAUTIONS = {
    "report-issue": (
        "Sends the ServiceNow username/password and Webex bot token you enter in the query string of a page "
        "hosted at cisco-ce.github.io. Use a test ServiceNow instance and a throw-away bot, or host the page yourself."
    ),
}
SKIP_DIRS = {".git", ".vs", "run-macro"}
ROOT_TOOLING = {"universal-adapter.js", "make-manifest.js"}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())


def pin_remote(url: str) -> tuple[str, str]:
    """Return (pinned_url, text). Downloads, size-checks and hashes the file."""
    parts = urlparse(url)
    if parts.scheme != "https" or parts.hostname not in REMOTE_HOSTS:
        raise SystemExit(f"Unexpected remote host in manifest: {url}")
    org, repo, _ref, *rest = parts.path.lstrip("/").split("/")
    key = f"{org}/{repo}"
    if key not in REMOTE_PINS:
        raise SystemExit(f"No reviewed pin for {key} (needed by {url}). Review it, then add it to REMOTE_PINS.")
    pinned = f"https://{parts.hostname}/{key}/{REMOTE_PINS[key]}/{'/'.join(rest)}"
    with urllib.request.urlopen(urllib.request.Request(pinned, headers={"User-Agent": "Macro-Generator"}), timeout=30) as r:
        raw = r.read(MAX_REMOTE_BYTES + 1)
    if len(raw) > MAX_REMOTE_BYTES:
        raise SystemExit(f"{pinned} is unexpectedly large")
    return pinned, raw


def remote_ref(url: str) -> tuple[dict, str]:
    pinned, raw = pin_remote(url)
    ref = {"url": pinned, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    return ref, raw.decode("utf-8-sig").replace("\r\n", "\n")


def panel_id_of(xml_text: str, fallback: str) -> str:
    try:
        pid = ET.fromstring(xml_text.strip()).findtext("Panel/PanelId")
    except ET.ParseError:
        pid = None
    return (pid or "").strip() or fallback


def first_paragraph(md: str) -> str:
    for block in re.split(r"\n\s*\n", md):
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)|<[^>]+>|^#+\s*", "", block, flags=re.M).strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) > 30 and not text.startswith(("#", "|", "```")):
            return text[:280]
    return ""


def find_file(folder: Path, payload: str) -> Path:
    rel = payload[2:] if payload.startswith("./") else payload
    direct = folder / rel
    if direct.is_file():
        return direct
    lower = {p.relative_to(folder).as_posix().lower(): p for p in folder.rglob("*") if p.is_file()}
    if rel.lower() in lower:
        return lower[rel.lower()]
    raise SystemExit(f"{folder.name}: manifest references missing file {payload}")


def build_sample(folder: Path, listing: dict[str, dict], out_root: Path, report: list[str]) -> dict:
    slug = slugify(folder.name)
    out = out_root / slug
    texts: dict[str, str] = {}                      # name -> text for macros (param check)

    def stash(path: Path) -> str:
        rel = path.relative_to(folder).as_posix()
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        text = read_text(path)
        target.write_text(text, encoding="utf-8", newline="\n")
        return rel

    manifest_path = folder / "manifest.json"
    manifest = json.loads(read_text(manifest_path))["profile"] if manifest_path.exists() else None
    js_files = sorted(p for p in folder.rglob("*.js") if p.is_file())
    xml_files = sorted(p for p in folder.rglob("*.xml") if p.is_file())
    used: set[Path] = set()

    macros: list[dict] = []
    panels: list[dict] = []

    if manifest:
        for item in manifest.get("macro", {}).get("items", []):
            kind = "library" if item["type"] == "library" else "primary"
            if item["payload"].startswith("http"):
                ref, text = remote_ref(item["payload"])
                macros.append({"name": item["id"], "kind": kind, "remote": ref})
                texts[item["id"]] = text
            else:
                path = find_file(folder, item["payload"])
                used.add(path)
                macros.append({"name": item["id"], "kind": kind, "file": stash(path)})
                texts[item["id"]] = read_text(path)
        for item in manifest.get("roomcontrol", {}).get("items", []):
            if item["payload"].startswith("http"):
                ref, text = remote_ref(item["payload"])
                panels.append({"panel_id": panel_id_of(text, item["id"]), "remote": ref})
            else:
                path = find_file(folder, item["payload"])
                used.add(path)
                panels.append({"panel_id": panel_id_of(read_text(path), item["id"]), "file": stash(path)})
        for path in js_files:
            if path not in used:
                macros.append({"name": path.stem, "kind": "example", "file": stash(path)})
                texts[path.stem] = read_text(path)
                used.add(path)
        for path in xml_files:
            if path not in used:
                panels.append({"panel_id": panel_id_of(read_text(path), path.stem), "file": stash(path), "optional": True})
                used.add(path)
    else:
        top_js = [p for p in js_files if p.parent == folder]
        imported = {m for p in top_js for m in re.findall(r"from\s+['\"]\./([^'\"]+)['\"]", read_text(p))}
        for path in js_files:
            if path.parent == folder:
                kind = "library" if path.stem in imported else "primary"
            else:
                kind = "example"
            macros.append({"name": path.stem, "kind": kind, "file": stash(path)})
            texts[path.stem] = read_text(path)
            used.add(path)
        for path in xml_files:
            panels.append({
                "panel_id": panel_id_of(read_text(path), slugify(path.stem)), "file": stash(path),
                "optional": path.parent != folder,
            })
            used.add(path)

    extras = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path in used or path.name == "manifest.json":
            continue
        is_root_readme = path.parent == folder and path.name.lower() == "readme.md"
        if is_root_readme:
            continue
        if path.suffix.lower() in (".json", ".md"):
            extras.append(stash(path))

    readme_path = next((p for p in folder.iterdir() if p.is_file() and p.name.lower() == "readme.md"), None)
    readme_rel = stash(readme_path) if readme_path else None
    readme_text = read_text(readme_path) if readme_path else ""

    # ---- settings
    primaries = [m["name"] for m in macros if m["kind"] == "primary"]
    params = []
    for up in (manifest or {}).get("userParams", []):
        domain = up.get("domain")
        target = domain if domain in texts else (primaries[0] if len(primaries) == 1 else None)
        if target != domain:
            report.append(f"  note: {folder.name}: setting '{up['id']}' names macro '{domain}' (not found) -> using '{target}'")
        ptype = "bool" if up["type"] in ("bool", "boolean") else up["type"]
        param = {"id": up["id"], "name": up.get("name", up["id"]), "type": ptype, "macro": target,
                 "info": up.get("info", ""), "required": bool(up.get("required", False))}
        if "default" in up:
            param["default"] = up["default"]
        if up.get("values"):
            param["values"] = up["values"]
        if not target or not read_param(texts.get(target, ""), up["id"])[0]:
            report.append(f"  note: {folder.name}: setting '{up['id']}' is not a simple top-level value (edit the macro by hand)")
        params.append(param)

    meta = listing.get(norm(folder.name), {})
    secret_ids = [p["id"] for p in params if SECRET_PARAM.search(p["id"])]
    caution = CAUTIONS.get(slug, "")
    if secret_ids:
        note = f"{', '.join(secret_ids)} is written in plain text into the macro source on the device."
        caution = f"{caution} {note}".strip()

    return {
        "id": slug,
        "title": meta.get("title", folder.name),
        "description": meta.get("description") or first_paragraph(readme_text),
        "symbol": meta.get("symbol", ""),
        "tags": [t.strip() for t in meta.get("tags", "").split(",") if t.strip()],
        "folder": folder.name,
        "upstream": f"{UPSTREAM}/tree/master/{quote(folder.name)}",
        "readme": readme_rel,
        "macros": macros,
        "panels": panels,
        "params": params,
        "extras": extras,
        "caution": caution,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", type=Path, help="local clone of roomdevices-macros-samples")
    args = ap.parse_args()
    repo: Path = args.repo.resolve()
    if not (repo / "macro-list.json").exists():
        print("That does not look like the samples repo (no macro-list.json).", file=sys.stderr)
        return 2

    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    listing = {norm(e["title"]): e for e in json.loads(read_text(repo / "macro-list.json"))}
    folders = [p for p in sorted(repo.iterdir()) if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(".")]

    staging = DATA_DIR.with_name("samples_data.new")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()

    report: list[str] = []
    samples = [build_sample(f, listing, staging, report) for f in folders]
    samples.sort(key=lambda s: s["title"].lower())

    unmatched = [e["title"] for e in listing.values() if norm(e["title"]) not in {norm(f.name) for f in folders}]
    if unmatched:
        report.append(f"  note: macro-list.json entries without a folder: {unmatched}")

    shutil.copyfile(repo / "LICENSE", staging / "LICENSE")
    catalog = {
        "source": {"repo": UPSTREAM, "commit": commit, "license": "MIT (Copyright 2018 Cisco Systems) - see LICENSE"},
        "samples": samples,
    }
    (staging / "catalog.json").write_text(json.dumps(catalog, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    (staging / "SOURCES.md").write_text(
        f"# Cisco macro samples - provenance\n\n"
        f"Imported from {UPSTREAM} at commit `{commit}` (MIT licence, see `LICENSE`).\n"
        f"Images, zip packages and PDFs were not copied; see the upstream repo for those.\n\n"
        f"## Downloaded on demand (not bundled - those repos declare no licence)\n\n"
        + "".join(
            f"* `{m.get('remote', {}).get('url')}`  (sha256 `{m['remote']['sha256']}`)\n"
            for s in samples for m in s["macros"] + s["panels"] if m.get("remote")
        ),
        encoding="utf-8", newline="\n",
    )

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    staging.rename(DATA_DIR)

    n_macros = sum(len(s["macros"]) for s in samples)
    n_panels = sum(len(s["panels"]) for s in samples)
    n_params = sum(len(s["params"]) for s in samples)
    print(f"Imported {len(samples)} samples: {n_macros} macros, {n_panels} UI panels, {n_params} settings (commit {commit[:10]}).")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
