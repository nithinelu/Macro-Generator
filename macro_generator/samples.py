"""Cisco roomdevices-macros-samples library: catalog, per-sample editing session and bundle building.

The data under samples_data/ is produced by tools/import_samples.py from
https://github.com/CiscoDevNet/roomdevices-macros-samples (MIT licence, see samples_data/LICENSE).
A few samples depend on files from other Cisco repos that declare no licence; those are *not*
bundled - they are downloaded on demand from a commit-pinned URL and checked against a SHA-256.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

DATA_DIR = Path(__file__).resolve().parent / "samples_data"
CATALOG_FILE = DATA_DIR / "catalog.json"
REMOTE_HOSTS = {"raw.githubusercontent.com"}
MAX_REMOTE_BYTES = 512 * 1024
MAX_ACTIVE_MACROS = 10          # RoomOS limit on simultaneously active macros
SAMPLE_PREFIX = "sample:"


class RemoteError(Exception):
    pass


# --------------------------------------------------------------------------- catalog model

@dataclass
class RemoteRef:
    url: str
    sha256: str
    size: int


@dataclass
class MacroEntry:
    name: str                    # name on the device - other macros import it as './<name>'
    kind: str                    # primary | library | example
    file: str | None = None      # path under the sample folder (None when remote)
    remote: RemoteRef | None = None

    @property
    def key(self) -> str:
        return f"macro:{self.name}"


@dataclass
class PanelEntry:
    panel_id: str
    optional: bool = False
    file: str | None = None
    remote: RemoteRef | None = None

    @property
    def key(self) -> str:
        return f"panel:{self.file or self.panel_id}"


@dataclass
class Param:
    id: str
    name: str
    type: str                    # string | number | bool | option
    macro: str | None            # macro name whose source holds `const|let|var <id>`
    info: str = ""
    default: Any = None
    values: dict[str, str] = field(default_factory=dict)
    required: bool = False


@dataclass
class Sample:
    id: str
    title: str
    description: str
    folder: str
    upstream: str
    symbol: str = ""
    tags: list[str] = field(default_factory=list)
    readme: str | None = None
    macros: list[MacroEntry] = field(default_factory=list)
    panels: list[PanelEntry] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)
    caution: str = ""

    @property
    def remotes(self) -> list[RemoteRef]:
        refs = [m.remote for m in self.macros if m.remote] + [p.remote for p in self.panels if p.remote]
        return refs


def _remote(d: dict | None) -> RemoteRef | None:
    return RemoteRef(**d) if d else None


def _sample_from_dict(d: dict) -> Sample:
    return Sample(
        id=d["id"], title=d["title"], description=d.get("description", ""), folder=d["folder"],
        upstream=d["upstream"], symbol=d.get("symbol", ""), tags=d.get("tags", []), readme=d.get("readme"),
        macros=[MacroEntry(m["name"], m["kind"], m.get("file"), _remote(m.get("remote"))) for m in d["macros"]],
        panels=[PanelEntry(p["panel_id"], p.get("optional", False), p.get("file"), _remote(p.get("remote"))) for p in d["panels"]],
        params=[Param(**p) for p in d.get("params", [])],
        extras=d.get("extras", []), caution=d.get("caution", ""),
    )


@lru_cache(maxsize=1)
def load_catalog() -> list[Sample]:
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    return [_sample_from_dict(s) for s in data["samples"]]


def catalog_source() -> dict:
    return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))["source"]


def get_sample(sample_id: str) -> Sample:
    for s in load_catalog():
        if s.id == sample_id:
            return s
    raise KeyError(sample_id)


def all_tags() -> list[str]:
    return sorted({t for s in load_catalog() for t in s.tags})


def search(query: str = "", tag: str = "") -> list[Sample]:
    words = query.lower().split()
    out = []
    for s in load_catalog():
        hay = f"{s.title} {s.description} {' '.join(s.tags)}".lower()
        if all(w in hay for w in words) and (not tag or tag in s.tags):
            out.append(s)
    return out


def read_data_file(sample: Sample, rel: str) -> str:
    path = (DATA_DIR / sample.id / rel).resolve()
    if DATA_DIR.resolve() not in path.parents:
        raise ValueError(f"Refusing to read outside the sample library: {rel}")
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- README -> plain text

def readme_to_text(md: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)                    # images
    text = re.sub(r"<img[^>]*>", "", text, flags=re.I)
    text = re.sub(r"</?(?:br|div|p|span|center|b|i|u|details|summary)[^>]*>", "", text, flags=re.I)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)   # links keep their URL
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", lambda m: m.group(1).upper(), text, flags=re.M)
    text = re.sub(r"^(\s*)[*-]\s+", lambda m: f"{m.group(1)}\u2022 ", text, flags=re.M)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --------------------------------------------------------------------------- settings ("userParams")
# Contract (from the upstream CONTRIBUTOR.md): a param's id is the name of a top-level
# const/let/var in the macro; the installer replaces its initialiser with the chosen value.

_STRING_LIT = r"""\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'"""
_SIMPLE_LIT = rf"(?:{_STRING_LIT}|-?\d+(?:\.\d+)?|true|false|null|undefined)"


