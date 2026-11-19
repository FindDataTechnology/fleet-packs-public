#!/usr/bin/env python3
"""技能正文红线扫描（enable-constructor-skill-authoring W4 / tasks 2.1-2.3）。

扫描对象：蓝图与 manifest 里的**内容层文本**——`skills[].content`、`skills[].description`、
`agents[].persona`。结构字段（allowTools / ref / cron 等）不在本扫描范围。

红线闭集两类（命中即阻断铸造，退出码 1）：

  泄密类
    L1 internal_path   内部仓路径（fleet-packs/ openspec/ paas/ /Users/ .agents/skills）
    L2 change_id       内部 change ID 形态（add-... / awaken-... 三段以上 kebab）
    L3 credential      凭据键赋值形态与长随机串
    L4 internal_host   内网域与私网/回环 IP

  越界指令类（要求共现，不做单词匹配——正常纪律词不误报）
    X1 bypass_approval 无需/跳过/绕过 + 批准/审批/拍板（6 字内）
    X2 hide_report     不要/禁止 + 上报/报数/汇报；隐藏/隐瞒 + 失败/错误/异常
    X3 override_discipline 忽略/无视/覆盖 + 纪律/规则/约束/指令

用法：
    python3 fleet-packs/scripts/skill_redline_scan.py fleet-packs/blueprints/**/blueprint.json
    python3 fleet-packs/scripts/skill_redline_scan.py --json <file> [...]
    python3 fleet-packs/scripts/skill_redline_scan.py --dir fleet-packs   # 递归扫 manifest/blueprint

作为模块：`from skill_redline_scan import scan_document, scan_text`
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

# ── 泄密类 ────────────────────────────────────────────────────────────────────

INTERNAL_PATH_PATTERNS = [
    r"fleet-packs/",
    r"openspec/",
    r"(?<![\w-])paas/",
    r"/Users/",
    r"\.agents/skills",
]

CHANGE_ID_RE = re.compile(
    r"(?<![\w-])(?:add|awaken|serve|enable|remove|move|fold|define|modernize|fix)"
    r"(?:-[a-z0-9]+){2,}(?![\w-])"
)

CREDENTIAL_RE = re.compile(
    r"(?:token|secret|password|passwd|api[_-]?key|access[_-]?key)\s*[:=]\s*\S",
    re.IGNORECASE,
)
# 长随机串：只认「像密钥」的形状——已知前缀，或纯字母数字且大小写混排。
# 刻意排除带下划线/双下划线的标识符（MCP 工具名、snake_case 字段名是正常内容）。
KEY_PREFIX_RE = re.compile(r"(?:sk-|pk-|ghp_|gho_|xox[bpas]-|AKIA)[A-Za-z0-9_-]{16,}")
PURE_ALNUM_RE = re.compile(r"(?<![\w-])[A-Za-z0-9]{32,}(?![\w-])")

INTERNAL_HOST_RE = re.compile(
    r"(?:\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
    r"|\b127\.0\.0\.1\b"
    r"|\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"
    r"|\b192\.168\.\d{1,3}\.\d{1,3}\b"
    r"|\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"
    r"|\.internal\b"
    r"|\.cluster\.local\b"
    r"|\b[a-z0-9-]+\.svc\b)",
    re.IGNORECASE,
)

# ── 越界指令类（共现才命中，且带否定护栏）──────────────────────────────────
#
# 否定护栏：命中句前 4 字内出现 不/无/未/禁/零/没/否/绝 时视为「合规陈述」——
# 「不存在绕过确认门的直写路径」「不跳过确认门直接写」是正面纪律，不是越界指令。
NEGATION_RE = re.compile(r"[不无未禁零没否绝]")

BYPASS_APPROVAL_RE = re.compile(
    r"(?:无需|无须|不用|不必|跳过|绕过|忽略|免去)[^\n。；]{0,6}(?:批准|审批|拍板|确认)"
    r"|直接(?:发布|上架|写库|写入|执行)"
)
HIDE_REPORT_RE = re.compile(
    r"(?:不要|无需|无须|不必|禁止)[^\n。；]{0,4}(?:上报|报数|汇报)"
    r"|(?:隐藏|隐瞒|美化)(?:失败|错误|异常|未取得)"
)
OVERRIDE_DISCIPLINE_RE = re.compile(
    r"(?:忽略|无视)[^\n。；]{0,8}(?:纪律|规则|约束|指令|提示词|系统提示)"
    r"|覆盖(?:纪律|指令|提示词|系统提示)"
)

RULES: list[tuple[str, str, re.Pattern[str], str]] = [
    # (rule id, 类别, pattern, 修法)
    ("L1", "泄密类", re.compile("|".join(INTERNAL_PATH_PATTERNS)),
     "改为业务语义描述（如「本仓」「平台侧」），不写内部路径"),
    ("L2", "泄密类", CHANGE_ID_RE,
     "删去 change ID，改写成需求语义（如「本轮改造」）"),
    ("L3", "泄密类", CREDENTIAL_RE,
     "删除凭据键与取值；凭据只走连接器配置，永不出现在技能正文"),
    ("L4", "泄密类", INTERNAL_HOST_RE,
     "删除内网地址；需要指代服务时用 registry 在册名"),
    ("X1", "越界指令", BYPASS_APPROVAL_RE,
     "改写为「须经主人批准后由动作通道执行」——红线动作永远人工"),
    ("X2", "越界指令", HIDE_REPORT_RE,
     "改写为「缺位如实报未取得，不编造」——报数如实是硬纪律"),
    ("X3", "越界指令", OVERRIDE_DISCIPLINE_RE,
     "删除覆盖类指令；纪律只可引用不可覆盖"),
]

SCAN_FIELDS = ("content", "persona", "description")


@dataclass(frozen=True)
class Hit:
    file: str
    field: str          # skills[0].content / agents[1].persona
    name: str           # 技能名或岗位名（无则空）
    rule: str
    category: str
    line: int
    snippet: str
    fix: str

    def render(self) -> str:
        return (
            f"{self.file}: {self.field}"
            + (f"「{self.name}」" if self.name else "")
            + f" 第 {self.line} 行  [{self.rule} {self.category}] {self.snippet}\n"
            + f"    修法：{self.fix}"
        )


def _snippet(line: str) -> str:
    s = line.strip()
    return s[:77] + "…" if len(s) > 80 else s


def scan_text(text: str, *, file: str, field: str, name: str = "") -> list[Hit]:
    hits: list[Hit] = []
    if not isinstance(text, str) or not text:
        return hits
    for idx, line in enumerate(text.splitlines(), start=1):
        flagged_l3 = False
        for rule, category, pattern, fix in RULES:
            m = pattern.search(line)
            if not m:
                continue
            if category == "越界指令" and _negated(line, m.start()):
                continue  # 「不存在绕过确认门」「不跳过确认门」= 合规陈述
            flagged_l3 = flagged_l3 or rule == "L3"
            hits.append(Hit(file, field, name, rule, category, idx, _snippet(line), fix))
        if not flagged_l3 and _secret_shaped(line):
            hits.append(Hit(file, field, name, "L3", "泄密类", idx, _snippet(line),
                            "删除疑似凭据/密钥的长随机串"))
    return hits


def _negated(line: str, start: int) -> bool:
    """命中点前 10 字内的否定词把越界指令句翻成合规陈述。

    窗口取 10 是实测值：「工具面不提供任何绕过确认车道」的「不」在命中点前 8 字。
    """
    return bool(NEGATION_RE.search(line[max(0, start - 10):start]))


def _secret_shaped(line: str) -> bool:
    """密钥形态：已知前缀的 key，或纯字母数字的大小写混排长串。

    带下划线/双下划线的标识符不算（MCP 工具名与 snake_case 字段名是正常内容）。
    """
    if KEY_PREFIX_RE.search(line):
        return True
    for m in PURE_ALNUM_RE.finditer(line):
        token = m.group(0)
        if any(c.isdigit() for c in token) and any(c.isupper() for c in token) and any(c.islower() for c in token):
            return True
    return False


def scan_document(doc: dict, *, file: str = "<memory>") -> list[Hit]:
    hits: list[Hit] = []
    for i, skill in enumerate(doc.get("skills") or []):
        if not isinstance(skill, dict):
            continue
        name = str(skill.get("name") or "")
        for fld in ("content", "description"):
            val = skill.get(fld)
            hits.extend(scan_text(val, file=file, field=f"skills[{i}].{fld}", name=name))
    for i, agent in enumerate(doc.get("agents") or []):
        if not isinstance(agent, dict):
            continue
        name = str(agent.get("name") or agent.get("id") or "")
        hits.extend(scan_text(agent.get("persona"), file=file, field=f"agents[{i}].persona", name=name))
    return hits


def iter_documents(paths: list[Path]):
    for p in paths:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - 扫描器要把坏文件如实报出来
            print(f"! 跳过无法解析的文件 {p}: {exc}", file=sys.stderr)
            continue
        if isinstance(doc, (dict, list)):
            docs = doc if isinstance(doc, list) else [doc]
            for d in docs:
                if isinstance(d, dict):
                    yield p, d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="技能正文红线扫描")
    ap.add_argument("paths", nargs="*", help="蓝图或 manifest JSON 文件；'-' 表示从 stdin 读")
    ap.add_argument("--dir", action="append", default=[], help="递归扫描目录下的 *.json")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    targets: list[Path] = [Path(p) for p in args.paths if p != "-"]
    docs: list[tuple[str, dict]] = []
    if "-" in args.paths:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except Exception as exc:  # noqa: BLE001
            print(f"! stdin 不是合法 JSON: {exc}", file=sys.stderr)
            return 2
        for d in payload if isinstance(payload, list) else [payload]:
            if isinstance(d, dict):
                docs.append(("<stdin>", d))
    for d in args.dir:
        for p in Path(d).rglob("*.json"):
            if "node_modules" in p.parts:
                continue
            targets.append(p)

    if not targets and not docs:
        ap.error("需要至少一个文件，或用 --dir 指定目录")

    hits: list[Hit] = []
    scanned = 0
    for label, doc in docs:
        if not isinstance(doc.get("skills"), list) and not isinstance(doc.get("agents"), list):
            continue
        scanned += 1
        hits.extend(scan_document(doc, file=label))
    for path, doc in iter_documents(targets):
        if not isinstance(doc.get("skills"), list) and not isinstance(doc.get("agents"), list):
            continue
        scanned += 1
        hits.extend(scan_document(doc, file=str(path)))

    if args.json:
        print(json.dumps(
            {"scanned": scanned, "hits": [asdict(h) for h in hits]},
            ensure_ascii=False, indent=2,
        ))
    else:
        for h in hits:
            print(h.render())
        print(f"\n扫描 {scanned} 份文档，命中 {len(hits)} 条。")
        if not hits:
            print("通过：无红线命中。")

    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
