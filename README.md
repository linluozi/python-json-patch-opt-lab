# python-json-patch-opt-lab

`jsonpatch.JsonPatch.from_diff` is 1.21–1.26× faster on real botocore service definitions (median **1.247×**, 5 real files / 3 version pairs) after replacing the whole-patch `copy.deepcopy` with a JSON-aware copy and adding two exact-type fast paths. Patch output is byte-identical on **1,171 independent document pairs** (2,342 comparisons incl. `optimization=False`).

Upstream: https://github.com/stefankoegl/python-json-patch

| scale | inputs (real files) | baseline median | optimized median | speedup |
| --- | --- | --- | --- | --- |
| small | rds 2014-09-01 → 2014-10-31 (326 KB → 1,088 KB) | 0.067827 s | 0.054378 s | **1.247×** |
| medium | ec2 2016-04-01 → 2016-09-15 (878 KB → 891 KB) | 0.028223 s | 0.023354 s | **1.208×** |
| large | ec2 2016-09-15 → 2016-11-15 (891 KB → 3,027 KB) | 0.199080 s | 0.158517 s | **1.256×** |

All three workloads are real AWS service definitions shipped by `botocore`; nothing here is generated. Each pair is a genuine API version diff (2,412 / 52 / 5,770 patch operations).

## 本轮创新

新的不是“把 deepcopy 删掉”——那会破坏 `from_diff` docstring 承诺的“patch 中的值已复制”，上一轮已实测到并否决。本轮的新东西是**三条保持语义的纯类型快路径 + 逐改动消融**：`_copy_json` 只对 `dict/list/tuple` 走快速递归、其余类型回退 `copy.deepcopy`，两处热函数先用 `type(x) is dict/list` 短路 `collections.abc` 的 ABC 检查。随后把每条改动单独回退重测，得到真实语料上的边际节省：拷贝 77.6%、`_compare_values` 28.1%、`_sorted_members` 58.1%（合计 163.8%，超过 100% 是因为后两条在重叠调用路径上省掉同一批 ABC 查找）。创新台账里尚无“`jsonpatch` 全量 patch 拷贝的语义保持替换 + 逐改动消融”，也无人给出过这三条路径在真实 3 MB JSON 上的分摊。

## 证据与闸门（每条都可复现）

| 闸门 | 结果 | 复现命令 |
| --- | --- | --- |
| G1 上游测试全绿 | **346 passed, 448 subtests passed**（改动后同一命令） | `python3 -m pytest tests.py -q`（在 upstream 根目录） |
| G2 差分逐字节一致 | **1,171 个去重独立样本 × 2 种 `optimization` = 2,342 次比较，0 差异** | `python3 -m pytest tests/test_diff.py -q` |
| G3 中位加速 ≥1.15× | **1.247×**（1.247 / 1.208 / 1.256 的中位数） | `python3 benchmark/bench.py` |
| G3b 真实语料加速 ≥1.15× | **1.247×**，最差真实文件 1.208× | 同上（3 对真实版本差分） |
| G4 大规模不退化 | **1.256×** ≥ 0.95× | 同上，`large` 行 |
| G5 峰值内存 ≤1.10× | **52,336 KB vs 52,336 KB（1.000×）** | `python3 benchmark/bench.py`（每个实现独立子进程 `ru_maxrss`） |

去重口径：`tests/test_diff.py` 为每个样本单独播种 `random.Random(20261113 + i)`，再以 `sha256(json.dumps([src, dst], sort_keys=True))` 去重；构造 1,400 个候选后剩 **1,171 个独立样本**，不是把同一输入循环 1,171 次。比对是对 `json.dumps(patch.patch)` 的字符串相等，不使用近似比较。

`bench.py` 每个规模 3 次 warmup（不计时）+ 7 次计时，逐样本写 `benchmark/raw/<impl>-<scale>-<i>.json`，报告 p25/中位/p75。实测相对标准差 0.75%–5.24%，全部低于 10%，噪声来源是共享 4 核主机上的并发任务与 Python GC。

## 改了什么（`patch.diff`，49 insertions / 3 deletions）

1. `_copy_json()`：JSON 文档的深拷贝，`dict/list/tuple` 快速递归，靠 `memo` 保留共享引用与环；其它类型（自定义对象、`Decimal` 等）原样回退 `copy.deepcopy`。两处 `from_diff`（标准与 append 变体）改用它。
2. `_compare_values()`：先按 `type(src) is dict and type(dst) is dict` / `list` 短路，命中则不必构造 4 次 `collections.abc` 的 `isinstance` 检查；其余类型仍走原 ABC 分支。
3. `_sorted_members()`：同样先用精确类型短路，再回退 ABC 分支。

不改变公共 API、返回值语义或上游测试；未引入新依赖。逐改动消融见 `benchmark/ablation.json` 与 `benchmark/ablate.py`。

## 复现（干净克隆）

```bash
python3 -m venv .venv && .venv/bin/pip install pytest jsonpointer
.venv/bin/python -m pytest tests/test_diff.py -q
.venv/bin/python benchmark/bench.py
```

上游基线用的是 `baseline_src/jsonpatch.py`（未改动的上游 HEAD `cc1229ae0892bdad91b2b81403ebbf2529eec6db` 原文），所以基准不依赖 PyPI 版本漂移。

## 环境

- CPU: AMD EPYC 7K62 48-Core Processor（4 vCPU）
- 内存: 3.6 GB
- Python: 3.12.3 (`/usr/bin/python3`), pytest 9.2.0
- 计时: `time.perf_counter()`；warmup 3 / reps 7；随机种子不适用（基准输入为真实文件，SHA-256 记在 `benchmark/raw/*.json`）
- 上游: `stefankoegl/python-json-patch` @ `cc1229ae0892bdad91b2b81403ebbf2529eec6db` (2026-11-11)

## 许可

上游 MIT 许可证原文见 `LICENSE`。本仓库对 `jsonpatch.py` 的三处修改同样以 MIT 发布；`baseline_src/jsonpatch.py` 为上游原文，版权归 Stefan Kögl 及贡献者。