def _decl_re(param_id: str) -> re.Pattern:
    return re.compile(
        rf"^(?P<head>[ \t]{{0,4}}(?:export[ \t]+)?(?:const|let|var)[ \t]+{re.escape(param_id)})"
        rf"(?:[ \t]*=[ \t]*(?P<val>{_SIMPLE_LIT}))?"
        rf"(?P<tail>[ \t]*;?[ \t]*(?://[^\n]*)?)$",
        re.M,
    )


def _parse_literal(lit: str | None) -> Any:
    if lit is None:
        return ""
    if lit in ("true", "false"):
        return lit == "true"
    if lit in ("null", "undefined"):
        return ""
    if lit[0] in "'\"":
        try:
            return ast.literal_eval(lit)
        except (ValueError, SyntaxError):
            return lit[1:-1]
    return float(lit) if "." in lit else int(lit)


def _to_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else repr(value)
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'").replace("\r", "\\r").replace("\n", "\\n")
    return f"'{escaped}'"


def read_param(code: str, param_id: str) -> tuple[bool, Any]:
    """(found, current value). found is False when the declaration is missing or not a simple literal."""
    m = _decl_re(param_id).search(code)
    return (True, _parse_literal(m.group("val"))) if m else (False, None)


def apply_param(code: str, param_id: str, value: Any) -> str:
    pattern = _decl_re(param_id)
    if not pattern.search(code):
        raise KeyError(param_id)
    return pattern.sub(lambda m: f"{m.group('head')} = {_to_literal(value)}{m.group('tail')}", code, count=1)


