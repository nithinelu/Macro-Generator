"""Application metadata (single source of truth)."""
from __future__ import annotations

APP_NAME = "Macro Generator"
VERSION = "1.0.0"
AUTHOR = "Nithin Eluvathingal"
YEAR = "2026"
LICENSE = "MIT License"
COPYRIGHT = f"© {YEAR} {AUTHOR}"
TAGLINE = "Build, customise and deploy Cisco RoomOS macros"

ABOUT_TEXT = (
    f"Version {VERSION}\n"
    f"by {AUTHOR}\n\n"
    "Builds Cisco RoomOS macros from templates and installs Cisco's sample macros.\n\n"
    f"{LICENSE}\n{COPYRIGHT}\n\n"
    "The bundled Cisco sample macros keep their own MIT licence from Cisco "
    "(macro_generator/samples_data/LICENSE)."
)
