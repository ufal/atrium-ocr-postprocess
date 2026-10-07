"""The service entrypoints must import when launched as SCRIPTS, not just as modules.

The Dockerfile's ``api`` stage runs ``ENTRYPOINT ["python", "service/text_api.py"]``.
Launching a script makes Python set ``sys.path[0]`` to the **script's directory**
(``/app/service``) -- not the working directory (``/app``) -- so the repo root is absent
and a module-level ``from atrium_document import ...`` raises ``ModuleNotFoundError``.

Every other launch context hides this: pytest imports the module as ``service.text_api``
from the repo root, ``uvicorn service.text_api:app`` runs with the repo root as CWD, and
the batch image's ``python run_pipeline.py`` has the repo root *as* its script directory.
So does CI -- ``docker-build-smoke`` is gated ``if: github.event_name == 'pull_request'``,
which means no push to ``test`` or ``master`` has ever started the ``api`` image at all.

These tests reproduce the container's ``sys.path`` exactly: a subprocess whose CWD is
outside the repo, with ``PYTHONPATH`` cleared, and only ``service/`` prepended. The module
is executed under a probe name so its ``if __name__ == "__main__"`` block does not start
uvicorn.

Marked ``slow`` because importing the FastAPI app pulls in the ML stack.

The last two tests are static and always run: the ``api`` stage must leave the port to
``$PORT`` (atrium-project#58), the fast-lane counterpart of the hub's non-default-port
container probe that the five other tool repositories already carry.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICE_DIR = REPO_ROOT / "service"

# Every service module the Dockerfile launches as a script.
#
# `text_api.py` is the `api` stage ENTRYPOINT and is REQUIRED to exist -- a missing
# one means the service was renamed or removed, which this test should report rather
# than silently pass over.
#
# `healthcheck.py` is the HEALTHCHECK CMD body. It is stdlib-only by design and is
# mirrored in from the hub, so it is OPTIONAL here: it does not exist on every branch
# during the issue-#55 api-stage rollout. Covered anyway, because the day it grows a
# first-party import this is the test that catches it.
REQUIRED_ENTRYPOINTS = ["text_api.py"]
OPTIONAL_ENTRYPOINTS = ["healthcheck.py"]
SCRIPT_ENTRYPOINTS = REQUIRED_ENTRYPOINTS + OPTIONAL_ENTRYPOINTS

#: The `api` stage's ENTRYPOINT: a script launch, made safe by text_api.py's sys.path
#: bootstrap (the two tests above).
API_ENTRYPOINT = ["python", "service/text_api.py"]

#: Environment variables the __main__ block must read for the k8s manifest's `env:` block
#: to mean anything. PORT is the one with teeth (healthcheck.py probes it); HOST and
#: GRACEFUL_SHUTDOWN_S are in the same contract (atrium-project#58, #55).
REQUIRED_ENV_READS = ("PORT", "HOST", "GRACEFUL_SHUTDOWN_S")


def _import_as_script(script_name: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Execute ``service/<script_name>`` with the sys.path a script launch really gets."""
    script = SERVICE_DIR / script_name
    code = (
        "import importlib.util, sys;"
        f"sys.path.insert(0, {str(SERVICE_DIR)!r});"
        f"spec = importlib.util.spec_from_file_location('_entrypoint_probe', {str(script)!r});"
        "mod = importlib.util.module_from_spec(spec);"
        # Not '__main__', so the module's own __main__ block stays inert.
        "spec.loader.exec_module(mod)"
    )

    env = dict(os.environ)
    # A stray PYTHONPATH pointing at the repo root would mask the very failure
    # this test exists to catch.
    env.pop("PYTHONPATH", None)

    return subprocess.run(
        [sys.executable, "-c", code],
        # Outside the repo, so the repo root cannot arrive via CWD either.
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.mark.slow
@pytest.mark.parametrize("script_name", SCRIPT_ENTRYPOINTS)
def test_service_entrypoint_imports_when_launched_as_a_script(script_name, tmp_path):
    if not (SERVICE_DIR / script_name).exists():
        if script_name in REQUIRED_ENTRYPOINTS:
            pytest.fail(
                f"service/{script_name} is missing. It is the Dockerfile `api` stage "
                f"ENTRYPOINT -- the image cannot start without it."
            )
        pytest.skip(f"service/{script_name} not present on this branch")

    result = _import_as_script(script_name, tmp_path)
    assert result.returncode == 0, (
        f"service/{script_name} does not import with the sys.path a script launch gets "
        f"(sys.path[0] = service/, repo root absent).\n"
        f"This is exactly how the Dockerfile `api` stage starts it.\n\n"
        f"stderr:\n{result.stderr}"
    )


def test_repo_root_bootstrap_precedes_first_party_imports():
    """The sys.path bootstrap must sit ABOVE the repo-root imports it enables.

    Cheap, no-import guard on source order, so the regression is caught by the fast
    lane too -- the subprocess tests above are ``slow`` and PR CI runs
    ``pytest -m "not slow"``. Ruff's import sorter is the thing most likely to undo
    this, which is why those imports carry ``noqa: E402``.
    """
    lines = (SERVICE_DIR / "text_api.py").read_text(encoding="utf-8").splitlines()

    bootstrap_line = next(
        (i for i, line in enumerate(lines) if line.startswith("_repo_root = ")),
        None,
    )
    assert bootstrap_line is not None, (
        "service/text_api.py no longer puts the repo root on sys.path. Without it the `api` image dies at import."
    )

    for module in ("atrium_document", "atrium_paradata", "document_hook"):
        import_line = next(
            (i for i, line in enumerate(lines) if line.startswith(f"from {module} import ")),
            None,
        )
        assert import_line is not None, f"expected a top-level `from {module} import ...`"
        assert import_line > bootstrap_line, (
            f"`from {module} import ...` (line {import_line + 1}) is hoisted above the "
            f"sys.path bootstrap (line {bootstrap_line + 1}). It will raise "
            f"ModuleNotFoundError under `python service/text_api.py`."
        )


def _api_entrypoint() -> list:
    """argv of the `api` stage's ENTRYPOINT, parsed out of the Dockerfile."""
    stage, found = None, None
    for raw in (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        match = re.match(r"^FROM\s+\S+\s+AS\s+(\S+)", line, re.IGNORECASE)
        if match:
            stage = match.group(1)
            continue
        if stage != "api" or not line.upper().startswith("ENTRYPOINT"):
            continue
        payload = line[len("ENTRYPOINT") :].strip()
        try:
            found = json.loads(payload) if payload.startswith("[") else shlex.split(payload)
        except ValueError:
            found = None
    return found or []


def _main_block_source() -> str:
    """Source of service/text_api.py's ``if __name__ == "__main__":`` block, or ""."""
    source = (SERVICE_DIR / "text_api.py").read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.If) and "__main__" in ast.dump(node.test):
            return ast.get_source_segment(source, node) or ""
    return ""


def test_api_entrypoint_does_not_hardcode_the_port():
    """The `api` stage must leave the port to $PORT (atrium-project#58).

    An exec-form ENTRYPOINT array runs no shell, so a `--port` baked in here cannot be
    overridden by the manifest's `env: PORT`; and because service/healthcheck.py DOES read
    PORT, the result is a container that reports unhealthy forever rather than one that
    merely ignores the setting.
    """
    argv = _api_entrypoint()
    assert argv, "the Dockerfile declares no ENTRYPOINT for the `api` stage"
    assert "--port" not in argv, (
        f"the `api` stage hardcodes the port in its ENTRYPOINT ({argv}). Exec form runs no "
        f"shell, so $PORT cannot expand, while service/healthcheck.py reads it: any "
        f"non-default PORT becomes a permanently unhealthy container. Read the port in "
        f"service/text_api.py's __main__ block instead (atrium-project#58)."
    )
    assert argv == API_ENTRYPOINT, (
        f"expected the `api` stage to launch {API_ENTRYPOINT}, got {argv}. If the launch "
        f"changed on purpose, update API_ENTRYPOINT and the import tests above with it."
    )


def test_main_block_reads_the_deployment_environment():
    """PORT/HOST/GRACEFUL_SHUTDOWN_S must come from the environment, not be literals.

    The manifest the partner deploys from (atrium-project
    docs/templates/k8s/atrium-service.deployment.yaml) declares `env: PORT`. If this block
    stops reading it, that declaration silently means nothing again.
    """
    block = _main_block_source()
    assert block, (
        'service/text_api.py has no `if __name__ == "__main__":` block, so the Dockerfile '
        "`api` stage ENTRYPOINT starts nothing"
    )
    missing = [name for name in REQUIRED_ENV_READS if f'"{name}"' not in block]
    assert not missing, (
        f"service/text_api.py's __main__ block does not read {missing} from the environment. "
        f"The k8s manifest declares these; a hardcoded value makes that declaration inert."
    )
    assert "uvicorn.run" in block, "the __main__ block never calls uvicorn.run"