def option_key(value: Any) -> str:
    """The key an option list uses for a value held in code (options are strings: 'true', '2', 'Third')."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    return "" if value is None else str(value)


def coerce_param(param: Param, raw: Any, existing: Any = "") -> Any:
    """Typed value for a form entry. Raises ValueError with a user-readable message."""
    if param.type == "bool" or param.type == "boolean":
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ("1", "true", "yes", "on")
    if param.type == "number":
        text = str(raw).strip()
        try:
            number = float(text)
        except ValueError:
            raise ValueError(f"{param.name}: enter a number.") from None
        return int(number) if number.is_integer() else number
    if param.type == "option":
        text = option_key(raw)
        if param.values and text not in param.values:
            raise ValueError(f"{param.name}: pick one of the listed options.")
        # keep the type the macro already uses: option keys are strings but code may hold booleans / numbers
        if isinstance(existing, bool) and text.lower() in ("true", "false"):
            return text.lower() == "true"
        if isinstance(existing, (int, float)) and not isinstance(existing, bool):
            try:
                number = float(text)
                return int(number) if number.is_integer() else number
            except ValueError:
                pass
        return text
    text = "" if raw is None else str(raw)
    if param.required and not text.strip():
        raise ValueError(f"{param.name}: required.")
    return text


# --------------------------------------------------------------------------- remote files (not bundled)

def cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "Macro-Generator" / "cache"


def _verify(ref: RemoteRef, raw: bytes) -> str:
    if len(raw) > MAX_REMOTE_BYTES:
        raise RemoteError(f"{ref.url}: file is larger than {MAX_REMOTE_BYTES // 1024} KB.")
    if hashlib.sha256(raw).hexdigest() != ref.sha256:
        raise RemoteError(f"{ref.url}: content does not match the version that was reviewed (SHA-256 mismatch).")
    try:
        return raw.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise RemoteError(f"{ref.url}: not valid UTF-8 text.") from None


def cached_remote(ref: RemoteRef, directory: Path | None = None) -> str | None:
    path = (directory or cache_dir()) / ref.sha256
    try:
        return _verify(ref, path.read_bytes())
    except (OSError, RemoteError):
        return None


def download_remote(ref: RemoteRef, directory: Path | None = None) -> str:
    """Fetch a pinned file (HTTPS, allow-listed host, size-capped, hash-checked) and cache it."""
    parsed = urlparse(ref.url)
    if parsed.scheme != "https" or parsed.hostname not in REMOTE_HOSTS:
        raise RemoteError(f"Refusing to download from {parsed.hostname or ref.url}.")
    request = urllib.request.Request(ref.url, headers={"User-Agent": "Macro-Generator"})
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            raw = reply.read(MAX_REMOTE_BYTES + 1)
    except OSError as exc:
        raise RemoteError(f"Could not download {ref.url}: {exc}") from None
    text = _verify(ref, raw)
    target = directory or cache_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / ref.sha256).write_bytes(raw)
    except OSError:
        pass   # caching is an optimisation only
    return text


# --------------------------------------------------------------------------- bundle

@dataclass
class BundleMacro:
    name: str
    code: str
    activate: bool


@dataclass
class BundlePanel:
    panel_id: str
    xml: str


@dataclass
class Bundle:
    macros: list[BundleMacro] = field(default_factory=list)
    panels: list[BundlePanel] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>\s*")
_RELATIVE_IMPORT = re.compile(r"^[ \t]*import\s[^;\n]*?from\s+['\"]\./([^'\"]+)['\"]", re.M)


def prepare_panel(xml_text: str, fallback_id: str) -> tuple[str, str]:
    """Return (panel_id, xml). Adds a <PanelId> when the file has none (Panel.Save needs one)."""
    xml = _XML_DECL.sub("", xml_text.lstrip("\ufeff")).strip()
    root = ET.fromstring(xml)                       # ET.ParseError propagates
    panel = root.find("Panel")
    if panel is None:
        raise ValueError("no <Panel> element")
    pid = (panel.findtext("PanelId") or "").strip()
    if pid:
        return pid, xml
    element = ET.Element("PanelId")
    element.text = fallback_id
    order = panel.find("Order")
    panel.insert(list(panel).index(order) + 1 if order is not None else 0, element)
    return fallback_id, ET.tostring(root, encoding="unicode")


class SampleSession:
    """Editable state for one sample: file texts, which files are selected, and the settings values."""

    def __init__(self, sample: Sample, use_cache: bool = True):
        self.sample = sample
        self.original: dict[str, str] = {}
        self.text: dict[str, str] = {}
        self.selected: dict[str, bool] = {}
        self.params: dict[str, Any] = {}
        self.param_errors: dict[str, str] = {}

        for m in sample.macros:
            self._register(m.key, m.file, m.remote, use_cache)
            self.selected[m.key] = m.kind != "example"
        for p in sample.panels:
            self._register(p.key, p.file, p.remote, use_cache)
            self.selected[p.key] = not p.optional
        for rel in sample.extras:
            self.original[f"extra:{rel}"] = self.text[f"extra:{rel}"] = read_data_file(sample, rel)
        self._init_params()

    # -- loading
    def _register(self, key: str, file: str | None, remote: RemoteRef | None, use_cache: bool):
        text = None
        if file:
            text = read_data_file(self.sample, file)
        elif remote and use_cache:
            text = cached_remote(remote)
        if text is not None:
            self.original[key] = self.text[key] = text

    def missing_remote(self) -> list[str]:
        out = []
        for e in [*self.sample.macros, *self.sample.panels]:
            if e.remote and e.key not in self.text:
                out.append(e.key)
        return out

    def load_remote(self, fetch: Callable[[RemoteRef], str] = download_remote) -> None:
        for e in [*self.sample.macros, *self.sample.panels]:
            if e.remote and e.key not in self.text:
                text = fetch(e.remote)
                self.original[e.key] = self.text[e.key] = text
        self._init_params()

    # -- text access
    def keys(self) -> list[str]:
        return [m.key for m in self.sample.macros] + [p.key for p in self.sample.panels] + [f"extra:{x}" for x in self.sample.extras]

    def label(self, key: str) -> str:
        kind, _, rest = key.partition(":")
        return {"macro": f"{rest}  (macro)", "panel": f"{Path(rest).name}  (UI panel)", "extra": f"{rest}  (extra)"}[kind]

    def get(self, key: str) -> str:
        return self.text.get(key, "")

    def set_text(self, key: str, text: str) -> None:
        if key in self.text:
            self.text[key] = text

    def reset(self, key: str) -> None:
        if key in self.original:
            self.text[key] = self.original[key]
            self._init_params()

    def modified(self, key: str) -> bool:
        return key in self.original and self.text[key] != self.original[key]

    # -- settings
    def _macro_key(self, param: Param) -> str | None:
        return f"macro:{param.macro}" if param.macro else None

    def param_editable(self, param: Param) -> bool:
        key = self._macro_key(param)
        return bool(key and key in self.text and read_param(self.text[key], param.id)[0])

    def _init_params(self) -> None:
        for p in self.sample.params:
            key = self._macro_key(p)
            if not key or key not in self.text:
                continue
            found, current = read_param(self.text[key], p.id)
            if not found:
                continue
            if p.id not in self.params:
                self.params[p.id] = p.default if p.default is not None else current
            self._push_param(p, self.params[p.id])

    def _push_param(self, p: Param, value: Any) -> None:
        key = self._macro_key(p)
        try:
            self.text[key] = apply_param(self.text[key], p.id, value)
        except KeyError:
            pass

    def set_param(self, param_id: str, raw: Any) -> str:
        """Apply a form value to the macro source. Returns '' or a message for the user."""
        p = next(x for x in self.sample.params if x.id == param_id)
        key = self._macro_key(p)
        if not key or key not in self.text:
            return "This file has not been downloaded yet."
        found, existing = read_param(self.text[key], param_id)
        if not found:
            return "Not a simple value in the code - edit the macro directly."
        try:
            value = coerce_param(p, raw, existing)
        except ValueError as exc:
            self.param_errors[param_id] = str(exc)
            return str(exc)
        self.param_errors.pop(param_id, None)
        self.params[param_id] = value
        self._push_param(p, value)
        return ""

    # -- bundle
    def bundle(self) -> Bundle:
        b = Bundle()
        if self.param_errors:
            b.errors.extend(self.param_errors.values())
        names: list[str] = []
        for m in self.sample.macros:
            if not self.selected.get(m.key):
                continue
            if m.key not in self.text:
                b.errors.append(f"'{m.name}' has to be downloaded first (it is not bundled).")
                continue
            if m.name in names:
                b.errors.append(f"Two selected macros are both called '{m.name}'.")
                continue
            names.append(m.name)
            b.macros.append(BundleMacro(m.name, self.text[m.key], activate=m.kind != "library"))

        seen_ids: dict[str, str] = {}
        for p in self.sample.panels:
            if not self.selected.get(p.key):
                continue
            if p.key not in self.text:
                b.errors.append(f"Panel '{p.panel_id}' has to be downloaded first (it is not bundled).")
                continue
            label = Path(p.file).name if p.file else p.panel_id
            try:
                pid, xml = prepare_panel(self.text[p.key], p.panel_id)
            except (ET.ParseError, ValueError) as exc:
                b.errors.append(f"Panel {label}: {exc}")
                continue
            if pid in seen_ids:
                b.errors.append(f"Panels {seen_ids[pid]} and {label} share the id '{pid}' - tick only one of them.")
                continue
            seen_ids[pid] = label
            b.panels.append(BundlePanel(pid, xml))

        if not b.macros and not b.panels and not b.errors:
            b.errors.append("Nothing is selected.")
        active = sum(1 for m in b.macros if m.activate)
        if active > MAX_ACTIVE_MACROS:
            b.errors.append(f"{active} macros would be active; RoomOS allows at most {MAX_ACTIVE_MACROS}.")
        for m in b.macros:
            for dep in dict.fromkeys(_RELATIVE_IMPORT.findall(m.code)):
                if dep not in names:
                    b.warnings.append(f"'{m.name}' imports './{dep}', which is not selected - it must already be on the device.")
        return b

    # -- profile (settings + selection + hand edits)
    def to_profile(self) -> dict[str, Any]:
        return {
            "template": SAMPLE_PREFIX + self.sample.id,
            "values": dict(self.params),
            "selected": [k for k, v in self.selected.items() if v],
            "edits": {k: self.text[k] for k in self.keys() if self.modified(k) and not self._only_param_changes(k)},
        }

    def _only_param_changes(self, key: str) -> bool:
        """True when the file differs from upstream only by the settings form (they are stored as `values`)."""
        text = self.original.get(key, "")
        for p in self.sample.params:
            if self._macro_key(p) == key and p.id in self.params:
                try:
                    text = apply_param(text, p.id, self.params[p.id])
                except KeyError:
                    pass
        return text == self.text.get(key)

    def apply_profile(self, data: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        known = set(self.keys())
        if "selected" in data:
            wanted = set(data["selected"])
            for k in self.selected:
                self.selected[k] = k in wanted
        for k, text in data.get("edits", {}).items():
            if k in known:
                self.text[k] = text
            else:
                problems.append(f"profile edits unknown file '{k}'")
        for pid, value in data.get("values", {}).items():
            if not any(p.id == pid for p in self.sample.params):
                problems.append(f"profile has unknown setting '{pid}'")
                continue
            msg = self.set_param(pid, value)
            if msg:
                problems.append(f"{pid}: {msg}")
        return problems

    # -- export
    def export_files(self, bundle: Bundle) -> dict[str, str]:
        """Relative filename -> text for everything in the bundle, for manual upload."""
        files = {f"{m.name}.js": m.code for m in bundle.macros}
        files.update({f"{p.panel_id}.xml": p.xml for p in bundle.panels})
        if self.sample.readme:
            files["README.md"] = read_data_file(self.sample, self.sample.readme)
        return files
