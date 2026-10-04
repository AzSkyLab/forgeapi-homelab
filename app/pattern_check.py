"""Pattern contract checker: will this Terraform root module work as a ForgeAPI pattern?

`check(root)` is pure static analysis of a pattern directory (nothing is run unless
`terraform=True`). Errors would be refused or break at runtime; warnings are risky; info is FYI.
`check_catalog(path)` checks `patterns.yaml` entries. Run it as a CLI:

    python -m app.pattern_check <dir | git-url> [--ref TAG] [--path SUBDIR] [--cloud aws|azure|gcp]
        [--terraform] [--json]
    python -m app.pattern_check --catalog patterns.yaml

Parsing is reused from `app.catalog` and `app.schema`; no FORGEAPI settings, cloud or credentials
are needed for a local directory.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import hcl2
import yaml
from lark.exceptions import LarkError

from app import budgets, catalog, schema
from app.cloud_targets import IDENTIFIERS
from app.tenants import TenancyError

PLATFORM_TERRAFORM = "1.16.5"
TERRAFORM_TIMEOUT = 300
CLOUDS = ("azure", "aws", "gcp")
PROVIDER_CLOUD = {"azurerm": "azure", "aws": "aws", "google": "gcp"}
PLACEMENT_VARIABLES = tuple(IDENTIFIERS.values())  # subscription_id, aws_account_id, project_id
CONFIG_KEYS = set(schema._ABOUT_KEYS)
SECRET_NAME = re.compile(r"password|secret|key|token|connection_string", re.I)
BACKEND_HARDCODED = ("storage_account_name", "container_name", "key", "resource_group_name")
BUILTIN_PROVIDERS = {"terraform"}


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warning" | "info"
    code: str
    message: str
    file: str | None = None
    line: int | None = None


def _unquote(value: Any) -> Any:
    return catalog._unquote(value)


def _blocks(document: dict, kind: str):
    """(label parts..., body) for every block of one kind; labels unquoted."""
    for block in document.get(kind, []):
        for first, rest in block.items():
            if first == "__is_block__":
                continue
            yield first, rest


def _flat(value: Any) -> str:
    return json.dumps(value, default=str)


def _refs(value: Any, name: str) -> bool:
    return re.search(rf"\bvar\.{re.escape(name)}\b", _flat(value)) is not None


def _symlinked(file: str) -> Finding:
    return Finding(
        "error",
        "symlinked_file",
        "file is a symlink or resolves outside the module; it is not read",
        file,
    )


class _Module:
    """Parsed root module: per-file documents and text, for line lookups."""

    def __init__(self, root: Path):
        self.root = root
        self.docs: dict[str, dict] = {}
        self.text: dict[str, str] = {}
        self.findings: list[Finding] = []
        for path in sorted(root.glob("*.tf")):
            if catalog.contained(path, root):
                self._parse(path, path.name)
            else:
                self.findings.append(_symlinked(path.name))
        self.nested = [
            p
            for p in sorted(root.rglob("*.tf"))
            if p.parent != root
            and ".terraform" not in p.relative_to(root).parts
            and catalog.contained(p, root)
        ]

    def _parse(self, path: Path, label: str) -> dict | None:
        try:
            text = path.read_text()
        except (OSError, UnicodeError):
            self.findings.append(Finding("error", "hcl_parse", "file is not readable text", label))
            return None
        try:
            document = catalog._hcl_file(path)
        except catalog.CatalogError:
            line = None
            try:
                hcl2.loads(text)
            except LarkError as error:
                line = getattr(error, "line", None)
            except Exception:  # noqa: BLE001 - any parser failure is a finding, not a crash
                pass
            self.findings.append(
                Finding("error", "hcl_parse", "file does not parse as HCL", label, line)
            )
            return None
        self.docs[label] = document
        self.text[label] = text
        return document

    def line(self, file: str, *patterns: str) -> int | None:
        """First line matching the first pattern (then later ones, in order after it)."""
        lines = self.text.get(file, "").splitlines()
        found = None
        for pattern in patterns:
            start = found or 0
            for number in range(start, len(lines)):
                if re.search(pattern, lines[number]):
                    found = number + 1
                    break
            else:
                return found
        return found

    def all(self, kind: str):
        for file, document in self.docs.items():
            for first, rest in _blocks(document, kind):
                yield file, first, rest


def _version_tuple(text: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-[\w.]+)?", text.strip())
    if not match:
        return None
    return tuple(int(g) for g in match.groups(default="0"))


def allows(constraint: str, version: str) -> bool | None:
    """Whether a Terraform version constraint admits `version`; None when it cannot be read."""
    target = _version_tuple(version)
    if target is None:
        return None
    for part in (p.strip() for p in constraint.split(",")):
        match = re.fullmatch(r"(=|!=|>=|<=|>|<|~>)?\s*(.+)", part)
        wanted = _version_tuple(match.group(2)) if match else None
        if not match or wanted is None:
            return None
        op = match.group(1) or "="
        if op == "~>":
            given = len(match.group(2).strip().removeprefix("v").split("-")[0].split("."))
            upper = list(wanted[: max(given - 1, 1)])
            upper[-1] += 1
            ok = target >= wanted and target < tuple(upper) + (0,) * (3 - len(upper))
        else:
            ok = {
                "=": target == wanted,
                "!=": target != wanted,
                ">=": target >= wanted,
                "<=": target <= wanted,
                ">": target > wanted,
                "<": target < wanted,
            }[op]
        if not ok:
            return False
    return True


# --- rule groups -----------------------------------------------------------------------------


def _terraform_block(module: _Module, out: list[Finding], platform: str) -> set[str]:
    """Checks `terraform {}`; returns the declared provider names."""
    declared: set[str] = set()
    versions: list[tuple[str, str, int | None]] = []
    for file, document in module.docs.items():
        for block in document.get("terraform", []):
            if "required_version" in block:
                versions.append(
                    (
                        file,
                        str(_unquote(block["required_version"])),
                        module.line(file, r"required_version"),
                    )
                )
            for providers in block.get("required_providers", []):
                for name, spec in providers.items():
                    if name == "__is_block__":
                        continue
                    name = _unquote(name)
                    declared.add(name)
                    constraint = spec.get("version") if isinstance(spec, dict) else _unquote(spec)
                    if not constraint:
                        out.append(
                            Finding(
                                "warning",
                                "unpinned_provider",
                                f"provider {name} has no version constraint",
                                file,
                                module.line(file, rf"\b{re.escape(name)}\b\s*="),
                            )
                        )
    if not versions and module.docs:
        out.append(
            Finding("warning", "no_required_version", "terraform { required_version } is not set")
        )
    for file, constraint, line in versions:
        if allows(constraint, platform) is False:
            out.append(
                Finding(
                    "error",
                    "required_version_excludes_platform",
                    f"required_version {constraint!r} does not allow the platform's Terraform "
                    f"{platform}",
                    file,
                    line,
                )
            )
    return declared


def _used_providers(module: _Module) -> set[str]:
    used = set()
    for kind in ("resource", "data"):
        for _file, type_, _rest in module.all(kind):
            used.add(_unquote(type_).split("_")[0])
    for _file, name, _rest in module.all("provider"):
        used.add(_unquote(name))
    return used - BUILTIN_PROVIDERS


def _inputs(module: _Module, out: list[Finding]) -> list[dict]:
    variables = catalog._parse_variables(module.docs.values())
    files = {}
    for file, name, _body in module.all("variable"):
        files.setdefault(_unquote(name), file)
    for variable in variables:
        name = variable["name"]
        file = files.get(name)
        line = module.line(file, rf'variable\s+"?{re.escape(name)}"?') if file else None
        if variable["sensitive"]:
            out.append(
                Finding(
                    "error",
                    "sensitive_input",
                    f"variable {name} is sensitive; the API refuses patterns with sensitive "
                    "inputs. Accept a vault reference (a Key Vault secret id) instead of a value",
                    file,
                    line,
                )
            )
        if variable["type"] not in schema._TYPES:
            out.append(
                Finding(
                    "warning",
                    "unchecked_type",
                    f"variable {name} has type {variable['type']!r}, which the input schema "
                    "cannot check up front (Terraform still checks it at plan)",
                    file,
                    line,
                )
            )
        if not variable["description"]:
            out.append(
                Finding("info", "no_description", f"variable {name} has no description", file, line)
            )
        for validation in variable["validations"]:
            if not schema.lift(name, variable["type"], validation["condition"]):
                out.append(
                    Finding(
                        "info",
                        "validation_runtime_only",
                        f"validation on {name} is enforced at plan time only (not lifted into "
                        "the input schema)",
                        file,
                        line,
                    )
                )
    return variables


def _infer_cloud(declared: set[str], used: set[str]) -> str | None:
    clouds = {PROVIDER_CLOUD[p] for p in declared | used if p in PROVIDER_CLOUD}
    return next(iter(clouds)) if len(clouds) == 1 else None


def _placement(
    module: _Module, out: list[Finding], variables: list[dict], cloud: str, inferred: bool
) -> None:
    identifier = IDENTIFIERS[cloud]
    names = {v["name"] for v in variables}
    missing = [n for n in (identifier, "region") if n not in names]
    if missing:
        # Azure patterns without `cloud:` in the catalog use the older subscription-only
        # mapping, so an inferred azure cloud is only a warning.
        level = "warning" if inferred and cloud == "azure" else "error"
        suffix = " (needed once the catalog entry sets cloud: azure)" if level == "warning" else ""
        out.append(
            Finding(
                level,
                "missing_target_inputs",
                f"{cloud} patterns must declare variables {', '.join(missing)}: the platform "
                f"injects them from the environment target{suffix}",
            )
        )
    provider = {"azure": "azurerm", "aws": "aws", "gcp": "google"}[cloud]
    attr = {"azure": "subscription_id", "aws": "allowed_account_ids", "gcp": "project"}[cloud]
    blocks = [
        (file, body) for file, name, body in module.all("provider") if _unquote(name) == provider
    ]
    if identifier in names:
        if not blocks:
            out.append(
                Finding(
                    "warning",
                    "target_not_wired",
                    f"no provider {provider!r} block references var.{identifier}",
                )
            )
        for file, body in blocks:
            value = body.get(attr)
            literal = value is not None and "${" not in json.dumps(value)
            if literal:
                # A fixed account/subscription/project ignores the landing zone entirely.
                out.append(
                    Finding(
                        "error",
                        "hardcoded_placement",
                        f"provider {provider} hard-codes {attr}; it must come from "
                        f"var.{identifier} so the landing zone decides where it deploys",
                        file,
                        module.line(file, rf'provider\s+"{provider}"'),
                    )
                )
            elif not _refs(value, identifier):
                out.append(
                    Finding(
                        "warning",
                        "target_not_wired",
                        f"provider {provider} does not set {attr} from var.{identifier}",
                        file,
                        module.line(file, rf'provider\s+"{provider}"'),
                    )
                )
    if "region" in names:
        everything = [d for f, d in module.docs.items()]
        used = any(
            _refs({k: v for k, v in document.items() if k != "variable"}, "region")
            for document in everything
        )
        if not used:
            out.append(
                Finding("warning", "target_not_wired", "var.region is declared but never used")
            )
    if "location" in names and identifier in names:
        out.append(
            Finding(
                "info",
                "location_and_target",
                "a location variable is declared alongside cloud targets; region is the "
                "platform-injected input, location is caller-visible",
            )
        )


def _outputs(module: _Module, out: list[Finding]) -> None:
    for file, name, body in module.all("output"):
        name = _unquote(name)
        line = module.line(file, rf'output\s+"?{re.escape(name)}"?')
        sensitive = body.get("sensitive") is True
        if sensitive:
            out.append(
                Finding(
                    "info",
                    "sensitive_output",
                    f"output {name} is sensitive; it will be withheld (name only)",
                    file,
                    line,
                )
            )
        elif SECRET_NAME.search(name):
            out.append(
                Finding(
                    "warning",
                    "secret_like_output",
                    f"output {name} looks like a secret but is not marked sensitive; "
                    "store secrets in the pattern's Key Vault and output a reference",
                    file,
                    line,
                )
            )
        if any(_refs(body.get("value"), v) for v in PLACEMENT_VARIABLES):
            out.append(
                Finding(
                    "warning",
                    "output_reveals_placement",
                    f"output {name} echoes a placement variable; it will be withheld",
                    file,
                    line,
                )
            )


def _numbers(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _config(
    module: _Module, out: list[Finding], variables: list[dict], fallback: Path | None
) -> None:
    folder = next(
        (
            f
            for f in (module.root, fallback)
            if f and ((f / "config.yaml").is_file() or (f / "config.yaml").is_symlink())
        ),
        None,
    )
    if folder is not None and not catalog.contained(folder / "config.yaml", folder):
        out.append(_symlinked("config.yaml"))
        return
    if folder is None:
        out.append(
            Finding(
                "warning",
                "no_estimated_costs",
                "no config.yaml: estimated_costs are missing, so environments with a budget "
                "refuse this pattern",
            )
        )
        return
    try:
        raw = catalog.config(
            catalog.Resolved(catalog.Pattern(name="check", local=str(folder)), None, None)
        )
    except catalog.CatalogError:
        out.append(
            Finding("error", "config_invalid", "config.yaml is not a YAML mapping", "config.yaml")
        )
        return
    raw = raw or {}
    for key in sorted(set(raw) - CONFIG_KEYS):
        out.append(
            Finding(
                "warning",
                "unknown_config_key",
                f"config.yaml key {key!r} is ignored (known: {', '.join(sorted(CONFIG_KEYS))})",
                "config.yaml",
            )
        )
    declared = {v["name"] for v in variables}
    sizing = raw.get("sizing")
    sizes: dict[str, set[str]] = {}
    if sizing is not None:
        if not isinstance(sizing, dict) or not all(
            isinstance(envs, dict) and all(isinstance(v, dict) for v in envs.values())
            for envs in sizing.values()
        ):
            out.append(
                Finding(
                    "error",
                    "invalid_sizing",
                    "sizing must be sizing.<size>.<environment>.<variable>",
                    "config.yaml",
                )
            )
        else:
            for size, envs in sizing.items():
                sizes[str(size)] = {str(e) for e in envs}
                for env, values in envs.items():
                    for name in values:
                        if name not in declared:
                            out.append(
                                Finding(
                                    "error",
                                    "sizing_var_undeclared",
                                    f"sizing {size}/{env} sets {name!r}, which is not a declared "
                                    "variable",
                                    "config.yaml",
                                )
                            )
    costs = raw.get("estimated_costs")
    if costs is None:
        out.append(
            Finding(
                "warning",
                "no_estimated_costs",
                "config.yaml has no estimated_costs; environments with a budget refuse this "
                "pattern",
                "config.yaml",
            )
        )
        return
    valid = _numbers(costs) or (
        isinstance(costs, dict)
        and all(
            _numbers(v) or (isinstance(v, dict) and all(_numbers(x) for x in v.values()))
            for v in costs.values()
        )
    )
    if not valid:
        out.append(
            Finding(
                "error",
                "invalid_estimated_costs",
                "estimated_costs must be a number, <environment>: number or "
                "<size>: {<environment>: number}, all non-negative",
                "config.yaml",
            )
        )
        return
    if isinstance(costs, dict) and sizes:
        cost_sizes = {k for k, v in costs.items() if isinstance(v, dict)}
        for size in sorted(set(sizes) - cost_sizes):
            for env in sorted(sizes[size]):
                try:
                    priced = budgets.estimated_cost(raw, env, size)
                except TenancyError:
                    priced = None
                if priced is None:
                    out.append(
                        Finding(
                            "warning",
                            "cost_sizing_mismatch",
                            f"size {size}/{env} has no estimated cost",
                            "config.yaml",
                        )
                    )
        for size in sorted(cost_sizes - set(sizes)):
            out.append(
                Finding(
                    "warning",
                    "cost_sizing_mismatch",
                    f"estimated_costs lists size {size!r}, which sizing does not offer",
                    "config.yaml",
                )
            )
        for size in sorted(cost_sizes & set(sizes)):
            for env in sorted(sizes[size] ^ set(costs[size])):
                with suppress(TenancyError):
                    if budgets.estimated_cost(raw, env, size) is not None:
                        continue
                out.append(
                    Finding(
                        "warning",
                        "cost_sizing_mismatch",
                        f"size {size} is priced and sized for different environments ({env})",
                        "config.yaml",
                    )
                )


def _state(module: _Module, out: list[Finding]) -> None:
    backends = []
    for file, document in module.docs.items():
        for block in document.get("terraform", []):
            for backend in block.get("backend", []):
                for kind, body in backend.items():
                    if kind != "__is_block__":
                        backends.append((file, _unquote(kind), body))
    if not backends:
        out.append(
            Finding(
                "warning",
                "local_state",
                "no backend: state stays local to one worker, and version upgrades will be refused",
            )
        )
        return
    azure = False
    with suppress(catalog.CatalogError):
        azure = catalog.uses_azurerm_backend(module.root)
    for file, kind, body in backends:
        line = module.line(file, r'backend\s+"')
        if kind == "azurerm" and azure:
            out.append(
                Finding(
                    "info",
                    "platform_backend",
                    "azurerm backend: the API injects the state configuration",
                    file,
                    line,
                )
            )
            for key in BACKEND_HARDCODED:
                if isinstance(body, dict) and key in body:
                    out.append(
                        Finding(
                            "error",
                            "hardcoded_backend",
                            f"azurerm backend hard-codes {key}; leave it empty, the API injects "
                            "it per deployment",
                            file,
                            module.line(file, r'backend\s+"', rf"\b{key}\b"),
                        )
                    )
        else:
            out.append(
                Finding(
                    "warning",
                    "self_configured_backend",
                    f"backend {kind!r} is configured by the pattern itself; the platform "
                    "neither injects nor protects it",
                    file,
                    line,
                )
            )


def _safety(module: _Module, out: list[Finding]) -> None:
    def provisioners(body: Any):
        for entry in body.get("provisioner", []) if isinstance(body, dict) else []:
            for kind in entry:
                if kind != "__is_block__":
                    yield _unquote(kind)

    for kind in ("resource", "data"):
        for file, type_, rest in module.all(kind):
            type_ = _unquote(type_)
            for name, body in rest.items():
                if name == "__is_block__":
                    continue
                name = _unquote(name)
                label = rf'{kind}\s+"{re.escape(type_)}"\s+"{re.escape(name)}"'
                for what in provisioners(body):
                    out.append(
                        Finding(
                            "warning",
                            "remote_code",
                            f"{type_}.{name} runs a {what} provisioner (arbitrary commands)",
                            file,
                            module.line(file, label, rf"provisioner\s+\"{what}\""),
                        )
                    )
                if kind == "data" and type_ in ("external", "http"):
                    out.append(
                        Finding(
                            "warning",
                            "remote_code",
                            f"data.{type_}.{name} runs code or fetches remote content at plan time",
                            file,
                            module.line(file, label),
                        )
                    )
                if kind == "resource" and isinstance(body, dict):
                    for meta in ("for_each", "count"):
                        derived = [v for v in PLACEMENT_VARIABLES if _refs(body.get(meta), v)]
                        if derived:
                            out.append(
                                Finding(
                                    "warning",
                                    "placement_in_address",
                                    f"{type_}.{name} {meta} is derived from {derived[0]}; the "
                                    "plan address would reveal placement and the plan would be "
                                    "refused as an unsafe summary",
                                    file,
                                    module.line(file, label, rf"\b{meta}\b"),
                                )
                            )
    for file, name, body in module.all("module"):
        for meta in ("for_each", "count"):
            derived = [v for v in PLACEMENT_VARIABLES if _refs(body.get(meta), v)]
            if derived:
                out.append(
                    Finding(
                        "warning",
                        "placement_in_address",
                        f"module.{_unquote(name)} {meta} is derived from {derived[0]}; the plan "
                        "address would reveal placement",
                        file,
                        module.line(file, rf'module\s+"{re.escape(_unquote(name))}"', meta),
                    )
                )


def _nested(module: _Module, out: list[Finding]) -> None:
    for path in module.nested:
        label = str(path.relative_to(module.root))
        document = module._parse(path, label)
        if document and document.get("variable"):
            out.append(
                Finding(
                    "info",
                    "nested_module_variables",
                    "variables declared in a nested module are not part of the input schema; "
                    "only the root module's variables are",
                    label,
                )
            )


def _terraform_validate(root: Path) -> list[Finding]:
    binary = shutil.which("terraform")
    if not binary:
        return [Finding("warning", "terraform_unavailable", "terraform is not on PATH")]
    env = catalog.git_env(token=False) | {"TF_IN_AUTOMATION": "1", "TF_INPUT": "0"}

    def run(workdir: Path, *args: str) -> tuple[int, str]:
        process = subprocess.Popen(
            [binary, *args],
            cwd=workdir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=TERRAFORM_TIMEOUT)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            return 124, "terraform timed out"
        return process.returncode, output

    with tempfile.TemporaryDirectory(prefix="pattern-check-") as tmp:
        work = Path(tmp) / "module"
        shutil.copytree(
            root,
            work,
            ignore=shutil.ignore_patterns(".terraform", ".git", "*.tfstate*", "*.tfplan"),
        )
        code, output = run(work, "init", "-backend=false", "-input=false", "-no-color")
        if code != 0:
            tail = " ".join(output.strip().splitlines()[-6:])
            return [Finding("error", "terraform_init", f"terraform init failed: {tail[:600]}")]
        code, output = run(work, "validate", "-json", "-no-color")
    try:
        report = json.loads(output)
    except ValueError:
        return [
            Finding("error", "terraform_validate", f"unreadable validate output: {output[:300]}")
        ]
    found = []
    for diagnostic in report.get("diagnostics", []):
        where = diagnostic.get("range") or {}
        message = diagnostic.get("summary", "")
        if diagnostic.get("detail"):
            message += f": {' '.join(diagnostic['detail'].split())}"
        found.append(
            Finding(
                "error" if diagnostic.get("severity") == "error" else "warning",
                "terraform_validate",
                message,
                where.get("filename"),
                (where.get("start") or {}).get("line"),
            )
        )
    return found


def check(
    root: Path,
    *,
    cloud: str | None = None,
    terraform: bool = False,
    terraform_version: str = PLATFORM_TERRAFORM,
    config_fallback: Path | None = None,
) -> list[Finding]:
    """Static contract check of the root module in `root`. `config_fallback` is a parent folder
    searched for config.yaml when the module has none (pattern repos keep it at the repo root)."""
    root = Path(root)
    module = _Module(root)
    out = list(module.findings)
    if module.docs:
        declared = _terraform_block(module, out, terraform_version)
        variables = _inputs(module, out)
        used = _used_providers(module)
        for name in sorted(used - declared):
            out.append(
                Finding(
                    "warning",
                    "undeclared_provider",
                    f"provider {name} is used but not declared in required_providers",
                )
            )
        has_content = any(
            module.docs[f].get("resource") or module.docs[f].get("module") for f in module.docs
        )
        if not has_content:
            out.append(
                Finding("error", "no_resources", "the root module declares no resources or modules")
            )
        inferred = cloud is None
        cloud = cloud or _infer_cloud(declared, used)
        if cloud is not None:
            _placement(module, out, variables, cloud, inferred)
        _outputs(module, out)
        _config(module, out, variables, config_fallback)
        _state(module, out)
        _safety(module, out)
    elif not module.findings:
        out.append(Finding("error", "no_resources", "no .tf files in the root module"))
    _nested(module, out)
    out.extend(f for f in module.findings if f not in out)
    if terraform and not any(f.code == "hcl_parse" for f in out):
        out.extend(_terraform_validate(root))
    return out


# --- catalog entries -------------------------------------------------------------------------

CATALOG_KEYS = set(catalog.Pattern.__dataclass_fields__) - {"name"}
CATALOG_GIT_TIMEOUT = 30


def fetch(url: str, ref: str | None, dest: Path) -> None:
    """Shallow checkout of `ref` (tag or commit; default HEAD) using the catalog's bounded git."""
    catalog._git("init", "-q", cwd=dest, timeout=CATALOG_GIT_TIMEOUT)
    catalog._git("fetch", "-q", "--depth=1", url, ref or "HEAD", cwd=dest, timeout=60)
    catalog._git("checkout", "-q", "FETCH_HEAD", cwd=dest, timeout=CATALOG_GIT_TIMEOUT)


