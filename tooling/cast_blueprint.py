#!/usr/bin/env python3
r"""Cast a blueprint pack directory (blueprints/<slug>/) into a publishable manifest.

蓝图目录约定：

  blueprint.json  蓝图真源：kind/boundary/dataSources/agents[]/skills[]/mcpServers
                  /fleet/board/monitoring/acceptance/tags/notes/i18n/_casting
  manifest.json   铸造产物（sibling）：只留市场要的段

本仓的两类源是**不同方言**，同一枚包只走其中一条通道：

  * fleet 模板（lex/wire/... 一舰一目录）→ cast_fleet_template.py
    壳字段住 cast.json，舰结构住 template.json
  * 蓝图（blueprints/<slug>）→ 本脚本
    壳字段（name/description/category/tags/visibility/notes/i18n）住既有
    manifest.json；结构段（agents/skills/mcpServers/fleet）来自 blueprint.json

为什么壳字段留在 manifest 而不进 blueprint：`fleet` 段的 name/oneLine 是**舰队
内部显示名**（岗位看到的队名），市场陈列名是另一层词（如「优化团队」vs 内部
「元优化舰队」）；两者不可互相覆盖。所以本脚本只搬结构段，壳字段一律不动。

用法：
    python3 cast_blueprint.py blueprints/meta-optimizer            # 铸造 + 校验
    python3 cast_blueprint.py blueprints/meta-optimizer --check    # 只对比既有产物
退出码：0=一致/已铸造；1=漂移（--check）或校验失败；2=用法错。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# 结构段：蓝图是唯一真源，逐段照搬。
STRUCT_KEYS = ("agents", "skills", "mcpServers", "fleet")
# 壳字段：由人维护在 manifest.json（市场陈列面），本脚本永不覆盖。
SHELL_KEYS = ("name", "description", "category", "tags", "visibility", "notes", "i18n")
LEDGER_NAME = "README.md"
PACK_ID_RE = re.compile(r"\{\{PACK_ID:([A-Za-z0-9_-]+)\}\}")
ROW_RE = re.compile(r"^\|\s*([a-z0-9][a-z0-9-]+)\s*\|\s*`([A-Za-z0-9_-]{8,})`")
_LEFTOVER = re.compile(r"\{\{PACK_ID:[^}]*\}\}")


def find_repo_root(start: Path) -> Path:
    """按标志文件找仓根，不认目录名（clone 名随人）。"""
    cur = start
    while True:
        if (cur / LEDGER_NAME).exists() and (cur / "scripts" / "check_drift.py").exists():
            return cur
        if cur == cur.parent:
            raise SystemExit(f"找不到 fleet-packs 仓根（从 {start} 上溯）")
        cur = cur.parent


def parse_ledger(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (root / LEDGER_NAME).read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if m:
            out.setdefault(m.group(1), m.group(2))
    return out


def resolve_placeholders(node, ledger: dict[str, str], trail: str = ""):
    """解析 `{{PACK_ID:<名>}}` 为台账里的字面 packId；解析不出来就抛。

    为什么必须在这里做：fleet 通道（cast_fleet_template.py）一直有这一步并在残留时
    直接抛；蓝图通道没有。结果 `shared/meta-grader` 的 manifest 里
    `fleet.members[0].target.packId` 一直是字面 `{{PACK_ID:meta-grader}}`，
    并且**带着这个坏值上架了**——线上「评测舰队」因此永远建不起来
    （preset 目标解析不出），而漂移哨看不见（比对的是仓 vs 市场，两边一样）。
    """
    if isinstance(node, dict):
        return {k: resolve_placeholders(v, ledger, f"{trail}.{k}" if trail else k)
                for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_placeholders(v, ledger, f"{trail}[{i}]") for i, v in enumerate(node)]
    if isinstance(node, str):
        def sub(m: "re.Match[str]") -> str:
            name = m.group(1)
            pid = ledger.get(name)
            if not pid:
                raise SystemExit(f"占位符 {m.group(0)} 在 {trail or '(root)'} 无法解析："
                                 f"台账里没有 '{name}' 的 packId——先上架回填台账再铸")
            return pid
        return PACK_ID_RE.sub(sub, node)
    return node


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def detect_indent(text: str, default: int = 1) -> int:
    """沿用目标文件自己的缩进宽度，别把整份文件重排。

    为什么：本仓的 manifest/blueprint 用 **1 空格**缩进，而 json.dumps 默认的手写值是 2。
    写死 2 的后果是「两个字段变了」在 diff 里看起来像「整份文件变了」（实测 meta-grader：
    2 处语义变化 ⇒ 604 行 diff），下一个人无从判断这次铸造到底动了什么。
    被测出的是实际缩进而不是猜：取第二行（首个字段）行首空白数。
    """
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" ")
        if stripped and stripped != line:
            return len(line) - len(stripped)
    return default


def dump(node, indent: int) -> str:
    return json.dumps(node, ensure_ascii=False, indent=indent) + "\n"


def sync_member_cadence(blueprint: dict) -> list[str]:
    """成员节律一律等于同 ref 的岗位节律。

    为什么会漂：蓝图里 cadence 有两份——agents[].cadence（岗位文档看到的）与
    fleet.members[].cadence（实例化时真正建 cron job 的那份）。改了前者忘了后者，
    上架后人读到的 persona 是新的、机器跑的还是旧的。2026-11-08 实物踩到过：
    三岗 persona 已收窄成分批，成员的 promptTemplate 仍是「逐团队」全量。

    这里做硬同步（不是警告）：ref 与 agent.id 一致即照搬 agent.cadence。真需要
    各成员不同节律时，蓝图里就该写成不同岗位，而不是同一岗两份节律。
    """
    by_id = {a.get("id"): a for a in blueprint.get("agents") or [] if a.get("id")}
    synced = []
    for member in (blueprint.get("fleet") or {}).get("members") or []:
        agent = by_id.get(member.get("ref"))
        if agent is None or agent.get("cadence") is None:
            continue
        if member.get("cadence") != agent["cadence"]:
            member["cadence"] = agent["cadence"]
            synced.append(member["ref"])
    return synced


def cast(blueprint: dict, existing: dict, ledger: dict[str, str]) -> tuple[dict, list[str]]:
    synced = sync_member_cadence(blueprint)
    manifest = dict(existing)
    for key in STRUCT_KEYS:
        if key in blueprint:
            manifest[key] = blueprint[key]
        else:
            manifest.pop(key, None)
    # 结构段里的 `{{PACK_ID:<名>}}` 必须在产物里解析成字面 id（见 resolve_placeholders 的注释）。
    manifest = resolve_placeholders(manifest, ledger)
    if _LEFTOVER.search(json.dumps(manifest, ensure_ascii=False)):
        raise SystemExit("unresolved placeholder survived casting: "
                         + _LEFTOVER.search(json.dumps(manifest, ensure_ascii=False)).group(0))
    return manifest, synced


def validate(manifest_path: Path) -> int:
    """跑放行前同一套校验器（真 validatePackManifest），失败即退出码 1。"""
    script = Path(__file__).resolve().parent / "validate_manifest.py"
    out = subprocess.run(
        [sys.executable, str(script), str(manifest_path)],
        capture_output=True, text=True,
    )
    sys.stdout.write(out.stdout)
    sys.stderr.write(out.stderr)
    return out.returncode


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="cast blueprint.json → manifest.json")
    ap.add_argument("dir", help="blueprint pack directory (e.g. blueprints/meta-optimizer)")
    ap.add_argument("--check", action="store_true", help="compare only, do not write")
    ap.add_argument("--no-validate", action="store_true", help="skip the manifest validator")
    args = ap.parse_args(argv)

    root = Path(args.dir).resolve()
    bp_path, mf_path = root / "blueprint.json", root / "manifest.json"
    for path in (bp_path, mf_path):
        if not path.exists():
            print(f"missing {path}", file=sys.stderr)
            return 2

    blueprint, existing = load(bp_path), load(mf_path)
    bp_indent = detect_indent(bp_path.read_text(encoding="utf-8"))
    mf_indent = detect_indent(mf_path.read_text(encoding="utf-8"))
    ledger = parse_ledger(find_repo_root(root))
    produced, synced = cast(blueprint, existing, ledger)
    if synced:
        print(f"member cadence synced from agent: {', '.join(synced)}")

    if args.check:
        drift = [k for k in set(produced) | set(existing) if produced.get(k) != existing.get(k)]
        if synced or drift:
            print(f"DRIFT {root.name}: {sorted(drift) or ['member cadence']}", file=sys.stderr)
            return 1
        print(f"OK {root.name}: manifest matches blueprint")
        return 0

    # 先留原字节：校验不过就把两个文件都还原回原样（``失败的铸造不留副作用''）。
    prev_bp = bp_path.read_bytes()
    prev_mf = mf_path.read_bytes()

    # 同步结果回写蓝图源：源=产物，否则下一个人改 blueprint 时看到的还是旧节律。
    if synced:
        bp_path.write_text(dump(blueprint, bp_indent), encoding="utf-8")

    changed = [k for k in STRUCT_KEYS if produced.get(k) != existing.get(k)]
    if not changed:
        print(f"no change {root.name}")
    else:
        mf_path.write_text(dump(produced, mf_indent), encoding="utf-8")
        print(f"cast {root.name}: updated {', '.join(changed)}")

    if args.no_validate:
        return 0
    rc = validate(mf_path)
    if rc != 0:
        bp_path.write_bytes(prev_bp)
        mf_path.write_bytes(prev_mf)
        print(f"cast ROLLED BACK -> {root.name}（blueprint 与 manifest 已还原）", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
