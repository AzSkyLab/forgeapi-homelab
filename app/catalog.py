"""Pattern catalog: each pattern is a Terraform root module in its own git repo, versioned by tag.

No Terraform code lives in this API. `patterns.yaml` maps a pattern name to a repo; versions are
the repo's semver tags; the input schema is read from the pattern's own `variable` blocks at the
resolved commit.
"""

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hcl2
import yaml
from lark.exceptions import LarkError

from app.settings import settings

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
GIT_TIMEOUT = 60
GIT_DRAIN_TIMEOUT = 5


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
    cloud: str | None = None  # platform-owned placement selector; never caller-supplied

    def __post_init__(self):
        if self.cloud not in (None, "azure", "aws", "gcp"):
            raise CatalogError("unsupported pattern cloud")

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


def git_env(token: bool = True) -> dict[str, str]:
    """Environment for git (and Terraform's git fetches). FORGEAPI_* never reaches a child
    process (secrets arrive that way per settings.py); the git token never touches disk or argv,
    and is added only when requested, so plan/apply do not mint one on every call."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("FORGEAPI_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    if not token:
        return env
    from app import github_app

    value = github_app.installation_token() if github_app.configured() else settings.github_token
    if value:
        host = settings.github_host
        env |= {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"url.https://x-access-token:{value}@{host}/.insteadOf",
            "GIT_CONFIG_VALUE_0": f"https://{host}/",
        }
    return env


def _git(*args: str, cwd: Path | None = None, timeout: float | None = None) -> str:
    env = git_env()
    try:
        process = subprocess.Popen(
            ["git", *args],
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except OSError:
        raise CatalogError("unable to start git for the pattern repository") from None
    try:
        output, _stderr = process.communicate(timeout=timeout or GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        # The leader may have exited while a child still holds a captured pipe open.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        drain_deadline = time.monotonic() + GIT_DRAIN_TIMEOUT
        try:
            process.communicate(timeout=GIT_DRAIN_TIMEOUT)
        except subprocess.TimeoutExpired:
            for pipe in (process.stdout, process.stderr):
                if pipe:
                    pipe.close()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=max(0, drain_deadline - time.monotonic()))
        raise CatalogError("git command timed out for the pattern repository") from None
    if process.returncode != 0:
        # stderr can contain the remote URL; never the token (it is injected via config env).
        raise CatalogError(f"git {args[0]} failed for the pattern repository")
    return output


def load() -> dict[str, Pattern]:
    raw = yaml.safe_load(settings.catalog_path.read_text()) or {}
    return {name: _entry(name, spec) for name, spec in (raw.get("patterns") or {}).items()}


def _entry(name: str, spec: Any) -> Pattern:
    known = set(Pattern.__dataclass_fields__) - {"name"}
    if not isinstance(spec, dict):
        raise CatalogError(f"catalog entry {name} must be a mapping")
    if unknown := sorted(map(str, set(spec) - known)):
        raise CatalogError(f"catalog entry {name} has unknown key(s): {', '.join(unknown)}")
    return Pattern(name=name, **spec)


def get(name: str) -> Pattern:
    try:
        return load()[name]
    except KeyError:
        raise UnknownPattern(name) from None


def versions(pattern: Pattern, timeout: float | None = None) -> dict[str, str]:
    """Semver tags of the pattern repo, newest first, mapped to the commit each points at."""
    if pattern.local:
        return {}
    found: dict[str, str] = {}
    for line in _git("ls-remote", "--tags", pattern.git_url, timeout=timeout).splitlines():
        sha, ref = line.split("\t")
        tag = ref.removeprefix("refs/tags/")
        # An annotated tag lists twice; the `^{}` line is the commit it points at.
        peeled = tag.endswith("^{}")
        tag = tag.removesuffix("^{}")
        if not _SEMVER.match(tag):
            continue
        if peeled:
            found[tag] = sha
        else:
            found.setdefault(tag, sha)
    ordered = sorted(found, key=lambda t: tuple(map(int, _SEMVER.match(t).groups())), reverse=True)
    return {tag: found[tag] for tag in ordered}


UPGRADE_GIT_TIMEOUT = 5  # seconds; upgrade hints must never make a resource read slow
# ponytail: per-process dict cache of raw tags per repo URL. Ceiling: each replica lists tags
# itself and may lag another by one TTL; move to a shared cache if that matters. It is used
# only for the upgrade hint: resolve() always lists live so a pinned commit is never stale.
_latest_cache: dict[str, tuple[float, str | None]] = {}


def latest_version(pattern: Pattern) -> str | None:
    """Newest semver tag, cached for `version_cache_seconds`; None when unknown or git fails.

    Caches raw per-repo results only; callers apply their own allowed-pattern check first."""
    if pattern.local:
        return None
    ttl, now, key = settings.version_cache_seconds, time.monotonic(), pattern.git_url
    hit = _latest_cache.get(key)
    if ttl > 0 and hit and now - hit[0] < ttl:
        return hit[1]
    try:
        found = next(iter(versions(pattern, timeout=UPGRADE_GIT_TIMEOUT)), None)
    except CatalogError:
        found = None  # degrade; failures are cached too so a down repo costs one wait per TTL
    if ttl > 0:
        _latest_cache[key] = (now, found)
    return found


def _key(tag: str) -> tuple[int, ...]:
    return tuple(map(int, _SEMVER.match(tag).groups()))


def upgrade_available(pattern_name: str, applied: str | None, allowed: set[str]) -> tuple:
    """(latest_version, upgrade_available), both None when unknown or not usable by caller."""
    if not _SEMVER.match(applied or "") or pattern_name not in allowed:
        return None, None
    try:
        latest = latest_version(get(pattern_name))
    except CatalogError:
        return None, None
    if latest is None:
        return None, None
    return latest, _key(latest) > _key(applied)


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
    cache.parent.mkdir(parents=True, exist_ok=True)
    with (cache.parent / f"{resolved.commit}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ready = cache / ".forgeapi-ready"
        if not ready.exists():
            cache.mkdir(parents=True, exist_ok=True)
            _git("init", "-q", cwd=cache)
            _git("fetch", "-q", "--depth=1", pattern.git_url, resolved.commit, cwd=cache)
            _git("checkout", "-q", "FETCH_HEAD", cwd=cache)
            ready.touch()
    return cache / pattern.path


def _unquote(value: Any) -> Any:
    return value.strip('"') if isinstance(value, str) else value


def contained(path: Path, root: Path) -> bool:
    """True for a file that is not a symlink and resolves inside `root`. A pattern repo is
    untrusted: a symlinked `*.tf` or `config.yaml` must not read files outside its checkout."""
    try:
        return not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _hcl_file(path: Path) -> dict:
    try:
        return hcl2.loads(path.read_text())
    except (LarkError, UnicodeError):
        raise CatalogError("invalid pattern source") from None


def variables(resolved: Resolved) -> list[dict[str, Any]]:
    """The pattern's `variable` blocks: name, type, required, default, description."""
    root = _checkout(resolved)
    return _parse_variables(
        _hcl_file(tf_file) for tf_file in sorted(root.glob("*.tf")) if contained(tf_file, root)
    )