def check_catalog(path: Path) -> list[Finding]:
    path = Path(path)
    out: list[Finding] = []
    label = path.name
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return [Finding("error", "catalog_unreadable", "catalog is not readable YAML", label)]
    entries = raw.get("patterns") if isinstance(raw, dict) else None
    if not isinstance(entries, dict):
        return [Finding("error", "catalog_shape", "catalog needs a `patterns:` mapping", label)]
    for name, spec in entries.items():
        where = f"{label}:{name}"

        def add(level: str, code: str, message: str, where=where, name=name):
            out.append(Finding(level, code, f"{name}: {message}", where))

        if not isinstance(spec, dict):
            add("error", "catalog_entry", "entry must be a mapping")
            continue
        unknown = sorted(set(spec) - CATALOG_KEYS)
        if unknown:
            add("error", "unknown_catalog_key", f"unknown key(s) {', '.join(map(str, unknown))}")
        if spec.get("cloud") not in (None, *CLOUDS):
            add("error", "invalid_cloud", f"cloud must be one of {', '.join(CLOUDS)}")
        if spec.get("local") and spec.get("repo"):
            add("error", "local_and_repo", "`local` and `repo` are mutually exclusive")
            continue
        if not spec.get("local") and not spec.get("repo"):
            add("error", "no_source", "entry needs `repo` or `local`")
            continue
        if unknown or spec.get("cloud") not in (None, *CLOUDS):
            continue
        pattern = catalog.Pattern(name=str(name), **spec)
        subdir = pattern.path.strip("/")
        if pattern.local:
            target = Path(pattern.local) / subdir
            if not any(target.glob("*.tf")):
                add("error", "path_missing", f"local directory {pattern.local} has no .tf files")
            continue
        try:
            tags = catalog.versions(pattern, timeout=CATALOG_GIT_TIMEOUT)
            listing = catalog._git(
                "ls-remote", "--tags", pattern.git_url, timeout=CATALOG_GIT_TIMEOUT
            )
        except catalog.CatalogError as error:
            add("error", "repo_unreachable", str(error))
            continue
        every = {
            line.split("\t")[1].removeprefix("refs/tags/").removesuffix("^{}")
            for line in listing.splitlines()
        }
        ignored = sorted(every - set(tags))
        if ignored:
            add("info", "non_semver_tags", f"ignored non-semver tags: {', '.join(ignored)}")
        if not tags:
            add("error", "no_versions", "repository has no semver tags (vX.Y.Z)")
            continue
        default = pattern.default_version or next(iter(tags))
        if default not in tags:
            add(
                "error",
                "default_version_missing",
                f"default_version {default!r} is not a tag; available: {', '.join(tags)}",
            )
            continue
        with tempfile.TemporaryDirectory(prefix="catalog-check-") as tmp:
            try:
                fetch(pattern.git_url, tags[default], Path(tmp))
            except catalog.CatalogError as error:
                add("error", "repo_unreachable", str(error))
                continue
            if not any((Path(tmp) / subdir).glob("*.tf")):
                add(
                    "error",
                    "path_missing",
                    f"path {pattern.path or '.'!r} has no .tf files at {default}",
                )
    return out


