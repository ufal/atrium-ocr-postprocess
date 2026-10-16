"""
tests/test_perplexity_backoff.py
================================

`text_util.calculate_perplexity_batch`: a batch that runs out of memory is split and retried, and
only a line that fails on its own scores 99999.

Why it exists
-------------
Before, any failure of a batch scored EVERY line of it 99999 ("worst quality") with only a stderr line
and, in the service, a `ppl_batch_lines` `skipped` note. atrium-digital-convert#5 measured it on a 24 GB
card shared with page-classification: 1,280 lines in 6 documents scored 99999 at 128 lines a batch, the
stage still read `ok`, and its Trash share moved. The service path now answers a batch that does not fit
by halving it (a smaller batch costs time, never quality).

The splitting logic is tested model-free (the forward pass is replaced); the one test that needs torch
(`labels=` is gone from the model call and the numbers did not move) is `slow`, like every test that runs
a model, runs in a clean subprocess (other test modules stub `torch`), and builds a tiny randomly
initialised GPT-2 -- no download.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import text_util as tu

_ROOT = Path(__file__).resolve().parent.parent


def _oom():
    return RuntimeError("CUDA out of memory. Tried to allocate 4.00 GiB (GPU 0; 23.65 GiB total capacity)")


def _forward_limited(max_rows, log):
    """A forward pass that runs out of memory above *max_rows* lines; the value of a line is its length."""

    def forward(texts, model, tokenizer, device):
        log.append(len(texts))
        if len(texts) > max_rows:
            raise _oom()
        return [float(len(t)) for t in texts]

    return forward


TEXTS = ["a" * n for n in range(1, 9)]  # 8 lines of distinct lengths, so order is visible


def test_a_batch_that_fits_is_one_forward_pass(monkeypatch):
    log = []
    monkeypatch.setattr(tu, "_perplexity_forward", _forward_limited(8, log))
    assert tu.calculate_perplexity_batch(TEXTS, None, None, "cpu") == [float(n) for n in range(1, 9)]
    assert log == [8]


def test_an_out_of_memory_batch_is_split_until_it_fits_and_keeps_its_order(monkeypatch):
    log = []
    monkeypatch.setattr(tu, "_perplexity_forward", _forward_limited(2, log))
    out = tu.calculate_perplexity_batch(TEXTS, None, None, "cpu")
    assert out == [float(n) for n in range(1, 9)], "every line is scored, in its own position"
    assert tu.PPL_FAILED not in out
    assert log == [8, 4, 2, 2, 4, 2, 2], "halves, depth first, each retried only as far as needed"


def test_an_odd_batch_splits_into_unequal_halves(monkeypatch):
    log = []
    monkeypatch.setattr(tu, "_perplexity_forward", _forward_limited(2, log))
    assert tu.calculate_perplexity_batch(TEXTS[:5], None, None, "cpu") == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert log == [5, 2, 3, 1, 2]


def test_a_line_that_fails_on_its_own_scores_99999_and_its_neighbours_survive(monkeypatch):
    calls = []

    def forward(texts, model, tokenizer, device):
        calls.append(len(texts))
        if any(len(t) == 5 for t in texts):  # this one line can never be scored
            raise _oom()
        return [float(len(t)) for t in texts]

    monkeypatch.setattr(tu, "_perplexity_forward", forward)
    out = tu.calculate_perplexity_batch(TEXTS, None, None, "cpu")
    assert out == [1.0, 2.0, 3.0, 4.0, tu.PPL_FAILED, 6.0, 7.0, 8.0]
    assert calls[-1] >= 1 and 1 in calls


def test_an_error_that_is_not_out_of_memory_is_not_retried(monkeypatch):
    log = []

    def forward(texts, model, tokenizer, device):
        log.append(len(texts))
        raise ValueError("boom")

    monkeypatch.setattr(tu, "_perplexity_forward", forward)
    assert tu.calculate_perplexity_batch(TEXTS, None, None, "cpu") == [tu.PPL_FAILED] * 8
    assert log == [8], "splitting cannot fix a bug, and would repeat it eight times"


def test_the_cpu_allocator_form_of_out_of_memory_counts_too(monkeypatch):
    log = []

    def forward(texts, model, tokenizer, device):
        log.append(len(texts))
        if len(texts) > 4:
            raise RuntimeError("[enforce fail at alloc_cpu.cpp] DefaultCPUAllocator: can't allocate memory")
        return [1.0] * len(texts)

    monkeypatch.setattr(tu, "_perplexity_forward", forward)
    assert tu.calculate_perplexity_batch(TEXTS, None, None, "cpu") == [1.0] * 8
    assert log == [8, 4, 4]


def test_an_empty_batch_is_empty(monkeypatch):
    monkeypatch.setattr(tu, "_perplexity_forward", lambda *a: pytest.fail("no forward pass for no lines"))
    assert tu.calculate_perplexity_batch([], None, None, "cpu") == []


def test_the_retry_runs_outside_the_except_block():
    """While an except block is open its traceback keeps the failed batch's tensors alive, which is
    exactly the memory a retry needs back. A guard on the source, since no unit test can see tensors."""
    import inspect

    source = inspect.getsource(tu.calculate_perplexity_batch)
    handler_end = source.index("    # Retried OUTSIDE the except block")
    assert source.index("except Exception") < handler_end < source.index("calculate_perplexity_batch(texts[:half]")


def test_the_service_reports_the_lines_that_failed_alone_not_the_batch():
    from unittest.mock import MagicMock

    from atrium_limits import LimitNotes
    from service.text_inference import TextModelManager

    m = TextModelManager()
    m.ppl_model = MagicMock()
    m.ppl_model.config.max_position_embeddings = 1024
    m.ppl_tokenizer = None
    notes = LimitNotes()
    # Three of four lines were scored; one failed even alone.
    m._note_perplexity(["a", "b", "c", "d"], [10.0, tu.PPL_FAILED, 30.0, 40.0], notes)
    [entry] = notes.as_list()
    assert (entry["limit"], entry["effect"], entry["count"]) == ("ppl_batch_lines", "skipped", 1)


# ── needs torch: the model call itself ──────────────────────────────────────────────────────

#: Run in a CLEAN interpreter: several test modules stub `torch` in `sys.modules` at import (the
#: fast lane has no model stack), and `pytest.importorskip` would hand this test that stub.
_PARITY_SCRIPT = r"""
import json, sys, types
import torch
from torch import nn
from transformers import GPT2Config, GPT2LMHeadModel
import text_util as tu

