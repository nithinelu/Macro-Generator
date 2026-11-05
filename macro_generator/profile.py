"""Saved settings for one generated macro, so a customer's setup can be reloaded and re-generated."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import Template, coerce, render, validate, validate_macro_name
from .templates import BY_ID


def save_profile(path: str | Path, template: Template, macro_name: str, values: dict[str, Any]) -> None:
    data = {"template": template.id, "macro_name": macro_name, "values": values}
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_profile(path: str | Path) -> tuple[Template, str, dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))   # -sig: tolerate the BOM Windows editors add
    template = BY_ID.get(data.get("template"))
    if template is None:
        raise ValueError(f"Unknown template '{data.get('template')}'. Known: {', '.join(BY_ID)}")
    merged = template.defaults()
    merged.update(data.get("values", {}))
    return template, data.get("macro_name") or template.default_macro_name, merged


def generate(template: Template, macro_name: str, raw_values: dict[str, Any]) -> tuple[str, list[str]]:
    """Return (js, errors). js is empty when there are errors."""
    values = coerce(template, raw_values)
    errors = validate_macro_name(macro_name) + validate(template, values)
    if errors:
        return "", errors
    return render(template, values), []