def _parse_variables(documents) -> list[dict[str, Any]]:
    found = []
    for document in documents:
        for block in document.get("variable", []):
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


CHANGES_GIT_TIMEOUT = 20  # seconds per git call; changelogs must stay bounded
CHANGES_MAX_COMMITS = 100


def _usable_repo(repo: Path) -> bool:
    try:
        _git("--git-dir", str(repo), "rev-parse", "--git-dir", timeout=CHANGES_GIT_TIMEOUT)
    except CatalogError:
        return False
    return True


def changes(pattern: Pattern, older: str, newer: str) -> dict[str, Any]:
    """Commits and parsed input variables between two existing tags (raw, for the caller to
    filter and summarise). Raises UnknownVersion for a missing tag or a reversed range."""
    tags = versions(pattern, timeout=CHANGES_GIT_TIMEOUT)
    for tag in (older, newer):
        if tag not in tags:
            raise UnknownVersion(f"{pattern.name} has no version {tag}")
    if _key(older) >= _key(newer):
        raise UnknownVersion("from must be an older version than to")
    commits = {older: tags[older], newer: tags[newer]}
    cache = settings.data_dir.resolve() / "pattern-cache" / pattern.name
    cache.mkdir(parents=True, exist_ok=True)
    repo = cache / "history.git"
    with (cache / "history.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if repo.exists() and not _usable_repo(repo):
            shutil.rmtree(repo, ignore_errors=True)  # half-initialised by an interrupted run
        if not repo.exists():
            # Build beside it and rename into place, so a crash never leaves a half repo.
            scratch = Path(tempfile.mkdtemp(prefix="history-", dir=cache))
            try:
                _git("init", "-q", "--bare", str(scratch), timeout=CHANGES_GIT_TIMEOUT)
                scratch.rename(repo)
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
        try:
            for sha in commits.values():
                _git("cat-file", "-e", f"{sha}^{{commit}}", cwd=repo, timeout=CHANGES_GIT_TIMEOUT)
        except CatalogError:
            _git(
                "fetch",
                "-q",
                "--no-write-fetch-head",
                pattern.git_url,
                "+refs/tags/*:refs/tags/*",
                cwd=repo,
                timeout=CHANGES_GIT_TIMEOUT,
            )
    log = _git(
        "log",
        f"--max-count={CHANGES_MAX_COMMITS}",
        "--format=%H%x1f%s",
        f"{commits[older]}..{commits[newer]}",
        cwd=repo,
        timeout=CHANGES_GIT_TIMEOUT,
    )
    entries = []
    for line in log.splitlines():
        sha, _, subject = line.partition("\x1f")
        entries.append({"commit": sha, "subject": subject})
    parsed = {}
    for tag, sha in commits.items():
        base = f"{sha}:{pattern.path}" if pattern.path else sha
        names = _git(
            "ls-tree", "--name-only", base, cwd=repo, timeout=CHANGES_GIT_TIMEOUT
        ).splitlines()
        documents = []
        for file in sorted(n for n in names if n.endswith(".tf")):
            prefix = f"{pattern.path.strip('/')}/" if pattern.path else ""
            text = _git("show", f"{sha}:{prefix}{file}", cwd=repo, timeout=CHANGES_GIT_TIMEOUT)
            try:
                documents.append(hcl2.loads(text))
            except (LarkError, UnicodeError):
                raise CatalogError("invalid pattern source") from None
        parsed[tag] = _parse_variables(documents)
    return {
        "from_commit": commits[older],
        "to_commit": commits[newer],
        "commits": entries,
        "from_variables": parsed[older],
        "to_variables": parsed[newer],
    }


def config(resolved: Resolved) -> dict[str, Any] | None:
    """The pattern repo's own `config.yaml` (next to the root module, else at the repo root)."""
    root = _checkout(resolved)
    folders = list(dict.fromkeys([root, *root.parents[: len(Path(resolved.pattern.path).parts)]]))
    for folder in folders:
        if (folder / "config.yaml").is_file() or (folder / "config.yaml").is_symlink():
            if not contained(folder / "config.yaml", folders[-1]):
                raise CatalogError("invalid pattern config")
            try:
                config = yaml.safe_load((folder / "config.yaml").read_text())
            except (yaml.YAMLError, UnicodeError):
                raise CatalogError("invalid pattern config") from None
            if config is not None and not isinstance(config, dict):
                raise CatalogError("invalid pattern config")
            return config
    return None


def uses_azurerm_backend(workdir: Path) -> bool:
    for tf_file in workdir.glob("*.tf"):
        if not contained(tf_file, workdir):
            continue
        for block in _hcl_file(tf_file).get("terraform", []):
            if any('"azurerm"' in b or "azurerm" in b for b in block.get("backend", [])):
                return True
    return False
