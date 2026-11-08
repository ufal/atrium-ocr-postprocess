"""tests/test_vendored_decision_parity.py -- the decision-model client vendored from atrium-page-classification.

ufal/atrium-project#83 runs decision models (Clef, Clef-Flash, Strands Decider 2B, served at
``POST /v1/systemone``) in four tools through one client, ``decision_client.py``, owned by
atrium-page-classification (its #58). This repository uses it offline only, in
``tools/decide_lines.py --decide`` (#10); the service and the batch pipeline never import it, so it
is not in ``.github/production-image.json``.

The copy is pinned by the SHA-256 of the page-classification file it was taken from (line endings
normalised to ``\\n``). A mismatch means a local edit to a vendored file -- make the change in
atrium-page-classification instead -- or a re-vendor without updating the pin.

To re-vendor: copy it from a sibling ``../atrium-page-classification`` checkout, run
``python3 tests/test_vendored_decision_parity.py`` and paste the printed pin over ``VENDORED`` and
``ADOPTED``. Commit the copy and the pins together (atrium-project docs/shared_components.md).

When an atrium-page-classification checkout sits next to this repo, the copy is also compared with it
byte for byte; that check is skipped when the checkout is absent (CI).
"""

import hashlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE_CLASSIFICATION = REPO_ROOT.parent / "atrium-page-classification"

#: The owner revision the copy was taken from: the page-classification commit of 2026-11-06 whose
#: decision_client.py has the digest below, first released in v1.13.0-beta.
ADOPTED = {
    "owner": "atrium-page-classification",
    "commit": "39a7bea87080de6e0e7ed30eba600ce05f9bef8b",
    "release": "v1.13.0-beta",
}

VENDORED = {
    "decision_client.py": "7031b3f5bdb9f397318a396ab0042e662b8c21994afe230107e3e74c604e9604",
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


@pytest.mark.parametrize("rel", sorted(VENDORED))
def test_vendored_copy_matches_its_pin(rel):
    assert _digest(REPO_ROOT / rel) == VENDORED[rel], (
        f"{rel} differs from the atrium-page-classification copy it was vendored from -- change it in "
        "atrium-page-classification and re-vendor (see this module's docstring)"
    )


@pytest.mark.parametrize("rel", sorted(VENDORED))
def test_vendored_copy_matches_a_sibling_page_classification_checkout(rel):
    upstream = PAGE_CLASSIFICATION / rel
    if not upstream.is_file():
        pytest.skip("no ../atrium-page-classification checkout next to this repo")
    assert _digest(REPO_ROOT / rel) == _digest(upstream), f"{rel} drifted from atrium-page-classification"


def test_adopted_names_an_immutable_owner_revision():
    assert ADOPTED["owner"] == PAGE_CLASSIFICATION.name
    assert re.fullmatch(r"[0-9a-f]{40}", ADOPTED["commit"]), "a full commit SHA, not a branch or a short SHA"
    assert re.fullmatch(r"v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?", ADOPTED["release"])


def test_the_client_selftest_passes():
    import decision_client

    assert decision_client.selftest() == 0


if __name__ == "__main__":
    for rel in sorted(VENDORED):
        print(f'    "{rel}": "{_digest(REPO_ROOT / rel)}",')
