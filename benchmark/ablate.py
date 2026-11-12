#!/usr/bin/env python3
"""Per-change ablation: disable exactly one of the three changes and re-time.

Each variant is the full patched module with one change textually reverted.
The large real-corpus diff (ec2 2016-09-15 -> 2016-11-15) is timed, 3 warmups
and 7 samples; the median is reported. Marginal saving = (variant - full) /
(baseline - full) of the total measured saving.
"""
import importlib.util
import json
import os
import statistics
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FULL = os.path.join(ROOT, "src", "jsonpatch.py")
BASE = os.path.join(ROOT, "baseline_src", "jsonpatch.py")
SRC = os.path.join(HERE, "fixtures", "ec2-2016-09-15.json")
DST = os.path.join(HERE, "fixtures", "ec2-2016-11-15.json")

REVERTS = {
    "copy-fast-path": (
        "ops = _copy_json(list(builder.execute()))",
        "ops = copy.deepcopy(list(builder.execute()))"),
    "compare-exact-type": (
        """        st, dt = type(src), type(dst)
        if st is dict and dt is dict:
            self._compare_dicts(_path_join(path, key), src, dst)

        elif st is list and dt is list:
            self._compare_lists(_path_join(path, key), src, dst)

        elif isinstance(src, MutableMapping) and \\
                isinstance(dst, MutableMapping):""",
        """        if isinstance(src, MutableMapping) and \\
                isinstance(dst, MutableMapping):"""),
    "sorted-members-exact-type": (
        """    if type(value) is dict:
        return {key: _sorted_members(value[key]) for key in sorted(value)}
    if type(value) is list:
        return [_sorted_members(item) for item in value]
    if isinstance(value, MutableMapping):""",
        "    if isinstance(value, MutableMapping):"),
}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def measure(path):
    mod = load("ab_" + str(abs(hash(path))), path)
    src = json.load(open(SRC, encoding="utf-8"))
    dst = json.load(open(DST, encoding="utf-8"))
    for _ in range(3):
        mod.JsonPatch.from_diff(src, dst)
    xs = []
    for _ in range(7):
        t = time.perf_counter()
        mod.JsonPatch.from_diff(src, dst)
        xs.append(time.perf_counter() - t)
    return statistics.median(xs), sorted(xs)


def main():
    base, base_samples = measure(BASE)
    full, full_samples = measure(FULL)
    total = base - full
    variants = {}
    with tempfile.TemporaryDirectory() as tmp:
        text = open(FULL, encoding="utf-8").read()
        for name, (old, new) in REVERTS.items():
            assert old in text, name
            path = os.path.join(tmp, name.replace("-", "_") + ".py")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text.replace(old, new, 1))
            med, samples = measure(path)
            variants[name] = {
                "median_s": med, "samples_s": samples,
                "margin_s": med - full,
                "share_of_total_saving": (med - full) / total if total else 0.0,
            }
    doc = {
        "input": {"src": os.path.basename(SRC), "dst": os.path.basename(DST)},
        "baseline_median_s": base, "baseline_samples_s": base_samples,
        "full_median_s": full, "full_samples_s": full_samples,
        "total_saving_s": total,
        "variants": variants,
        "explained_fraction": sum(v["margin_s"] for v in variants.values()) / total,
        "warmup": 3, "reps": 7, "timer": "time.perf_counter",
    }
    with open(os.path.join(HERE, "ablation.json"), "w") as f:
        json.dump(doc, f, indent=1)
    print(json.dumps({k: (round(v, 5) if isinstance(v, float) else v)
                      for k, v in doc.items() if k != "variants"}, indent=1))
    for k, v in variants.items():
        print(k, round(v["median_s"], 4), round(v["margin_s"], 4),
              f"{100 * v['share_of_total_saving']:.1f}%")


if __name__ == "__main__":
    main()
