"""What the three integrity checks share: findings, the status they decide, the
instruction-like phrases a hidden prompt uses, and the Chinese report. Plain code."""

import json
import math
import re
from pathlib import Path

# Phrases that address a model reading the paper rather than a human reader: the hidden
# prompts PhantomLint and hiddenprompt-scan look for, in English and Chinese.
INSTRUCTION = re.compile(
    r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|preceding|other)"
    r"\s+(?:instructions?|prompts?|directions?|guidelines?|reviews?)"
    r"|\bgive\s+(?:this\s+paper\s+|it\s+|a\s+|an\s+)?(?:very\s+|highly\s+|strongly\s+)?(?:positive|favou?rable|high|good|"
    r"excellent|glowing)\s+(?:review|score|rating|evaluation|assessment)"
    r"|\bas\s+an?\s+(?:\w+\s+)?(?:reviewer|referee|llm|ai|language\s+model|assistant)\b[^.]{0,40}?\byou\s+(?:must|should|will|need)"
    r"|\b(?:llm|ai|gpt|chatgpt|claude|gemini|language[- ]model)[- ](?:reviewers?|assistants?|judges?)\b"
    r"|\b(?:if\s+you\s+are|for)\s+an?\s+(?:llm|ai|large\s+language\s+model|language\s+model)\b"
    r"|\brecommend\s+(?:this\s+paper\s+for\s+)?accept(?:ance|ing)?\b"
    r"|\bdo\s+not\s+(?:highlight|mention|point\s+out)\s+(?:any\s+)?(?:negatives?|weakness(?:es)?|flaws?|limitations?)"
    r"|\b(?:only\s+)?output\s+(?:a\s+)?positive\b|\brate\s+this\s+paper\s+(?:highly|as)\b"
    r"|忽略(?:之前|以上|先前|前面|上述|此前)的?(?:所有|全部)?(?:指令|指示|提示|要求|说明)"
    r"|(?:给出?|给予|写)(?:一个|一份)?(?:非常|高度)?(?:正面|积极|正向|好|高|优秀)的?(?:评价|评审|审稿意见|评分|分数)"
    r"|作为(?:一名|一个)?(?:审稿人|评审|评审人|大模型|AI|人工智能|语言模型)[^。]{0,20}?你(?:必须|应该|应当|需要)"
    r"|(?:大模型|AI|人工智能|语言模型)(?:审稿人|评审)"
    r"|(?:推荐|建议)(?:接收|录用|接受)(?:这篇|本文|该)?"
    r"|不要(?:提及|指出|强调)(?:任何)?(?:缺点|不足|弱点|问题|局限)", re.I)


def instruction_like(text: str) -> str:
    """The first instruction-like phrase in text, or ""."""
    m = INSTRUCTION.search(" ".join(text.split()))
    return m.group(0) if m else ""


def finding(kind: str, level: str, where: str, problem: str, fix: str, text: str = "", **extra) -> dict:
    return {"kind": kind, "level": level, "where": where, "problem": problem, "fix": fix,
            "text": cut(text), **extra}


def cut(text: str, n: int = 300) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n] + "…"


def status(findings: list[dict]) -> str:
    if any(f["level"] == "error" for f in findings):
        return "FAIL"
    return "PASS_WITH_NOTES" if findings else "PASS"


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The Wilson score interval for a proportion (95% by default)."""
    if n <= 0:
        return 0.0, 1.0
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


TITLES = {"PASS": "通过", "PASS_WITH_NOTES": "通过，有提示", "FAIL": "不通过"}


def report(title: str, summary: str, findings: list[dict], extra: list[str] | None = None, status_: str = "") -> str:
    lines = [f"# {title}：{TITLES.get(status_ or status(findings), status_)}", "", summary, ""]
    for level, name in (("error", "必须处理"), ("note", "提示")):
        rows = [f for f in findings if f["level"] == level]
        if rows:
            lines += [f"## {name}（{len(rows)}）", "", "| 类型 | 位置 | 问题 | 内容 | 修法 |", "|---|---|---|---|---|"]
            lines += [f"| {f['kind']} | `{cell(f['where'])}` | {cell(f['problem'])} | {cell(f.get('text', ''))} | "
                      f"{cell(f['fix'])} |" for f in rows]
            lines.append("")
    if not findings:
        lines += ["没有发现问题。", ""]
    lines += extra or []
    return "\n".join(lines).rstrip() + "\n"


def cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
