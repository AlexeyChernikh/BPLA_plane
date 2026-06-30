"""UAV mission planner application."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def _configure_bundled_proj() -> None:
    """Prefer Rasterio's compatible PROJ database over unrelated PostGIS installs."""
    specification = importlib.util.find_spec("rasterio")
    if specification is None or specification.origin is None:
        return
    proj_data = Path(specification.origin).parent / "proj_data"
    if proj_data.is_dir():
        os.environ["PROJ_LIB"] = str(proj_data)
        os.environ["PROJ_DATA"] = str(proj_data)


_configure_bundled_proj()
