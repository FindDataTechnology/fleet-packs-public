#!/usr/bin/env python3
"""E4 回放对齐（openspec: add-meta-fleet-line / §6.7）。

拿一份**真实的、脱敏的**回合语料（`fixtures/turn-replay.jsonl`，来自 cell 1da519d4
的 7360 条 member_turn_events），把运维岗的盘账判据实现成纯函数跑一遍，再与
SQL 算出的真值（`fixtures/turn-replay-truth.json`）逐舰队、逐岗位对拍。

为什么需要这一层：E3 的假宇宙证明「断言能红」，但它证明不了**判据本身写对了**。
真值由 SQL 算、不由模型自评，所以这一层是整套证据里唯一不依赖任何人打分的 gold。
仓内纪律：「拿自己的输出当基准＝自己判自己的卷」。

三条判据（与 fixtures/turn-replay-truth.json 的 meta.caliber 逐字对应）：
  应报 = 该舰队窗口内至少 1 个非 skipped 回合的岗位
  缺报 = 应报岗位中，最后一个回合 status != ok
  连败 = 应报岗位中，末尾连续 failed/interrupted 回合数 >= 2

用法：
    python3 fleet-packs/scripts/check_replay_alignment.py
    python3 fleet-packs/scripts/check_replay_alignment.py --max-diff 0   # 收紧到零差异
退出码：0=对齐（或差异在豁免表内）；1=有未豁免差异；2=语料/真值读不到。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "scripts" / "fixtures"
CORPUS = FIXTURES / "turn-replay.jsonl"
TRUTH = FIXTURES / "turn-replay-truth.json"

# 已知且已核实的豁免：语料与真值在同一份 SQL 口径下应当零差异。
# 留空表示「不接受任何解释性差异」——填条目必须带理由与复核日期。
EXEMPTIONS: dict[str, str] = {}


def load_corpus(path: Path) -> dict[str, dict[str, list[dict]]]:
    by_fleet: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            by_fleet[r["f"]][r["m"]].append(r)
    return by_fleet


def verdict_for(turns: list[dict]) -> dict:
    """运维岗的判据（纯函数）。与 SQL 真值同口径，任何偏离都要能被解释。"""
    effective = [t for t in turns if t.get("st") != "skipped"]
    if not effective:
        return {"should": False, "missing": False, "failing": False}
    last = effective[-1]
    missing = last.get("st") != "ok"
    streak = 0
    for t in reversed(effective):
        if t.get("st") in ("failed", "interrupted"):
            streak += 1
        else:
            break
    return {"should": True, "missing": missing, "failing": streak >= 2}


def align(corpus: dict, truth: dict) -> tuple[list[dict], dict]:
    """返回 (差异清单, 统计)。差异按 (舰队, 岗位, 项) 逐条列出。"""
    diffs: list[dict] = []
    stats = {"fleets": 0, "members": 0, "should": 0, "missing": 0, "failing": 0, "compared": 0}

    truth_fleets = truth.get("fleets") or {}
    for fleet, members in sorted(corpus.items()):
        expected = truth_fleets.get(fleet)
        if expected is None:
            diffs.append({"fleet": fleet, "member": "-", "field": "fleet",
                          "expected": "(not-in-truth)", "got": "present"})
            continue
        stats["fleets"] += 1
        exp_should = set(expected.get("shouldReport") or [])
        exp_missing = set(expected.get("missing") or [])
        exp_failing = set(expected.get("failing") or [])

        got_should, got_missing, got_failing = set(), set(), set()
        for member, turns in members.items():
            v = verdict_for(turns)
            stats["members"] += 1
            stats["compared"] += 1
            if v["should"]:
                got_should.add(member)
            if v["missing"]:
                got_missing.add(member)
            if v["failing"]:
                got_failing.add(member)

        for field, exp, got in (("shouldReport", exp_should, got_should),
                                ("missing", exp_missing, got_missing),
                                ("failing", exp_failing, got_failing)):
            for m in sorted(exp - got):
                diffs.append({"fleet": fleet, "member": m, "field": field,
                              "expected": True, "got": False})
            for m in sorted(got - exp):
                diffs.append({"fleet": fleet, "member": m, "field": field,
                              "expected": False, "got": True})
        stats["should"] += len(exp_should)
        stats["missing"] += len(exp_missing)
        stats["failing"] += len(exp_failing)

    # 真值里有、语料里没有的舰队
    for fleet in sorted(set(truth_fleets) - set(corpus)):
        diffs.append({"fleet": fleet, "member": "-", "field": "fleet",
                      "expected": "present", "got": "(not-in-corpus)"})
    return diffs, stats


def self_test() -> int:
    """证明对齐器能红。

    为什么必须有这一臂：干净语料跑出 0 差异**不等于**对齐器有效——判据函数与导出 SQL
    是我按同一份口径写的两份实现，0 差异只证明「口径转录正确」。若对拍器本身瞎了，
    它同样会打印 0 差异。所以必须注入两种错，看它是否各自判红。
    """
    import copy

    if not CORPUS.exists() or not TRUTH.exists():
        print(f"✗ 语料或真值缺失：{CORPUS.name} / {TRUTH.name}", file=sys.stderr)
        return 2

    corpus = load_corpus(CORPUS)
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))
    arms: list[tuple[str, dict, dict, str]] = []

    # ① 干净基线必须绿
    arms.append(("干净语料必须零差异", corpus, truth, ""))

    # ② 真值错：把某舰队的首个应报岗位从缺报里删掉 → 必须红
    t2 = copy.deepcopy(truth)
    victim = next((f for f, v in t2["fleets"].items() if v.get("missing")), None)
    if victim:
        t2["fleets"][victim]["missing"] = t2["fleets"][victim]["missing"][1:]
        arms.append((f"真值少报一个缺报岗位（{victim[:12]}）必须红", corpus, t2, "must-red"))

    # ③ 语料错：把某岗位最后一个 failed 回合改成 ok → 必须红
    c2 = {f: {m: [dict(t) for t in ts] for m, ts in ms.items()} for f, ms in corpus.items()}
    flipped = False
    for f, ms in c2.items():
        for m, ts in ms.items():
            eff = [t for t in ts if t.get("st") != "skipped"]
            if eff and eff[-1].get("st") == "failed":
                eff[-1]["st"] = "ok"
                flipped = True
                break
        if flipped:
            break
    if flipped:
        arms.append(("把末尾 failed 改成 ok（少算一个缺报）必须红", c2, truth, "must-red"))

    passed = 0
    for label, c, t, expect in arms:
        diffs, _ = align(c, t)
        if expect == "":
            ok = len(diffs) == 0
            detail = "0 差异" if ok else f"{len(diffs)} 条差异"
        else:
            ok = len(diffs) > 0
            detail = (f"{len(diffs)} 条差异（如 {diffs[0]['fleet'][:10]}:"
                      f"{diffs[0]['member'][:10]}:{diffs[0]['field']}）") if ok else "未判红"
        print(f"  {'PASS' if ok else 'FAIL'}  {label} — {detail}")
        passed += 1 if ok else 0

    print(f"self-test {'绿' if passed == len(arms) else '红'} ({passed}/{len(arms)})")
    return 0 if passed == len(arms) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E4 回放对齐：运维判据 vs SQL 真值")
    ap.add_argument("--max-diff", type=int, default=0,
                    help="允许的未豁免差异上限（默认 0）")
    ap.add_argument("--show", type=int, default=20, help="最多打印多少条差异")
    ap.add_argument("--self-test", action="store_true", help="证明对拍器能红")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    if not CORPUS.exists() or not TRUTH.exists():
        print(f"✗ 语料或真值缺失：{CORPUS.name} / {TRUTH.name}", file=sys.stderr)
        return 2

    corpus = load_corpus(CORPUS)
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))
    diffs, stats = align(corpus, truth)

    unwaived = [d for d in diffs if f"{d['fleet']}:{d['member']}:{d['field']}" not in EXEMPTIONS]
    waived = [d for d in diffs if f"{d['fleet']}:{d['member']}:{d['field']}" in EXEMPTIONS]

    print("语料：", CORPUS.relative_to(REPO))
    print(f"  舰队 {stats['fleets']} / 岗位 {stats['members']} / 对比 {stats['compared']}")
    print(f"  真值口径：应报 {stats['should']} · 缺报 {stats['missing']} · 连败 {stats['failing']}")
    print(f"  差异 {len(diffs)}（未豁免 {len(unwaived)}，豁免 {len(waived)}）")

    for d in unwaived[: args.show]:
        print(f"    ✗ {d['fleet'][:18]} {d['member'][:18]} {d['field']}: "
              f"SQL={d['expected']} 判据={d['got']}")
    if waived:
        for d in waived[: args.show]:
            key = f"{d['fleet']}:{d['member']}:{d['field']}"
            print(f"    ~ 豁免 {d['fleet'][:18]} {d['member'][:18]} {d['field']} — {EXEMPTIONS[key]}")

    ok = len(unwaived) <= args.max_diff
    print(f"replay-alignment {'绿' if ok else '红'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
