"""
tests/test_batch_entrypoint.py — the batch image's stages start their program through container_entrypoint.py.

The hub-canonical ``container_entrypoint.py`` (tested by tests/test_container_entrypoint_shared.py)
runs the program a stage names, or Galaxy's job script (``docker run IMAGE /bin/sh tool_script.sh``,
atrium-project#66). This file pins which stage runs which program, by its absolute path in the image,
and that the program is in this repository. The ``api`` stage keeps its own ENTRYPOINT.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Dockerfile stage -> the program its ENTRYPOINT runs, as a path in the image.
STAGES = {
    "base": "/app/run_pipeline.py",
}


def _entrypoints() -> dict:
    """The last ENTRYPOINT of every stage, as an argv list."""
    found, stage = {}, None
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        words = line.split()
        if words[:1] == ["FROM"]:
            stage = words[3] if len(words) >= 4 and words[2].upper() == "AS" else None
        elif words[:1] == ["ENTRYPOINT"] and stage:
            found[stage] = json.loads(line.split(None, 1)[1])
    return found


@pytest.mark.parametrize("stage", sorted(STAGES))
def test_the_stage_runs_its_program_through_the_shared_entrypoint(stage):
    assert _entrypoints().get(stage) == ["python", "/app/container_entrypoint.py", STAGES[stage]]


@pytest.mark.parametrize("stage", sorted(STAGES))
def test_the_program_is_in_the_repository(stage):
    assert (ROOT / STAGES[stage].removeprefix("/app/")).is_file()


def test_the_entrypoint_module_is_vendored():
    assert (ROOT / "container_entrypoint.py").is_file()