torch.manual_seed(0)
model = GPT2LMHeadModel(GPT2Config(vocab_size=300, n_positions=64, n_embd=32, n_layer=2, n_head=2)).eval()


class Tokenizer:
    def __call__(self, texts, return_tensors="pt", padding=True, truncation=True, max_length=64):
        ids = [[b % 299 + 1 for b in t.encode("utf-8")][:max_length] for t in texts]
        width = max(len(i) for i in ids)
        input_ids = torch.zeros(len(ids), width, dtype=torch.long)
        mask = torch.zeros(len(ids), width, dtype=torch.long)
        for row, i in enumerate(ids):
            input_ids[row, : len(i)] = torch.tensor(i)
            mask[row, : len(i)] = 1
        return types.SimpleNamespace(input_ids=input_ids, attention_mask=mask)


def previous_body(texts, model, tokenizer, device):
    # The body of calculate_perplexity_batch before 2026-10-16, verbatim: `labels=` passed to the model.
    max_length = getattr(model.config, "max_position_embeddings", 1024)
    enc = tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=max_length)
    input_ids, attention_mask = enc.input_ids.to(device), enc.attention_mask.to(device)
    target_ids = input_ids.clone()
    target_ids[attention_mask == 0] = -100
    with torch.no_grad():
        logits = model(input_ids, attention_mask=attention_mask, labels=target_ids).logits
        shift_logits, shift_labels = logits[..., :-1, :].contiguous(), target_ids[..., 1:].contiguous()
        loss = nn.CrossEntropyLoss(reduction="none")(
            shift_logits.view(-1, shift_logits.size(-1)), shift_labels.view(-1)
        ).view(target_ids.size(0), -1)
        kept = shift_labels != -100
        return torch.exp((loss * kept).sum(dim=1) / kept.sum(dim=1).clamp(min=1)).tolist()


class Flaky(nn.Module):
    def __init__(self, inner):
        super().__init__()
        self.inner, self.config = inner, inner.config

    def forward(self, input_ids, **kwargs):
        if input_ids.shape[0] > 2:
            raise RuntimeError("CUDA out of memory. Tried to allocate 4.00 GiB")
        return self.inner(input_ids, **kwargs)


texts = ["Praha 1932", "v klášteře Strahovském.", "a", "Opomenulé nebo opozděné ohlášení", "oueussd", "x y z"]
tok = Tokenizer()
now = tu.calculate_perplexity_batch(texts, model, tok, "cpu")
before = previous_body(texts, model, tok, "cpu")
split = tu.calculate_perplexity_batch(texts, Flaky(model), tok, "cpu")
print(json.dumps({"identical": now == before, "split_failed": tu.PPL_FAILED in split,
                  "split_max_rel_diff": max(abs(a - b) / max(1.0, abs(a)) for a, b in zip(now, split))}))
"""


def _torch_available():
    probe = subprocess.run([sys.executable, "-c", "import torch, transformers"], capture_output=True, cwd=str(_ROOT))
    return probe.returncode == 0


@pytest.mark.slow
def test_dropping_labels_leaves_every_perplexity_exactly_as_it_was():
    """`labels=` made Hugging Face compute a second, upcast loss over another copy of the logits that
    was thrown away; the logits are the same without it, and so is every number (a tiny random GPT-2,
    compared against the previous body of the function, verbatim). A batch that does not fit comes out the
    same, line for line, as one that does."""
    if not _torch_available():
        pytest.skip("torch and transformers are not installed")
    run = subprocess.run(
        [sys.executable, "-c", _PARITY_SCRIPT],
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
        env={**os.environ, "PYTHONPATH": str(_ROOT)},
    )
    assert run.returncode == 0, run.stderr[-2000:]
    result = json.loads(run.stdout.strip().splitlines()[-1])
    assert result["identical"] is True
    assert result["split_failed"] is False
    assert result["split_max_rel_diff"] < 1e-5