# --- CLI -------------------------------------------------------------------------------------


def _counts(findings: list[Finding]) -> tuple[int, int]:
    return (
        sum(f.level == "error" for f in findings),
        sum(f.level == "warning" for f in findings),
    )


def snippet(
    name: str, source: str, path: str, cloud: str | None, ref: str | None, is_git: bool
) -> str:
    lines = ["patterns:", f"  {name}:"]
    if is_git:
        repo = re.sub(r"^https?://", "", source).removesuffix(".git")
        lines.append(f"    repo: {repo}")
    else:
        lines.append(f"    local: {source}")
    if path:
        lines.append(f"    path: {path}")
    if ref:
        lines.append(f"    default_version: {ref}")
    if cloud:
        lines.append(f"    cloud: {cloud}")
    lines += [
        "",
        f"# Then allow it for a business unit in tenants.yaml: add `{name}` to that unit's",
        "# `patterns:` list. Tag the pattern repo vX.Y.Z; versions are its semver tags.",
    ]
    return "\n".join(lines)


def render(findings: list[Finding]) -> str:
    lines = []
    for level in ("error", "warning", "info"):
        group = [f for f in findings if f.level == level]
        if not group:
            continue
        lines.append(f"{level.upper()} ({len(group)})")
        for f in group:
            where = f"{f.file}:{f.line}: " if f.file and f.line else f"{f.file}: " if f.file else ""
            lines.append(f"  [{f.code}] {where}{f.message}")
        lines.append("")
    errors, warnings = _counts(findings)
    lines.append(f"{errors} error(s), {warnings} warning(s)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.pattern_check",
        description="Check a Terraform pattern against the ForgeAPI contract.",
    )
    parser.add_argument("source", nargs="?", help="pattern directory or git URL")
    parser.add_argument("--ref", help="git tag or commit (git URLs)")
    parser.add_argument("--path", default="", help="sub-directory holding the root module")
    parser.add_argument("--cloud", choices=CLOUDS)
    parser.add_argument("--terraform", action="store_true", help="also run terraform validate")
    parser.add_argument("--terraform-version", default=PLATFORM_TERRAFORM)
    parser.add_argument("--catalog", type=Path, help="check a patterns.yaml instead")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.catalog) == bool(args.source):
        parser.error("give a directory or git URL, or --catalog patterns.yaml")

    registration = None
    if args.catalog:
        findings = check_catalog(args.catalog)
    else:
        is_git = "://" in args.source or args.source.startswith("git@")
        try:
            if is_git:
                with tempfile.TemporaryDirectory(prefix="pattern-check-") as tmp:
                    fetch(args.source, args.ref, Path(tmp))
                    findings = check(
                        Path(tmp) / args.path,
                        cloud=args.cloud,
                        terraform=args.terraform,
                        terraform_version=args.terraform_version,
                        config_fallback=Path(tmp),
                    )
            else:
                findings = check(
                    Path(args.source) / args.path,
                    cloud=args.cloud,
                    terraform=args.terraform,
                    terraform_version=args.terraform_version,
                )
        except catalog.CatalogError as error:
            findings = [Finding("error", "fetch_failed", str(error))]
        if _counts(findings)[0] == 0:
            base = args.source.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
            registration = snippet(
                base or "my-pattern", args.source, args.path, args.cloud, args.ref, is_git
            )
    errors, warnings = _counts(findings)
    if args.json:
        print(
            json.dumps(
                {
                    "findings": [asdict(f) for f in findings],
                    "errors": errors,
                    "warnings": warnings,
                    "registration": registration,
                },
                indent=2,
            )
        )
    else:
        print(render(findings))
        if registration:
            print("\nRegistration (patterns.yaml):\n" + registration)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
