"""Claude Code 风格的 9-section summary prompt.

照搬自 references/claude-code-leaked/src/services/compact/prompt.ts
的 BASE_COMPACT_PROMPT, 用于跑 summary 时驱动 LLM 输出结构化摘要.

为什么照搬: 自由 "summarize the conversation" 产出的摘要丢信息很多,
9 section 强制结构 + <analysis>/<summary> 双 block 双输出实测效果好得
多 (analysis 是 drafting scratchpad, 强迫模型先 chronological scan
一遍再写最终 summary, 类似 chain-of-thought). 没必要重新发明轮子, prompt
字面照抄, 只在外层加 "do not call tools" preamble/trailer 防 LLM
误以为 summary 任务是新 turn 还能 tool call.

模板里所有英文都保留 — LLM 读英文 prompt 命中训练分布更好, 翻成中
文反而掉效果。
"""
from __future__ import annotations


# 防 LLM 在 summary turn 里 tool call. Claude Code 实测 Sonnet 4.6+
# 偶尔会 ignore "respond with text" 指令尝试 tool call, 一旦被拒就
# 浪费整 turn. 显式说后果能压下来。
NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""


# Drafting scratchpad — formatCompactSummary 之后会把 <analysis> 整段
# 砍掉。它存在的唯一价值是逼模型按时间线扫一遍, 不直接写最终结论。
DETAILED_ANALYSIS_INSTRUCTION = """Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly."""


BASE_COMPACT_PROMPT = f"""Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

{DETAILED_ANALYSIS_INSTRUCTION}

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages:
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response.
"""


NO_TOOLS_TRAILER = (
    "\n\nREMINDER: Do NOT call any tools. Respond with plain text only — "
    "an <analysis> block followed by a <summary> block. "
    "Tool calls will be rejected and you will fail the task."
)


def build_prompt(items_text: str, custom_instructions: str = "") -> str:
    """拼最终 prompt: preamble + base + items 转录 + custom + trailer.

    items_text 是被合并的 items 序列化后的对话文本 (caller 决定怎么拼,
    一般是 "Role: content" 一行一条). custom_instructions 是从 session
    config / agent 配置带进来的额外指令 (比如"重点关注 typescript 改动"),
    没有就传空串。
    """
    parts: list[str] = [NO_TOOLS_PREAMBLE, BASE_COMPACT_PROMPT]
    parts.append("\n<conversation>\n")
    parts.append(items_text)
    parts.append("\n</conversation>\n")
    if custom_instructions.strip():
        parts.append(f"\nAdditional Instructions:\n{custom_instructions.strip()}\n")
    parts.append(NO_TOOLS_TRAILER)
    return "".join(parts)


def extract_summary(raw: str) -> str:
    """从 LLM 输出里挖出 <summary>...</summary> 内容。

    挖不到就退回原文 (有的模型不老实带 tag, 但内容还在; 总比丢了好).
    <analysis> 段会被直接丢掉 — 那是 scratchpad, 信息价值已经体现在
    <summary> 里了。
    """
    import re

    m = re.search(r"<summary>([\s\S]*?)</summary>", raw)
    if m:
        return m.group(1).strip()
    # 没 tag: 把 analysis 段去掉再返回。
    cleaned = re.sub(r"<analysis>[\s\S]*?</analysis>", "", raw).strip()
    return cleaned or raw.strip()
