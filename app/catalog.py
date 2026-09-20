"""Pattern catalog: each pattern is a Terraform root module in its own git repo, versioned by tag.

No Terraform code lives in this API. `patterns.yaml` maps a pattern name to a repo; versions are
the repo's semver tags; the input schema is read from the pattern's own `variable` blocks at the
resolved commit.
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hcl2
import yaml

from app.settings import settings

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class CatalogError(Exception):
    """The catalog, the pattern repo or the requested version could not be used."""


class UnknownPattern(CatalogError):
    pass


class UnknownVersion(CatalogError):
    pass


@dataclass(frozen=True)
class Pattern:
    name: str
    repo: str | None = None  # e.g. github.com/org/terraform-pattern-x
    path: str = ""  # sub-directory holding the root module, if not the repo root
    default_version: str | None = None  # tag used when a request names none; else newest tag
    local: str | None = None  # unversioned local directory; examples and tests only

    @property
    def git_url(self) -> str:
        return self.repo if "://" in self.repo else f"https://{self.repo}.git"


@dataclass(frozen=True)
class Resolved:
    pattern: Pattern
    version: str | None
    commit: str | None

    @property
    def terraform_source(self) -> str:
        if self.pattern.local:
            return str(Path(self.pattern.local).resolve())
        subdir = f"//{self.pattern.path}" if self.pattern.path else ""
        return f"git::{self.pattern.git_url}{subdir}?ref={self.commit}"


def git_env() -> dict[str, str]:
    """Environment for git (and Terraform's git fetches). The token never touches disk or argv."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if settings.github_token:
        env |= {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"url.https://x-access-token:{settings.github_token}@github.com/"
            ".insteadOf",
            "GIT_CONFIG_VALUE_0": "https://github.com/",
        }
    return env


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=git_env(), capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        # stderr can contain the remote URL; never the token (it is injected via config env).
        raise CatalogError(f"git {args[0]} failed for the pattern repository")
    return result.stdout


def load() -> dict[str, Pattern]:
    raw = yaml.safe_load(settings.catalog_path.read_text()) or {}
    return {name: Pattern(name=name, **spec) for name, spec in (raw.get("patterns") or {}).items()}


def get(name: str) -> Pattern:
    try:
        return load()[name]
    except KeyError:
        raise UnknownPattern(name) from None


def versions(pattern: Pattern) -> dict[str, str]:
    """Semver tags of the pattern repo, newest first, mapped to the commit each points at."""
    if pattern.local:
        return {}
    found: dict[str, str] = {}
    for line in _git("ls-remote", "--tags", pattern.git_url).splitlines():
        sha, ref = line.split("\t")
        tag = ref.removeprefix("refs/tags/")
        # An annotated tag lists twice; the `^{}` line is the commit it points at.
        if tag.endswith("^{}"):
            found[tag.removesuffix("^{}")] = sha
        elif _SEMVER.match(tag):
            found.setdefault(tag, sha)
    ordered = sorted(found, key=lambda t: tuple(map(int, _SEMVER.match(t).groups())), reverse=True)
    return {tag: found[tag] for tag in ordered}


def resolve(name: str, version: str | None) -> Resolved:
    pattern = get(name)
    if pattern.local:
        return Resolved(pattern, None, None)
    available = versions(pattern)
    if not available:
        raise UnknownVersion(f"{name} has no version tags")
    if version is None:
        version = pattern.default_version or next(iter(available))
    if version not in available:
        raise UnknownVersion(f"{name} has no version {version}; choose one of {list(available)}")
    return Resolved(pattern, version, available[version])


def _checkout(resolved: Resolved) -> Path:
    """Root-module directory at the resolved commit. Cached by commit, so it cannot go stale."""
    pattern = resolved.pattern
    if pattern.local:
        return Path(pattern.local)
    cache = settings.data_dir.resolve() / "pattern-cache" / pattern.name / resolved.commit
    if not (cache / ".git").exists():
        cache.mkdir(parents=True, exist_ok=True)
        _git("init", "-q", cwd=cache)
        _git("fetch", "-q", "--depth=1", pattern.git_url, resolved.commit, cwd=cache)
        _git("checkout", "-q", "FETCH_HEAD", cwd=cache)
    return cache / pattern.path


def _unquote(value: Any) -> Any:
    return value.strip('"') if isinstance(value, str) else value


def variables(resolved: Resolved) -> list[dict[str, Any]]:
    """The pattern's `variable` blocks: name, type, required, default, description."""
    found = []
    for tf_file in sorted(_checkout(resolved).glob("*.tf")):
        for block in hcl2.loads(tf_file.read_text()).get("variable", []):
            for name, body in block.items():
                tf_type = str(body.get("type", "any")).removeprefix("${").removesuffix("}")
                default = body.get("default")
                if isinstance(default, str):
                    try:
                        default = json.loads(default)
                    except ValueError:
                        default = _unquote(default)
                found.append(
                    {
                        "name": _unquote(name),
                        "type": tf_type,
                        "required": "default" not in body,
                        "default": default,
                        "description": _unquote(body.get("description", "")),
                        "sensitive": body.get("sensitive") is True,
                        "validations": [
                            {
                                "condition": str(v.get("condition", "")),
                                "error_message": _unquote(v.get("error_message", "")),
                            }
                            for v in body.get("validation", [])
                        ],
                    }
                )
    return found


def config(resolved: Resolved) -> dict[str, Any] | None:
    """The pattern repo's own `config.yaml` (next to the root module, else at the repo root)."""
    root = _checkout(resolved)
    for folder in dict.fromkeys([root, *root.parents[: len(Path(resolved.pattern.path).parts)]]):
        if (folder / "config.yaml").is_file():
            return yaml.safe_load((folder / "config.yaml").read_text())
    return None


def uses_azurerm_backend(workdir: Path) -> bool:
    for tf_file in workdir.glob("*.tf"):
        for block in hcl2.loads(tf_file.read_text()).get("terraform", []):
            if any('"azurerm"' in b or "azurerm" in b for b in block.get("backend", [])):
                return True
    return False
