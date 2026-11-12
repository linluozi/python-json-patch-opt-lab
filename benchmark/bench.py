#!/usr/bin/env python3
"""Real-corpus benchmark: jsonpatch.JsonPatch.from_diff baseline vs patched.

Every timed sample is written to benchmark/raw/<impl>-<scale>-<i>.json.
Timing uses time.perf_counter(); warmups are never recorded as samples.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
RAW = os.path.join(HERE, "raw")

SCALES = {
    # name: (source fixture, destination fixture)
    "small": ("rds-2014-09-01.json", "rds-2014-10-31.json"),
    "medium": ("ec2-2016-04-01.json", "ec2-2016-09-15.json"),
    "large": ("ec2-2016-09-15.json", "ec2-2016-11-15.json"),
}
IMPLS = {
    "baseline": os.path.join(ROOT, "baseline_src", "jsonpatch.py"),
    "optimized": os.path.join(ROOT, "src", "jsonpatch.py"),
}
WARMUP = 3
REPS = 7


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(impl, scale):
    src_name, dst_name = SCALES[scale]
    src_path, dst_path = os.path.join(FIX, src_name), os.path.join(FIX, dst_name)
    src = json.load(open(src_path, encoding="utf-8"))
    dst = json.load(open(dst_path, encoding="utf-8"))
    mod = load("jsonpatch_" + impl, IMPLS[impl])
    for _ in range(WARMUP):
        mod.JsonPatch.from_diff(src, dst)
    samples = []
    for i in range(REPS):
        t0 = time.perf_counter()
        patch = mod.JsonPatch.from_diff(src, dst)
        elapsed = time.perf_counter() - t0
        record = {
            "impl": impl, "scale": scale, "sample": i, "seconds": elapsed,
            "warmup": WARMUP, "reps": REPS,
            "src": src_name, "dst": dst_name,
            "src_bytes": os.path.getsize(src_path),
            "dst_bytes": os.path.getsize(dst_path),
            "src_sha256": sha256_file(src_path),
            "dst_sha256": sha256_file(dst_path),
            "ops": len(patch.patch),
            "patch_sha256": hashlib.sha256(
                json.dumps(patch.patch, ensure_ascii=False, sort_keys=False)
                .encode("utf-8")).hexdigest(),
            "applies": patch.apply(src) == dst,
        }
        samples.append(record)
        os.makedirs(RAW, exist_ok=True)
        with open(os.path.join(RAW, f"{impl}-{scale}-{i}.json"), "w") as f:
            json.dump(record, f, indent=1)
    return samples


def aggregate(impl, samples):
    out = {}
    for scale in SCALES:
        xs = sorted(s["seconds"] for s in samples if s["scale"] == scale)
        med = statistics.median(xs)
        p25 = xs[len(xs) // 4]
        p75 = xs[(3 * len(xs)) // 4]
        mean = statistics.fmean(xs)
        sd = statistics.pstdev(xs)
        out[scale] = {
            "n": len(xs), "median_s": med, "p25_s": p25, "p75_s": p75,
            "min_s": xs[0], "max_s": xs[-1], "mean_s": mean,
            "rsd_pct": 100.0 * sd / mean if mean else 0.0,
            "src_bytes": samples[0]["src_bytes"],
            "dst_bytes": samples[0]["dst_bytes"],
            "ops": next(s["ops"] for s in samples if s["scale"] == scale),
            "patch_sha256": next(s["patch_sha256"] for s in samples if s["scale"] == scale),
            "applies": all(s["applies"] for s in samples if s["scale"] == scale),
        }
    return out


def memory(impl, scale):
    code = (
        "import importlib.util,json,resource,sys;"
        "spec=importlib.util.spec_from_file_location('m',%r);"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "a=json.load(open(%r));b=json.load(open(%r));"
        "p=m.JsonPatch.from_diff(a,b);"
        "print(len(p.patch),resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)"
    ) % (IMPLS[impl], os.path.join(FIX, SCALES[scale][0]),
         os.path.join(FIX, SCALES[scale][1]))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout.split()
    return {"ops": int(out[0]), "peak_rss_kb": int(out[1])}


def environment():
    cpu = ""
    with open("/proc/cpuinfo") as f:
        for line in f:
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    mem_kb = 0
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal"):
                mem_kb = int(line.split()[1])
                break
    return {
        "cpu": cpu, "mem_total_kb": mem_kb, "cores": os.cpu_count(),
        "python": sys.version, "python_executable": sys.executable,
        "platform": platform.platform(), "warmup": WARMUP, "reps": REPS,
        "timer": "time.perf_counter", "seed": None,
        "seed_note": "benchmark is deterministic; no random input is generated",
        "command": "python3 benchmark/bench.py",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-memory", action="store_true")
    args = ap.parse_args()
    env = environment()
    summary = {}
    for impl in ("baseline", "optimized"):
        samples = []
        for scale in SCALES:
            samples += run(impl, scale)
        agg = aggregate(impl, samples)
        mem = {} if args.skip_memory else {s: memory(impl, s) for s in SCALES}
        doc = {"environment": env, "samples": len(samples), "scales": agg,
               "memory": mem, "implementation": IMPLS[impl]}
        with open(os.path.join(HERE, impl + ".json"), "w") as f:
            json.dump(doc, f, indent=1)
        summary[impl] = doc
        print(impl, {s: round(agg[s]["median_s"], 4) for s in SCALES},
              {s: round(agg[s]["rsd_pct"], 2) for s in SCALES}, mem)
    print("\nscale  baseline_s  optimized_s  speedup")
    for s in SCALES:
        b = summary["baseline"]["scales"][s]["median_s"]
        o = summary["optimized"]["scales"][s]["median_s"]
        print(f"{s:7s} {b:.6f}  {o:.6f}  {b / o:.3f}x")


if __name__ == "__main__":
    main()
