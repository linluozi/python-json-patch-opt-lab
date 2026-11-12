"""Byte-identical differential tests: baseline copy vs optimized copy.

Each random document pair is generated from its own seed (no seed reset inside
a loop), then deduplicated by sha256 of the canonical serialization, so the
reported number is the number of *independent* pairs actually compared.
"""
import hashlib
import importlib.util
import json
import math
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BASE = load("jp_base", os.path.join(ROOT, "baseline_src", "jsonpatch.py"))
OPT = load("jp_opt", os.path.join(ROOT, "src", "jsonpatch.py"))

SCALARS = [None, True, False, 0, 1, -1, 2 ** 63, -2 ** 63, 0.0, 1.5, -3.25,
           "", "a", "x" * 300, "日本語テキスト", "naïve", "emoji 😀",
           "\u0000ctrl", "line\nbreak", "tab\tchar"]


def random_value(rng, depth=0):
    r = rng.random()
    if depth >= 4 or r < 0.55:
        return rng.choice(SCALARS)
    if r < 0.78:
        return [random_value(rng, depth + 1) for _ in range(rng.randrange(0, 5))]
    return {rng.choice(["a", "b", "c", "key", "日本", ""]) + str(i):
            random_value(rng, depth + 1) for i in range(rng.randrange(0, 5))}


def mutate(rng, doc, depth=0):
    if isinstance(doc, dict) and doc and rng.random() < 0.7:
        key = rng.choice(list(doc))
        doc = dict(doc)
        if rng.random() < 0.4:
            del doc[key]
        else:
            doc[key] = mutate(rng, doc[key], depth + 1)
        if rng.random() < 0.3:
            doc["added" + str(rng.randrange(9))] = random_value(rng, depth + 1)
        return doc
    if isinstance(doc, list) and doc and rng.random() < 0.7:
        doc = list(doc)
        i = rng.randrange(len(doc))
        if rng.random() < 0.4:
            doc.pop(i)
        else:
            doc[i] = mutate(rng, doc[i], depth + 1)
        if rng.random() < 0.4:
            doc.append(random_value(rng, depth + 1))
        return doc
    return random_value(rng, depth + 1)


def corpus(n=1400):
    seen, pairs = set(), []
    for i in range(n):
        rng = random.Random(20261113 + i)  # one seed per pair, never reset in a loop
        src = random_value(rng)
        dst = mutate(rng, src) if rng.random() < 0.85 else random_value(rng)
        key = hashlib.sha256(json.dumps([src, dst], sort_keys=True,
                                        ensure_ascii=False).encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        pairs.append((src, dst, key))
    return pairs


def dump(patch):
    return json.dumps(patch.patch, ensure_ascii=False, sort_keys=False)


PAIRS = corpus()


def test_independent_sample_count():
    assert len(PAIRS) >= 1000, len(PAIRS)
    keys = {p[2] for p in PAIRS}
    assert len(keys) == len(PAIRS)


def test_byte_identical_patches():
    mismatches = []
    for src, dst, key in PAIRS:
        for opt in (True, False):
            a = dump(BASE.JsonPatch.from_diff(src, dst, optimization=opt))
            b = dump(OPT.JsonPatch.from_diff(src, dst, optimization=opt))
            if a != b:
                mismatches.append((key, opt, a, b))
    assert not mismatches, mismatches[:2]


def test_patches_apply_and_reach_dst():
    for src, dst, key in PAIRS[:400]:
        p = OPT.JsonPatch.from_diff(src, dst)
        assert p.apply(src) == dst, key


def test_boundary_inputs_explicit():
    cases = [
        ({}, {}), ([], []), (None, None), (0, 0), (True, 1), (1, True),
        ({"a": []}, {"a": [None]}), ([[[[]]]], [[[{}]]]),
        ({"k": "x" * 5000}, {"k": "x" * 4999 + "y"}),
        ({"日本": {"語": [1, 2]}}, {"日本": {"語": [1, 2, 3]}}),
        (float("nan"), 1.0), ({"n": float("nan")}, {"n": float("nan")}),
        ({"t": (1, 2)}, {"t": (1, 2, 3)}),
    ]
    for src, dst in cases:
        a = dump(BASE.JsonPatch.from_diff(src, dst))
        b = dump(OPT.JsonPatch.from_diff(src, dst))
        assert a == b, (src, dst)


def test_generated_patch_does_not_alias_documents():
    for mod in (BASE, OPT):
        src = {"keep": [1, 2], "old": {"x": 1}}
        dst = {"keep": [1, 2, 3], "new": {"deep": [4, 5]}}
        p = mod.JsonPatch.from_diff(src, dst)
        dst["new"]["deep"].append(999)
        dst["keep"].append(777)
        assert "999" not in dump(p)
        assert "777" not in dump(p)
        values = [op.get("value") for op in p.patch if "value" in op]
        assert {"deep": [4, 5]} in values or [4, 5] in values
        # mutating the patch must not reach back into dst either
        for op in p.patch:
            if isinstance(op.get("value"), list) and op["value"]:
                op["value"].append(-1)
        assert "999" in json.dumps(dst)


def test_shared_subtrees_stay_shared_within_patch():
    shared = {"v": [1, 2]}
    dst = {"a": shared, "b": shared}
    for mod in (BASE, OPT):
        p = mod.JsonPatch.from_diff({"a": [0], "b": [0]}, dst)
        lists = []
        for op in p.patch:
            val = op.get("value")
            if isinstance(val, dict):
                lists.append(val)
        # both document members must be independent copies of the same shape
        assert len(lists) >= 2
        assert lists[0] == lists[1] == {"v": [1, 2]}
        # copy.deepcopy preserves shared references; _copy_json must too
        assert lists[0] is lists[1]
