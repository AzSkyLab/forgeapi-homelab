"""The work deployment brief is what the work environment is built from. It must not name
settings or endpoints that do not exist, and must account for every setting that does."""

import re
from pathlib import Path

from app.main import app
from app.settings import Settings

BRIEF = (Path(__file__).resolve().parent.parent / "docs" / "work-deployment.md").read_text()


def test_every_setting_the_brief_names_exists():
    real = {f"FORGEAPI_{name.upper()}" for name in Settings.model_fields}
    assert set(re.findall(r"FORGEAPI_[A-Z_]+", BRIEF)) <= real


def test_every_setting_is_accounted_for_in_the_brief():
    """A new setting must be documented there: set it, do not set it, or leave the default."""
    real = {f"FORGEAPI_{name.upper()}" for name in Settings.model_fields}
    assert real - set(re.findall(r"FORGEAPI_[A-Z_]+", BRIEF)) == set()


def test_every_endpoint_is_in_the_brief():
    paths = {
        route.path
        for route in app.routes
        if getattr(route, "methods", None)
        and not route.path.startswith(("/docs", "/openapi", "/redoc"))
    }
    written = BRIEF.replace("{id}", "{deployment_id}").replace("<id>", "{deployment_id}")
    missing = {path for path in paths if path not in written}
    assert missing == set()
