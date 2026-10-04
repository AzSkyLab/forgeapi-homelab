import os
import re
import subprocess
import tomllib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"


def test_image_publish_requires_read_only_checks():
    # BaseLoader keeps GitHub's "on" key intact under YAML 1.1 parsing.
    image = yaml.load((WORKFLOWS / "image.yml").read_text(), Loader=yaml.BaseLoader)
    check = yaml.load((WORKFLOWS / "check.yml").read_text(), Loader=yaml.BaseLoader)

    assert image["on"] == {"push": {"tags": ["v*"]}, "workflow_dispatch": ""}
    assert image["jobs"]["publish"]["needs"] == "check"
    assert image["jobs"]["check"]["uses"] == "./.github/workflows/check.yml"
    assert "if" not in image["jobs"]["publish"]
    assert "if" not in image["jobs"]["check"]
    assert image["jobs"]["publish"]["permissions"]["packages"] == "write"
    assert check["permissions"] == {"contents": "read"}
    assert set(check["on"]) == {"pull_request", "push", "workflow_call"}
    job = check["jobs"]["check"]
    assert "if" not in job and "continue-on-error" not in job
    steps = job["steps"]
    assert all("if" not in step and "continue-on-error" not in step for step in steps)
    assert [step["run"] for step in steps if "run" in step] == [
        "uv sync --locked",
        "uv run --frozen ruff check .",
        "uv run --frozen pytest -q",
    ]


def test_release_tag_must_match_project_version_before_publish():
    image = yaml.load((WORKFLOWS / "image.yml").read_text(), Loader=yaml.BaseLoader)
    steps = image["jobs"]["publish"]["steps"]
    checkout, guard, login, setup_buildx, metadata, build, metadata_engine, build_engine = steps
    push_only = "${{ github.event_name == 'push' }}"

    assert checkout["uses"] == "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
    assert checkout["with"]["persist-credentials"] == "false"
    assert guard["if"] == push_only
    assert "${{" not in guard["run"]
    assert login["uses"] == "docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9"
    assert re.fullmatch(r"docker/setup-buildx-action@[0-9a-f]{40}", setup_buildx["uses"])
    assert metadata["uses"] == "docker/metadata-action@c299e40c65443455700f0fdfc63efafe5b349051"
    assert metadata["with"]["images"] == "ghcr.io/${{ github.repository_owner }}/forgeapi"
    assert metadata["with"]["tags"].splitlines() == [
        f"type=ref,event=tag,enable={push_only}",
        "type=sha,format=long",
    ]
    assert build["uses"] == "docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8"
    assert "target" not in build["with"]

    # Same tag scheme, a second image published from the "engine" target (Dockerfile).
    engine_image = "ghcr.io/${{ github.repository_owner }}/forgeapi-engine"
    assert metadata_engine["with"]["images"] == engine_image
    assert metadata_engine["with"]["tags"] == metadata["with"]["tags"]
    assert build_engine["uses"] == build["uses"]
    assert build_engine["with"]["target"] == "engine"

    for step in (build, build_engine):
        assert step["with"]["provenance"] == "mode=max"
        assert step["with"]["sbom"] == "true"

    assert all("continue-on-error" not in step for step in steps)
    assert "continue-on-error" not in image["jobs"]["publish"]

    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    for ref, expected_code in [(f"v{version}", 0), ("v0.0.0", 1), ("main", 1)]:
        result = subprocess.run(
            ["bash", "-e", "-c", guard["run"]],
            cwd=REPO,
            env={**os.environ, "GITHUB_REF_NAME": ref},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == expected_code, (ref, result.stderr)


def test_external_workflow_actions_are_commit_pinned():
    for name in ("check.yml", "image.yml"):
        workflow = yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)
        for job in workflow["jobs"].values():
            for use in [job.get("uses"), *(step.get("uses") for step in job.get("steps", []))]:
                if use and not use.startswith("./"):
                    assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", use), (name, use)


def test_image_scan_job_builds_both_targets_and_fails_closed():
    check = yaml.load((WORKFLOWS / "check.yml").read_text(), Loader=yaml.BaseLoader)
    job = check["jobs"]["image-scan"]
    assert "if" not in job and "continue-on-error" not in job
    steps = job["steps"]
    assert all("if" not in step and "continue-on-error" not in step for step in steps)

    assert any(
        step.get("run", "").split() == ["docker", "build", "-t", "forgeapi:ci", "."]
        for step in steps
    )
    assert any(
        "--target" in step.get("run", "") and "engine" in step.get("run", "")
        for step in steps
    )

    scan = next(step for step in steps if "uses" in step and "trivy-action" in step["uses"])
    assert re.fullmatch(r"aquasecurity/trivy-action@[0-9a-f]{40}", scan["uses"])
    assert scan["with"]["ignore-unfixed"] == "true"
    assert scan["with"]["severity"] == "CRITICAL,HIGH"
    assert scan["with"]["exit-code"] == "1"


def test_dockerfile_base_images_pinned_and_default_stage_has_no_temporal():
    lines = (REPO / "Dockerfile").read_text().splitlines()
    from_lines = [line for line in lines if line.startswith("FROM ")]

    # Every FROM referencing a registry image (not a prior stage by name) is digest-pinned.
    for line in from_lines:
        ref = line.split()[1]
        if ref == "runtime":
            continue
        assert re.search(r"@sha256:[0-9a-f]{64}$", ref), line

    stages: list[tuple[str | None, list[str]]] = []
    for line in lines:
        if line.startswith("FROM "):
            parts = line.split()
            name = parts[3] if len(parts) > 3 and parts[2] == "AS" else None
            stages.append((name, []))
        elif stages:
            stages[-1][1].append(line)

    # The default stage (no --target) is the LAST one in the file, and must not be "engine".
    assert stages[-1][0] != "engine"
    default_body = stages[-1][1]
    assert not any("temporal" in line.lower() for line in default_body)

    engine_body = next(body for name, body in stages if name == "engine")
    assert any(line.strip().startswith("COPY --from=temporal") for line in engine_body)
