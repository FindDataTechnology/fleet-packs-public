#!/usr/bin/env python3
"""可见性与版本判据（openspec: fleet-drift-and-hygiene / fleet-supply-drift-gates）。

查两件本仓看不出来的事：

  ① **官方元线三支必须在公开列表可被发现。** 2026-11-18 用户裁定：优化团队 /
     行业分析团队 / 评测团队必须公开。它们的真因不是漏打包，而是 packs.db 里
     visibility='private'（unlisted=0）被可见性闸挡在 /api/packs 之外——「发版
     成功」与「对外可见」是两条独立路径（publish_pack.py 只写版本行，转公开要
     另打 PUT /api/packs/:id/visibility）。

  ② **仓内 manifest 与市场在册版本的关系可读。** 三值：同版 / 落后（带跨度）/
     市场无此包（区分从未铸造与已撤下）。

## 口径（实测，判之前必须先确认，否则全是假红）

    /api/packs 只返回 public ∧ 非 unlisted
      → 「列表里没有」有三种成因，必须分开报，不能一律说「不存在」：
        · private    可见性闸挡住（对元线判红，对其他包只记录）
        · unlisted   公开但主动下架（同上）
        · 真缺失     从未铸造 / 已删除
      但 API 不暴露后两者的区分（unlisted 行根本不返回），所以对非元线包只记
      「不在公开列表」，不猜成因。要判成因得读 packs.db，那需要集群内访问——
      本脚本保持「干净 clone 可跑」，因此不读库。

  分页必须读完再判：单页上限 50，140 条要 3 页。没读完记「未判完」，退出码 2。

用法：
    python3 fleet-packs/scripts/check_visibility.py                 # 线上一路
    python3 fleet-packs/scripts/check_visibility.py --offline       # 只读仓内，不碰网络
    python3 fleet-packs/scripts/check_visibility.py --self-test     # 六条负向对照臂
退出码：0=全绿；1=有红；2=判不了（市场读不到 / 分页未读完 / 台账读不到）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BASE = "https://facet.finddatatech.cloud"
AUTHOR = "operator@example.com"

ROW_RE = re.compile(r"^\|\s*([a-z0-9][a-z0-9-]+)\s*\|\s*`([A-Za-z0-9_-]{8,})`")

# 官方元线的「slug → packId」。三个 packId 是线上在用的，不是猜的：
# 取自活跃舰队的 fleets.source_template.packId（cell 1da519d4 三支）。
META_LINE = {
    "优化团队":   "gkHE4CsQHC2rEV0ZWziqWg",
    "行业分析团队": "8gqJKlQ7TDLtPgyWGQw0dw",
    "评测团队":   "2uXkSlEByBS7Zzu4JkmdwA",
}


def token() -> str:
    t = os.environ.get("FACET_INTERNAL_TOKEN", "").strip()
    if t:
        return t
    try:
        out = subprocess.run(
            ["kubectl", "-n", "fd-prod", "get", "secret", "facet-secrets",
             "-o", "jsonpath={.data.FACET_INTERNAL_TOKEN}"],
            capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return base64.b64decode(out.stdout.strip()).decode()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _identity() -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"email": AUTHOR, "groups": ["creators", "platform"]}).encode()
    ).decode().rstrip("=")


def fetch_all_public(base: str, tok: str) -> list[dict] | None:
    """翻完所有页，**以访客视角**。

    ⚠️ 必须匿名：2026-11-18 实测同一个 `/api/packs` 两个身份看到不同的数——
    带作者凭据（`x-facet-user`）返回 **158** 条（含 18 条 private），匿名返回
    **140** 条。判据要回答的是「访客能不能发现它」，所以只能匿名读；用作者
    身份读会把 private 包也算成「可见」，得到假绿（本判据第一版就栽在这里：
    对行业分析团队报 ✅，而它在匿名列表里根本不存在）。

    未读完返回 None——**不许把半份列表当全量判**。
    """
    collected: list[dict] = []
    seen: set[str] = set()
    total = None
    for page in range(1, 40):  # 上限 40 页 = 2000 条，够用；超了说明分页语义变了
        url = f"{base.rstrip('/')}/api/packs?limit=50&page={page}"
        # 只带 x-market-token（通道鉴权），**不带 x-facet-user**（身份）。带 token
        # 是因为 facet 前的代理要求它；不带 user 是为了拿访客视角。
        req = urllib.request.Request(url, headers={"x-market-token": tok})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            print(f"  ! HTTP {e.code} 读第 {page} 页", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001
            print(f"  ! 通道异常 读第 {page} 页：{e}", file=sys.stderr)
            return None
        rows = body.get("packs") or []
        total = body.get("total", total)
        for p in rows:
            pid = p.get("id")
            if pid and pid not in seen:
                seen.add(pid)
                collected.append(p)
        if not rows or (total is not None and len(seen) >= total):
            break
    if total is not None and len(seen) < total:
        print(f"  ! 分页未读完：读到 {len(seen)} / total {total}", file=sys.stderr)
        return None
    # 匿名视角下不该出现 private 行；出现了说明身份头漏带，如实报出来而不是静默算绿。
    priv = [p for p in collected if p.get("visibility") == "private"]
    if priv:
        print(f"  ! 匿名列表里出现 {len(priv)} 条 private 行——身份头可能漏带，"
              f"本次读数不可信", file=sys.stderr)
        return None
    return collected


def load_ledger(path: Path) -> dict[str, str]:
    """packId → slug。"""
    return {pid: slug for pid, slug, _v in _load_ledger_rows(path)}


def _load_ledger_rows(path: Path) -> list[tuple[str, str, int | None]]:
    """(slug, packId, 台账版本)。版本列是 `vN`；`—` / `-` / 空 记 None。

    为什么版本从台账读而不是从 manifest 读：**manifest 里没有 version 字段**。
    版本是铸造时由市场分配的（`_casting` 记录铸造来源，不含版本），仓内
    manifest 是「铸造输入」而非「已发布对象」。所以「仓内 vN」的唯一载体重合
    就是台账这一列——它是人维护的，这正是本判据要盯的：台账记的版本与市场
    实况脱节本身就是漂移（`pack-taxonomy-and-naming` 的「改名在跑实例不断链」
    依赖台账可信）。
    """
    if not path.exists():
        return []
    rows: list[tuple[str, str, int | None]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        slug, pid = m.group(1), m.group(2)
        vm = re.search(r"\|\s*v(\d+)\s*\|", line)
        rows.append((slug, pid, int(vm.group(1)) if vm else None))
    return rows


def local_versions(root: Path, ledger: dict[str, str]) -> dict[str, int | None]:
    """packId → 仓内 manifest 声明的版本。

    目录名与台账 slug **不总一致**（台账 `hotel-industry-watch-fleet`，目录
    `hotel/industry-watch-fleet`），所以不能按 slug 直接拼路径。改用：
    先扫全仓 manifest 建「目录名 → 版本」，再对每个台账 slug 试三档匹配
    （原样 / 去掉板块前缀 / 加板块前缀）。匹配不到记 None。
    """
    by_dir: dict[str, int | None] = {}
    for depth in ("*/*", "*/*/*"):
        for p in root.glob(f"{depth}/manifest.json"):
            if "/.git/" in str(p):
                continue
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                by_dir[p.parent.name] = None
                continue
            v = doc.get("version")
            by_dir[p.parent.name] = int(v) if isinstance(v, int) or (
                isinstance(v, str) and v.isdigit()) else None

    out: dict[str, int | None] = {}
    dirs = set(by_dir)
    for pid, slug in ledger.items():
        if slug in dirs:
            out[pid] = by_dir[slug]
            continue
        # 台账 slug 带板块前缀而目录不带（或反之）
        cand = [d for d in dirs if d.endswith("-" + slug) or slug.endswith("-" + d)]
        if len(cand) == 1:
            out[pid] = by_dir[cand[0]]
        else:
            out[pid] = None
    return out


def check(root: Path, market: list[dict] | None, offline: bool = False) -> tuple[int, int]:
    """返回 (红数, 判不了哨兵)。红数 -1 = 判不了。"""
    rows = _load_ledger_rows(root / "README.md")
    if not rows:
        print(f"❌ 台账读不到或为空：{root / 'README.md'}——判不了", file=sys.stderr)
        return (-1, 0)
    ledger = {pid: slug for slug, pid, _v in rows}
    ledger_v = {pid: v for _s, pid, v in rows}

    if not offline and market is None:
        print("❌ 市场读不到（通道/权限/分页未读完）——判不了，不当通过", file=sys.stderr)
        return (-1, 0)

    red = 0
    if not offline:
        listed = {p.get("id"): p for p in (market or [])}

        # ① 元线三支必须可被发现
        print("── 元线可见性 ──")
        for name, pid in META_LINE.items():
            row = listed.get(pid)
            if row is None:
                print(f"❌ 元线「{name}」({pid}) 不在公开列表——三支必须公开（2026-11-18 裁定）")
                red += 1
            else:
                print(f"✅ 元线「{name}」在公开列表：v{row.get('version')} · category={row.get('category')}")

        # ② 台账版本 ↔ 市场版本（三值：同版 / 不一致（带跨度）/ 不在公开列表）
        # ①b 匿名视角自证（finddata-fc 的对偶教训：他那边是「只断言状态码会假绿」，
        #     失败与成功的语义藏在 content-type 与正文字节里；这里同理——带身份读
        #     会让 private 包看起来可见，所以「匿名到底能看到什么」只能靠不含 user
        #     头的请求自证。fetch_all_public 内已带自检，此处把结论打印出来，
        #     让人看到这道自证真的跑了，而不是只信一个 ✅。）
        priv_seen = sum(1 for p in (market or []) if p.get("visibility") == "private")
        print(f"🔎 匿名自证：列表 {len(market or [])} 条，其中 visibility=private {priv_seen} 条"
              + ("（应为 0；非 0 说明身份头漏带，读数不可信）" if priv_seen else "（符合匿名视角）"))

        print("\n── 台账 ↔ 市场版本 ──")
        same = diff = absent = 0
        diff_rows: list[tuple[str, int, int]] = []
        for pid, slug in sorted(ledger.items(), key=lambda kv: kv[1]):
            live = listed.get(pid)
            if live is None:
                absent += 1
                continue
            rv, mv = ledger_v.get(pid), live.get("version")
            if rv is None or mv is None:
                continue
            if rv == mv:
                same += 1
            else:
                diff += 1
                diff_rows.append((slug, rv, mv))
        for slug, rv, mv in sorted(diff_rows, key=lambda x: -abs(x[2] - x[1]))[:15]:
            gap = "落后" if mv > rv else "台账领先"
            print(f"🔶 {slug:30s} 台账 v{rv} · 市场 v{mv}（{gap} {abs(mv - rv)} 版）")
        if len(diff_rows) > 15:
            print(f"   …另有 {len(diff_rows) - 15} 个版本不一致")
        print(f"\n合计：同版 {same} · 版本不一致 {diff} · 不在公开列表 {absent}")
        # 版本落后是「该跟新」的信号，不判红（fleet-owner-iteration：只提示不迁移）。
        # 不在公开列表同理——除元线外，私有/未上架都是合法的发布选择。

    return (red, 0)


# ── 负向对照 ─────────────────────────────────────────────────────────────

def self_test() -> int:
    """判据必须能红。六条臂。"""
    import contextlib
    import io
    import shutil

    def build(root: Path, rows: list[tuple[str, str, int]]) -> None:
        # 台账行的版本列形如 `| slug | `packId` | vN | 说明 |`——判据从这一列读版本
        # （manifest 里没有 version 字段，见 _load_ledger_rows 的说明）。
        lines = ["| pack | packId | 版本 | 说明 |", "|---|---|---|---|"]
        for slug, pid, v in rows:
            lines.append(f"| {slug} | `{pid}` | v{v} | test |")
        (root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    A, B, C = "AAAAAAAAAAAAAAAAAAAAAA", "BBBBBBBBBBBBBBBBBBBBBB", "CCCCCCCCCCCCCCCCCCCCCC"

    def market(entries: list[tuple[str, int]]) -> list[dict]:
        return [{"id": pid, "version": v} for pid, v in entries]

    cases = [
        # (名称, 期望红, 台账行, 市场项, 替换后的 META_LINE)
        # 注意：元线判据对 META_LINE 的每个条目各判一次，所以 fixture 的市场列表
        # 必须覆盖「替换后 META_LINE 里所有 packId」，否则会因别支缺失而多红。
        ("A 元线全在列表 → 绿", 0, [(A, 1)],
         [(A, 1), (META_LINE["行业分析团队"], 1), (META_LINE["评测团队"], 1)],
         {"优化团队": A}),
        ("B 元线不在列表 → 红", 1, [(A, 1)],
         [(META_LINE["行业分析团队"], 1), (META_LINE["评测团队"], 1)],
         {"优化团队": A}),
        ("C 唯一一支缺失 → 红 1", 1, [(A, 1)], [], {"优化团队": A}),
    ]
    passed = 0
    for name, want_red, ledger_rows, mkt, meta in cases:
        tmp = Path(tempfile.mkdtemp(prefix="vis-"))
        try:
            saved = dict(META_LINE)
            META_LINE.clear(); META_LINE.update(meta)
            try:
                build(tmp, [(f"p{i}", pid, v) for i, (pid, v) in enumerate(ledger_rows)])
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    red, _ = check(tmp, market(mkt))
            finally:
                META_LINE.clear(); META_LINE.update(saved)
            ok = red == want_red
            passed += ok
            print(f"  {'✅' if ok else '❌'} {name}（红 {red}/{want_red}）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # D：版本落后只提示不判红（fleet-owner-iteration：跟新只提示不迁移）
    tmp = Path(tempfile.mkdtemp(prefix="vis-"))
    try:
        saved = dict(META_LINE); META_LINE.clear(); META_LINE.update({"优化团队": A})
        try:
            build(tmp, [("opt", A, 2)])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                red, _ = check(tmp, market([(A, 9)]))
            out = buf.getvalue()
            ok = red == 0 and "落后 7 版" in out
            passed += ok
            print(f"  {'✅' if ok else '❌'} D 版本落后 → 只提示不判红（红 {red}，{'含' if '落后' in out else '缺'}跨度）")
        finally:
            META_LINE.clear(); META_LINE.update(saved)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # E：市场读不到必须判不了，不能算绿
    tmp = Path(tempfile.mkdtemp(prefix="vis-"))
    try:
        build(tmp, [("opt", A, 1)])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            red, _ = check(tmp, None)
        ok = red == -1
        passed += ok
        print(f"  {'✅' if ok else '❌'} E 市场读不到 → 判不了（哨兵 {red}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # F：台账读不到必须判不了
    tmp = Path(tempfile.mkdtemp(prefix="vis-"))
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            red, _ = check(tmp, [])
        ok = red == -1
        passed += ok
        print(f"  {'✅' if ok else '❌'} F 台账缺失 → 判不了（哨兵 {red}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = 6
    print(f"\nself-test {'绿' if passed == total else '红'} ({passed}/{total})")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--base", default=os.environ.get("FACET_BASE", DEFAULT_BASE))
    ap.add_argument("--offline", action="store_true", help="只读仓内，不碰网络")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"❌ 仓根不存在：{root}", file=sys.stderr)
        return 2
    market = None
    if not args.offline:
        tok = token()
        if not tok:
            print("❌ 取不到 facet token（--offline 可跳过市场检查）——判不了", file=sys.stderr)
            return 2
        market = fetch_all_public(args.base, tok)
    red, _ = check(root, market, args.offline)
    if red == -1:
        return 2
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
