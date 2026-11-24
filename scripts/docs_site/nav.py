"""Build the left-sidebar navigation tree from the docs/ directory layout.

The tree mirrors the on-disk folder structure. Each directory becomes a group;
its title comes from that directory's README.md H1 if present, else a prettified
folder name. Within a group, README.md is pinned first, the rest sort by name.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Folders that are never part of the docs site.
EXCLUDE_DIRS = {"_site", "_site.tmp", "_site.old", "_static_root",
                "images", "node_modules"}

# Top-level tabs. Each top-level directory under docs/ is one tab in the top
# navbar; the sidebar only shows the current tab's tree. Order here is the
# navbar order. (dir name -> (中文 label, English label))
TABS: dict[str, tuple[str, str]] = {  # dir -> (English label, 中文 label)
    "start":        ("Get started", "开始使用"),
    "install":      ("Install", "安装"),
    "capabilities": ("Capabilities", "能力"),
    "interfaces":   ("Interfaces", "界面"),
    "models":       ("Models", "模型"),
    "integrations": ("Integrations", "集成"),
    "server":       ("Server & Ops", "服务与运维"),
    "reference":    ("Reference", "参考"),
    # Virtual tab: no docs/design/ directory — pages under reference/design/
    # (the engineering-notes archive) are routed here by tab_of(), so the
    # archive gets a first-class navbar tab without moving 130+ file pairs.
    "design":       ("Design", "设计"),
}
# Loose files directly under docs/ belong to a tab too.
ROOT_PAGE_TAB = {"README.html": "start"}
# A top-level dir not listed in TABS falls back to the reference tab, so a
# stray folder degrades to "filed under Reference" instead of vanishing.
FALLBACK_TAB = "reference"

# Display-name overrides for pages whose H1 doesn't make a good sidebar label.
ROOT_PAGE_GROUPS: dict[str, tuple[str, str]] = {
    "README.html": ("Overview", "start"),
    "capabilities/agentic-programming/philosophy.html": ("Philosophy", ""),
}

# Short bilingual display names; descriptive document headings remain in the sources.
PAGE_TITLES: dict[str, dict[str, str]] = json.loads(
    Path(__file__).with_name("page_titles.json").read_text(encoding="utf-8")
)

# Sidebar titles for directories that have no README.md of their own.
DIR_TITLES: dict[str, tuple[str, str]] = {  # rel dir -> (English, 中文)
    "capabilities/agentic-programming/writing-functions": ("Writing functions", "编写函数"),
    "capabilities/agentic-programming/choosing-the-next-step": ("Choosing the next step", "选择下一步"),
    "reference/design": ("Overview", "概览"),
    "reference/cli": ("CLI commands", "CLI 命令"),
}



@dataclass
class Page:
    src: Path          # absolute source path
    rel: Path          # path relative to docs/ (e.g. design/runtime/rewind.md)
    out: Path          # output path relative to _site/ (always .html)
    title: str
    is_readme: bool
    kind: str          # "md" or "html"
    i18n_key: str = ""  # if set, sidebar label switches with the UI language
    zh_src: Path | None = None   # Chinese-version source (xxx.zh.md), if any
    zh_out: Path | None = None   # Chinese-version output path, if any
    title_zh: str = ""  # Chinese sidebar label (from the .zh.md H1), if any
    key: str = ""       # how navigation registries name this page (see _registry_key)


_H1_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", re.MULTILINE)
_HTML_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def extract_title(path: Path) -> str:
    """First H1 (md) or <title> (html); fall back to a prettified file stem."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return prettify(path.stem)
    if path.suffix == ".md":
        m = _H1_RE.search(text)
        if m:
            return m.group(1).strip()
    else:
        m = _HTML_TITLE_RE.search(text)
        if m:
            # strip a common " — OpenProgram" style suffix for nav brevity
            return html.unescape(re.sub(r"\s*[—·|-]\s*OpenProgram.*$", "", m.group(1).strip()))
        # body-only fragment: fall back to its first <h1>
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.IGNORECASE | re.DOTALL)
        if h1:
            return html.unescape(re.sub(r"<[^>]+>", "", h1.group(1)).strip())
    return prettify(path.stem)


def prettify(name: str) -> str:
    name = name.replace("_", " ").replace("-", " ")
    return name.strip().title()


def is_excluded(rel: Path) -> bool:
    return any(part in EXCLUDE_DIRS for part in rel.parts)


def _registered_paths() -> set[str]:
    named = set(PAGE_ORDER) | set(PAGE_TITLES) | set(ROOT_PAGE_GROUPS) | set(ROOT_PAGE_TAB)
    for sections in TAB_SECTIONS.values():
        for _en, _zh, paths in sections:
            named.update(paths)
    return named


def _registry_key(rel: str, registered: set[str], sources: set[str]) -> str:
    """The name navigation registries use for a page.

    A page moved from ``topic.md`` to ``topic.html`` keeps its registered title,
    section and order. A ``.md`` file still present beside it owns that entry.
    """
    if rel.endswith(".html") and rel not in registered:
        former = rel[:-len(".html")] + ".md"
        if former in registered and former not in sources:
            return former
    return rel


def discover(docs_root: Path) -> list[Page]:
    """All public renderable pages under docs/, excluding internal paths.

    Bilingual convention: ``xxx.md`` or ``xxx.html`` is the default (English)
    version; a sibling ``xxx.zh.md`` or ``xxx.zh.html`` is its Chinese version.
    The Chinese source does not get its own sidebar entry; it is attached to the
    default source as ``zh_src`` and reached via the language toggle.
    """
    # First pass: collect all .zh.md chinese sources, keyed by their base stem.
    zh_sources: dict[Path, Path] = {}  # base rel (xxx.md) -> zh src path
    for path in docs_root.rglob("*.zh.md"):
        rel = path.relative_to(docs_root)
        if is_excluded(rel):
            continue
        base_rel = rel.with_name(rel.name[:-len(".zh.md")] + ".md")
        zh_sources[base_rel] = path
    for path in docs_root.rglob("*.zh.html"):
        rel = path.relative_to(docs_root)
        if is_excluded(rel):
            continue
        base_rel = rel.with_name(rel.name[:-len(".zh.html")] + ".html")
        zh_sources.setdefault(base_rel, path)

    sources = {path.relative_to(docs_root).as_posix() for path in docs_root.rglob("*.md")}
    registered = _registered_paths()
    pages: list[Page] = []
    for path in sorted(docs_root.rglob("*")):
        if path.suffix not in (".md", ".html"):
            continue
        rel = path.relative_to(docs_root)
        if is_excluded(rel):
            continue
        if rel.name.endswith((".zh.md", ".zh.html")):
            continue  # chinese version is attached to its base, not a page
        out = rel.with_suffix(".html")
        rel_str = _registry_key(rel.as_posix(), registered, sources)
        override = ROOT_PAGE_GROUPS.get(rel_str)
        title = override[0] if override else extract_title(path)
        zh_src = zh_sources.get(rel)
        zh_out = (rel.with_name(rel.stem + ".zh.html")) if zh_src else None
        title_zh = ("概览" if rel_str in ("README.html", "README.md") else extract_title(zh_src)) if zh_src else ""
        pages.append(
            Page(
                src=path,
                rel=rel,
                out=out,
                title=title,
                is_readme=path.stem.upper() == "README",
                kind=path.suffix.lstrip("."),
                zh_src=zh_src,
                zh_out=zh_out,
                title_zh=title_zh,
                key=rel_str,
            )
        )
    pages = _dedupe_md_html(pages)
    for page in pages:
        names = PAGE_TITLES.get(page.key)
        if names:
            page.title = names["en"]
            if page.zh_src is not None:
                page.title_zh = names["zh"]
    return pages


def _dedupe_md_html(pages: list[Page]) -> list[Page]:
    """When foo.md and foo.html coexist in the same dir, the md is the canonical
    page and the html is its visualization. Keep both, but the html output path
    is suffixed so it never overwrites the md's output."""
    by_dir_stem: dict[tuple, list[Page]] = {}
    for p in pages:
        by_dir_stem.setdefault((p.rel.parent, p.rel.stem), []).append(p)
    result: list[Page] = []
    for group in by_dir_stem.values():
        kinds = {p.kind for p in group}
        if kinds == {"md", "html"}:
            for p in group:
                if p.kind == "html":
                    p.out = p.rel.parent / f"{p.rel.stem}.viz.html"
                    if p.zh_out is not None:
                        p.zh_out = p.zh_out.with_suffix(".viz.html")
                    p.title = f"{p.title} (viz)"
                    if p.title_zh:
                        p.title_zh = f"{p.title_zh}（图解）"
                result.append(p)
        else:
            result.extend(group)
    return result


@dataclass
class Section:
    """An ordered page group, optionally nested under a design area."""
    title: str
    title_zh: str
    pages: list  # list[Page]
    area: tuple[str, str] | None = None


@dataclass
class Tab:
    key: str            # top-level dir name ("start", "models", …)
    title: str          # English (default) label
    title_zh: str
    sections: list      # list[Section] — the tab's sidebar
    landing: Path       # out path the navbar tab links to


def tab_of(p: Page) -> str:
    rel = str(p.rel).replace("\\", "/")
    if rel.startswith(ARCHIVE_PREFIX):
        return "design"
    parts = p.rel.parts
    if len(parts) == 1:
        return ROOT_PAGE_TAB.get(p.key or parts[0], FALLBACK_TAB)
    return parts[0] if parts[0] in TABS else FALLBACK_TAB


# Editorial sidebar layout: per tab, ordered sections with explicit page
# membership. (section EN title, section 中文 title, [rel paths in order]).
# Pages not listed here fall into an automatic trailing section named after
# their directory, so a new file never vanishes from the sidebar.
ARCHIVE_PREFIX = "reference/design/"
TAB_SECTIONS: dict[str, list[tuple[str, str, list[str]]]] = {
    "start": [
        ("Overview", "概览", ["README.html", "start/features.html"]),
        ("First steps", "第一步", [
            "start/GETTING_STARTED.html", "start/daily-use.html", "start/faq.html"]),
    ],
    "install": [
        ("Install", "安装", ["install/install.html", "install/download.html"]),
        ("Maintenance", "维护", [
            "install/upgrade.html", "install/conversational-self-update.html",
            "install/self-update-verification.html", "install/self-update-rollback.html",
            "install/development-checkout.html", "install/profiles.html"]),
    ],
    "capabilities": [
        ("Overview", "概览", ["capabilities/README.html", "capabilities/applications.html", "capabilities/framework-control.html"]),
        ("Agentic Programming", "Agentic Programming", [
            "capabilities/agentic-programming/README.html",
            "capabilities/agentic-programming/philosophy.html",
            "capabilities/agentic-programming/self-programming-ai-agents.html",
            "capabilities/agentic-programming/embedding-in-your-own-stack.html"]),
        ("Writing functions", "编写函数", [
            "capabilities/agentic-programming/writing-functions/pure-python.html",
            "capabilities/agentic-programming/writing-functions/agent.html",
            "capabilities/agentic-programming/writing-functions/function-metadata.html"]),
        ("Choosing the next step", "选择下一步", [
            "capabilities/agentic-programming/choosing-the-next-step/tool-calling.html",
            "capabilities/agentic-programming/choosing-the-next-step/fixed-order-calls.html",
            "capabilities/agentic-programming/choosing-the-next-step/next-step-decision.html"]),
        ("Agentic Workflows", "Agentic Workflows", [
            "capabilities/workflows/README.html",
            "capabilities/workflows/authoring.html",
            "capabilities/workflows/recording.html",
            "capabilities/workflows/reports.html"]),
        ("Agents", "Agents", ["capabilities/agents.html",
                              "capabilities/agents-md.html"]),
        ("Automation", "自动化", ["capabilities/always-on-agents.html",
                                  "capabilities/reminders.html",
                                  "capabilities/event-triggers.html",
                                  "capabilities/goal.html",
                                  "capabilities/computer-use.html",
                                  "capabilities/meetings.html"]),
        ("Memory", "记忆", ["capabilities/memory.html",
                            "capabilities/memory-settings.html"]),
        ("Self-update", "自我更新", ["capabilities/self-update.html"]),
        ("Extending", "扩展", [
            "capabilities/environments.html",
            "capabilities/installing-programs.html", "capabilities/skills.html",
            "capabilities/commit-push-pr.html",
            "capabilities/plugins.html", "capabilities/mcp.html",
            "capabilities/tools.html", "capabilities/permissions.html", "capabilities/lsp.html"]),
    ],
    "interfaces": [
        ("Overview", "概览", ["interfaces/README.html"]),
        ("Surfaces", "界面", [
            "interfaces/desktop.html", "interfaces/web.html", "interfaces/other-devices.html",
            "interfaces/tui.html", "interfaces/cli.html", "interfaces/acp.html",
            "interfaces/openai-api.html"]),
        ("Chat input", "聊天输入", ["interfaces/voice-input.html"]),
    ],
    "models": [
        ("Overview", "概览", ["models/README.html", "models/providers.html"]),
        ("Configuration", "配置", [
            "models/auth.html", "models/several-keys.html", "models/local-models.html", "models/fast-tier.html",
            "models/thinking-effort.html", "models/token-tracking.html"]),
    ],
    "integrations": [
        ("Services", "服务", [
            "integrations/anthropic.html", "integrations/openclaw.html",
            "integrations/channels.html"]),
    ],
    "server": [
        ("Overview", "概览", ["server/README.html"]),
        ("Operations", "运维", [
            "server/configuration.html", "server/upgrading.html",
            "server/backup.html", "server/troubleshooting.html"]),
    ],
    "reference": [
        ("Overview", "概览", ["reference/README.html"]),
        ("API", "API", [
            "reference/API.html", "reference/api/runtime.html",
            "reference/api/providers.html", "reference/api/agent.html"]),
        ("CLI and configuration", "CLI 与配置", [
            "reference/cli.html", "reference/config.html",
            "reference/diagnostics.html",
            "reference/session-export.html",
            "reference/session-import.html",
            "reference/output-styles.html",
            # generated from code at build time (generate_reference.py):
            "reference/config-keys.md", "reference/provider-registry.md"]),
        ("Comparisons", "对比", [
            "reference/comparisons/ai-agent-frameworks.html",
            "reference/comparisons/related-projects.html",
        ]),
    ],
    "design": [
        ('Overview', '总览', [
            "reference/design/README.html",
            "reference/design/framework-overview.html",
            "reference/design/repository/structure.html",
            "reference/design/plans/implementation-status.html",
            "reference/design/framework-comparison/overview.html",
            "reference/design/framework-comparison/capability-differences.html",
            "reference/design/framework-comparison/feature-matrix.html",
        ]),
        ('Runtime · Execution', '运行时 · 执行与控制', [
            'reference/design/runtime/agent/tool-name-resolution.html',
            'reference/design/runtime/agent/unified-agent.html',
            "reference/design/runtime/README.html",
            "reference/design/runtime/codex-capability-adoption.html",
            "reference/design/runtime/agent/agent-call-flow.html",
            "reference/design/runtime/execution/control.html",
            "reference/design/runtime/execution/branch-settings.html",
            "reference/design/runtime/agent/agent-action-lifecycle.html",
            "reference/design/runtime/execution/agent-worktree.html",
            "reference/design/runtime/execution/command-sessions.html",
            "reference/design/runtime/agent/agentic-self-recursion.html",
            "reference/design/runtime/execution/async-job-lifecycle.html",
            "reference/design/runtime/agent/agentic-llm-streaming.html",
            "reference/design/runtime/execution/controllability-and-three-surface-sync.html",
            "reference/design/runtime/execution/dispatcher-split.html",
            "reference/design/runtime/agent/next-step-decision.html",
        ]),
        ('Runtime · Sessions', '运行时 · 会话与存储', [
            'reference/design/runtime/session/environments.html',
            "reference/design/runtime/session/README.html",
            "reference/design/runtime/session/storage.html",
            "reference/design/runtime/session/operations.html",
            "reference/design/runtime/session/context.html",
            "reference/design/runtime/session/unified-session-context.html",
            "reference/design/runtime/session/additional-working-directories.html",
            "reference/design/runtime/session/comparison.html",
            "reference/design/runtime/session/external-import.html",
            "reference/design/runtime/session/storage-consistency.html",
            "reference/design/runtime/session/name.html",
        ]),
        ('Runtime · DAG and collaboration', '运行时 · DAG 与协作', [
            "reference/design/runtime/dag/overview.html",
            "reference/design/runtime/dag/nested-llm-node-content.html",
            "reference/design/runtime/collaboration/agent-collaboration.html",
            "reference/design/runtime/collaboration/agent-collab-architecture.html",
            "reference/design/runtime/collaboration/agent-collab-comparison.html",
            "reference/design/runtime/dag/branch-collaboration.html",
            "reference/design/runtime/dag/layout.html",
            "reference/design/runtime/dag/external-program-run-node.html",
            "reference/design/runtime/dag/persistence-observability.html",
            "reference/design/runtime/dag/rendering.html",
            "reference/design/runtime/operations/branch-naming.html",
            "reference/design/runtime/operations/edge-field-rename.html",
            "reference/design/runtime/operations/unify-parent-called-by.html",
        ]),
        ('Runtime · Goals and recovery', '运行时 · 目标与恢复', [
            "reference/design/runtime/reliability/goal-and-recovery.html",
            "reference/design/runtime/always-on-agents.html",
            "reference/design/runtime/security/outbound-hold.html",
            "reference/design/runtime/reliability/agent-resource-governance.html",
            "reference/design/runtime/reliability/structured-generation-recovery.html",
            "reference/design/runtime/reliability/durable-tool-results.html",
            "reference/design/runtime/reliability/agent-loop-guards.html",
            "reference/design/runtime/operations/streaming-resume.html",
            "reference/design/runtime/operations/chat-progress-recovery.html",
            "reference/design/runtime/reliability/web-runtime-reliability.html",
            "reference/design/runtime/reliability/web-frontend-spawn.html",
            "reference/design/runtime/reliability/worker-memory.html",
            "reference/design/runtime/reliability/change-notification.html",
        ]),
        ('Runtime · Configuration and operations', '运行时 · 配置与操作', [
            "reference/design/runtime/agent-configuration/agent-configuration-ui.html",
            "reference/design/runtime/agent-configuration/agent-core-configuration-ui.html",
            "reference/design/runtime/agent-configuration/agent-tool-configuration-ui.html",
            "reference/design/runtime/agent-configuration/agent-capability-configuration-ui.html",
            "reference/design/runtime/agent-configuration/agent-configuration-plan.html",
            "reference/design/runtime/agent-configuration/chat-search-settings.html",
            "reference/design/runtime/agent-configuration/tool-toggle-management.html",
            "reference/design/runtime/agent-configuration/bot-template-references.html",
            "reference/design/runtime/operations/user-input-requests.html",
            "reference/design/runtime/operations/file-management.html",
        ]),
        ('Programs and workflows', '程序与工作流', [
            "reference/design/function/README.html",
            "reference/design/function/program-model.html",
            "reference/design/function/agentic-program.html",
            "reference/design/function/calling-unification.html",
            "reference/design/function/deferred-tools.html",
            "reference/design/function/tool-guides.html",
            "reference/design/function/calling/code-call.html",
            "reference/design/runtime/programs/application-runtime.html",
            "reference/design/runtime/programs/programs-architecture.html",
            "reference/design/runtime/programs/meetings.html",
            "reference/design/runtime/workflows/workflow.html",
            "reference/design/runtime/workflows/workflow-recording.html",
            "reference/design/runtime/workflows/self-programming-workflow-ui.html",
            "reference/design/runtime/agent/llm-and-agent.html",
            "reference/design/runtime/self-update.html",
        ]),
        ('Workflows · Reports', '工作流 · 报告', [
            "reference/design/runtime/workflows/report-suite.html",
            "reference/design/runtime/workflows/weekly-report.html",
            "reference/design/runtime/workflows/group-weekly-report.html",
            "reference/design/runtime/workflows/report-delivery-intent.html",
        ]),
        ('Context', '上下文', [
            "reference/design/context/README.html",
            "reference/design/context/overview.html",
            "reference/design/context/composition.html",
            "reference/design/context/cache-prefix.html",
            "reference/design/context/compaction.html",
            "reference/design/context/compaction-diagram.html",
            "reference/design/context/occupancy-status.html",
            "reference/design/context/statistics.html",
            "reference/design/context/memory-introspection.html",
            "reference/design/context/comparison.html",
            "reference/design/context/claude-code-compaction.html",
        ]),
        ('Memory', '记忆', [
            "reference/design/memory/README.html",
            "reference/design/memory/overview.html",
            "reference/design/memory/architecture.html",
            "reference/design/memory/entity-memory-proposal.html",
            "reference/design/memory/virtual-memory.html",
            "reference/design/memory/authority-handoff.html",
            "reference/design/memory/authority-landscape.html",
            "reference/design/memory/adoption.html",
            "reference/design/memory/comparison.html",
            "reference/design/memory/editor.html",
            "reference/design/memory/settings-ui.html",
            "reference/design/memory/speaker-identity.html",
            "reference/design/memory/written-marker.html",
        ]),
        ('Events and scheduling', '事件与调度', [
            "reference/design/proactive/README.html",
            "reference/design/proactive/event-layer.html",
            "reference/design/proactive/events-and-state.html",
            "reference/design/proactive/execution-model.html",
            "reference/design/proactive/overview.html",
            "reference/design/proactive/policies-mvp.html",
            "reference/design/proactive/invariants.html",
            "reference/design/proactive/event-reference.html",
            "reference/design/proactive/framework-evolution.html",
            "reference/design/proactive/event-triggers.html",
            "reference/design/scheduler/memory-integration.html",
            "reference/design/scheduler/tasks-page.html",
        ]),
        ('UI · Foundations', '界面 · 基础', [
            'reference/design/ui/visual-system/icons.html',
            "reference/design/ui/README.html",
            "reference/design/ui/desktop/app-icon.html",
            "reference/design/ui/invariants.html",
            "reference/design/ui/visual-system/design-tokens.html",
            "reference/design/ui/visual-system/surface-system.html",
            "reference/design/ui/state-layer.html",
            "reference/design/ui/visual-system/theme-system.html",
            "reference/design/ui/desktop/window-state.html",
            "reference/design/ui/workspace/center-canvas-layout.html",
            "reference/design/ui/workspace/session-tab-identity.html",
            "reference/design/ui/desktop/window-lifecycle.html",
            "reference/design/ui/visual-system/web-styles.html",
            "reference/design/ui/interaction-feedback.html",
            "reference/design/ui/page-loading-performance.html",
            "reference/design/ui/visual-system/indicator-dots.html",
            "reference/design/ui/visual-system/agent-avatars.html",
            "reference/design/ui/sidebar/session-status.html",
            "reference/design/ui/sidebar/activity-view.html",
        ]),
        ('UI · Chat and composer', '界面 · 对话与编辑器', [
            'reference/design/ui/chat/chat-compose.html',
            'reference/design/ui/chat/chat-recovery-copy.html',
            'reference/design/ui/turn-lifecycle/conversation-lifecycle.html',
            "reference/design/ui/chat/attachment-handling.html",
            "reference/design/ui/chat/chat-attachments.html",
            "reference/design/ui/chat/chat-turn-visual-spec.html",
            "reference/design/ui/chat/tool-group-summary.html",
            "reference/design/ui/chat/sub-agent-row.html",
            "reference/design/ui/chat/task-blocks.html",
            "reference/design/ui/chat/activity-panel.html",
            "reference/design/ui/chat/chat-transcript-follow.html",
            "reference/design/ui/composer/composer-interaction-modes.html",
            "reference/design/ui/composer/composer-local-attachment-paths.html",
            "reference/design/ui/composer/composer-responsive-controls.html",
            "reference/design/ui/composer/composer-options-menu.html",
            "reference/design/ui/composer/composer-fast-control.html",
            "reference/design/ui/browser/agent-control-overlay.html",
            "reference/design/ui/resources/resource-preview.html",
            "reference/design/ui/browser/browser-control-surfaces.html",
            "reference/design/ui/turn-lifecycle/send-queue-reliability.html",
            "reference/design/ui/sidebar/session-auto-rename.html",
            "reference/design/ui/composer/slash-and-compact.html",
            "reference/design/ui/turn-lifecycle/turn-occupancy.html",
            "reference/design/ui/turn-lifecycle/websocket-command-lifecycle.html",
        ]),
        ('UI · Transcript model', '界面 · 对话记录模型', [
            "reference/design/ui/transcript-model/overview.html",
            "reference/design/ui/transcript-model/structure.html",
            "reference/design/ui/transcript-model/reads.html",
            "reference/design/ui/transcript-model/live.html",
            "reference/design/ui/transcript-model/wire.html",
            "reference/design/ui/transcript-model/delivery.html",
            "reference/design/ui/transcript-model/limits.html",
            "reference/design/ui/chat/todo-list.html",
            "reference/design/ui/chat/chat-markdown.html",
        ]),
        ('UI · Composer', '界面 · 输入框', [
            'reference/design/ui/composer/effort-picker.html',
            'reference/design/ui/composer/git-folder-pills.html',
            'reference/design/ui/composer/hover-tips.html',
        ]),
        ('UI · Browser and tabs', '界面 · 浏览器与标签页', [
            "reference/design/ui/browser/browser-extensions.html",
            "reference/design/ui/browser/built-in-browser.html",
            "reference/design/ui/browser/annotate.html",
            "reference/design/ui/resources/session-resources.html",
            "reference/design/ui/workspace/center-tabs-and-split-layout.html",
            "reference/design/ui/workspace/launchpad.html",
            "reference/design/ui/desktop/window-tab-transfer.html",
            "reference/design/ui/desktop/embedded-windows.html",
            "reference/design/ui/remote-web-access.html",
            "reference/design/ui/browser/web-tab-home-button.html",
            "reference/design/ui/browser/web-tab-native-bounds.html",
        ]),
        ('UI · Settings and catalog', '界面 · 设置与目录', [
            "reference/design/ui/settings/avatar-randomization.html",
            "reference/design/ui/programs/programs-explorer-template.html",
            "reference/design/ui/programs/programs-source-categories.html",
            "reference/design/ui/settings/settings-collapsible-columns.html",
        ]),
        ('UI · Workspace and sidebar', '界面 · 工作区与侧栏', [
            "reference/design/ui/workspace/integrated-terminal.html",
            "reference/design/ui/workspace/pane-recording.html",
            "reference/design/ui/workspace/review-pane.html",
            "reference/design/ui/sidebar/project-order.html",
            "reference/design/ui/workspace/project-location.html",
            "reference/design/ui/visual-system/file-type-icons.html",
            "reference/design/ui/workspace/project-workspace.html",
            "reference/design/ui/sidebar/right-sidebar-files.html",
            "reference/design/ui/chat/activity-chart-messages.html",
            "reference/design/ui/sidebar/right-sidebar-typography.html",
            "reference/design/ui/sidebar/sidebars-resizing.html",
            "reference/design/ui/sidebar/secondary-rail.html",
        ]),
        ('CLI and TUI', '命令行与终端界面', [
            "reference/design/cli/README.html",
            "reference/design/cli/config-write-safety.html",
            "reference/design/cli/drop-run-command.html",
            "reference/design/cli/headless-print.html",
            "reference/design/cli/naming.html",
            "reference/design/cli/ports.html",
            "reference/design/cli/redesign.html",
            "reference/design/cli/single-port.html",
            "reference/design/cli/slash-commands-references.html",
            "reference/design/cli/slash-commands.html",
            "reference/design/cli/tui-upgrade.html",
        ]),
        ('Providers · Requests and models', '模型服务 · 请求与模型', [
            'reference/design/providers/models/local-models.html',
            "reference/design/providers/README.html",
            "reference/design/providers/models/overview.html",
            "reference/design/providers/requests/request-build.html",
            "reference/design/providers/requests/request-redaction.html",
            "reference/design/providers/requests/web-search.html",
            "reference/design/providers/routing-overview.html",
            "reference/design/providers/models/custom-openai-compatible-provider.html",
            "reference/design/providers/models/fast-tier.html",
            "reference/design/providers/models/thinking-effort.html",
            "reference/design/providers/requests/json-schema-structured-output.html",
            "reference/design/providers/requests/network-proxy.html",
            "reference/design/providers/models/metadata-load-diagnostics.html",
            "reference/design/providers/models/bailian-model-catalog.html",
            "reference/design/providers/usage-metering.html",
        ]),
        ('Providers · Authentication', '模型服务 · 账号与认证', [
            'reference/design/providers/auth/anthropic-api-auth.html',
            "reference/design/providers/auth/api-key-resolution-unification.html",
            "reference/design/providers/auth/credential-connection-unification.html",
            "reference/design/providers/auth/credential-file-hardening.html",
            "reference/design/providers/auth/credential-model.html",
            "reference/design/providers/auth/credential-status-redesign.html",
            "reference/design/providers/auth/credential-validation-unification.html",
            "reference/design/providers/auth/unified-account-management.html",
            "reference/design/providers/auth/unified-auth-storage.html",
        ]),
        ('Providers · Reliability', '模型服务 · 可靠性', [
            "reference/design/providers/reliability/error-and-timeout-mechanism.html",
            "reference/design/providers/reliability/error-retry.html",
            "reference/design/providers/reliability/error-taxonomy-propagation.html",
            "reference/design/providers/reliability/llm-fault-tolerance.html",
            "reference/design/providers/reliability/request-repair.html",
            "reference/design/providers/requests/codex-transport.html",
            "reference/design/providers/provider-quota.html",
            "reference/design/providers/provider-management.html",
            "reference/design/providers/requests/record-replay.html",
            "reference/design/providers/requests/responses-reasoning-replay.html",
        ]),
        ('Extensions and integrations', '扩展与集成', [
            'reference/design/repository/integration.html',
            "reference/design/integrations/README.html",
            "reference/design/integrations/nooa.html",
            "reference/design/integrations/editor-integration.html",
            "reference/design/integrations/extensions/extension-management.html",
            "reference/design/integrations/harness-standard.html",
            "reference/design/integrations/extensions/mcp-integration.html",
            "reference/design/integrations/extensions/mcp-server.html",
            "reference/design/integrations/openai-compatible-endpoint.html",
            "reference/design/integrations/remote-openprogram.html",
            "reference/design/integrations/extensions/skills-and-plugins.html",
            "reference/design/integrations/extensions/plugin-runtime.html",
            "reference/design/extension-gating/README.html",
            "reference/design/extension-gating/future-work.html",
            "reference/design/extension-gating/reference-comparison.html",
        ]),
        ('Computer and browser use', '操控电脑与浏览器', [
            "reference/design/integrations/computer-use/computer-and-browser-use.html",
            "reference/design/integrations/computer-use/approval.html",
            "reference/design/integrations/computer-use/app-target.html",
            "reference/design/integrations/computer-use/macos.html",
            "reference/design/integrations/computer-use/linux-windows.html",
            "reference/design/integrations/computer-use/working-behind-the-user.html",
            "reference/design/integrations/computer-use/interface.html",
            "reference/design/integrations/computer-use/machines-and-targets.html",
            "reference/design/integrations/computer-use/user-browsers.html",
            "reference/design/integrations/browser/application.html",
            "reference/design/integrations/browser/tab-driver.html",
            "reference/design/integrations/browser/tab-actions.html",
        ]),
        ('Channels', '消息渠道', [
            "reference/design/channels/README.html",
            "reference/design/channels/audit.html",
            "reference/design/channels/design.html",
            "reference/design/channels/chat-bots.html",
        ]),
        ('Security and permissions', '安全与权限', [
            "reference/design/runtime/security/permission-decision.html",
            "reference/design/runtime/security/sandbox-architecture.html",
            "reference/design/runtime/security/system-access.html",
            "reference/design/runtime/security/ssrf-protection.html",
            "reference/design/security/dependency-security.html",
        ]),
        ('Distribution', '安装与分发', [
            "reference/design/distribution/installation-packaging.html",
            "reference/design/distribution/automatic-updates.html",
            "reference/design/distribution/dev-instance.html",
            "reference/design/distribution/github-publishing.html",
            "reference/design/distribution/developer-id.html",
            "reference/design/distribution/windows-support.html",
            "reference/design/distribution/windows-command-shell.html",
        ]),
        ('Engineering · Testing and errors', '工程 · 测试与错误处理', [
            "reference/design/testing/test-system.html",
            "reference/design/testing/windows-ci.html",
            "reference/design/testing/harbor-benchmark-adapter.html",
            "reference/design/repository/error-handling.html",
        ]),
        ('Engineering · Documentation and website', '工程 · 文档与网站', [
            "reference/design/docs-site/overview.html",
            "reference/design/docs-site/authoring.html",
            "reference/design/docs-site/governance.html",
            "reference/design/docs-site/discoverability-performance.html",
            "reference/design/docs-site/agent-readable-docs.html",
            "reference/design/docs-site/indexnow-discovery.html",
            "reference/design/repository/community-discoverability.html",
        ]),
        ('Supporting · Prototypes', '补充 · 交互原型', [
            "reference/design/ui/visual-system/layout-density-prototype.html",
            "reference/design/ui/composer/composer-interaction-prototype.html",
            "reference/design/ui/workspace/project-workspace-prototype.html",
            "reference/design/ui/sidebar/sidebar-hierarchy-prototype.html",
            "reference/design/ui/sidebar/sidebar-luma-prototype.html",
            "reference/design/runtime/dag/live-layout.html",
        ]),
        ('Supporting · Implementation records', '补充 · 实施记录', [
            "reference/design/runtime/execution/p3-three-surface-sync.html",
            "reference/design/repository/structure-implementation.html",
            "reference/design/plans/documentation-gaps.html",
            "reference/design/plans/engineering-backlog.html",
            "reference/design/ui/visual-system/unification-work.html",
            "reference/design/ui/resources/resource-panel-preview-plan.html",
            "reference/design/distribution/implementation-plan.html",
            "reference/design/extension-gating/implementation.html",
            "reference/design/plans/credential-connection-unification.html",
            "reference/design/plans/mcp-server-implementation.html",
            "reference/design/plans/cache-control-passthrough.html",
            "reference/design/plans/proactive-implementation.html",
        ]),
        ('Supporting · Research', '补充 · 研究材料', [
            "reference/design/proactive/_research_archive/evaluation.html",
            "reference/design/proactive/_research_archive/replay.html",
            "reference/design/proactive/_research_archive/threat-model.html",
        ]),
    ],
}


# Top-level design areas. The section lists below are the only source of
# hierarchy/order; flat page sequences remain available to breadcrumbs and
# previous/next navigation. Overview is a direct group, not a redundant wrapper.
DESIGN_AREAS = [
    ("Architecture", "架构总览", ["Overview"]),
    ("Agents and workflows", "Agent 与工作流", [
        "Runtime · Execution", "Runtime · Sessions", "Runtime · DAG and collaboration",
        "Runtime · Goals and recovery", "Runtime · Configuration and operations",
        "Programs and workflows", "Workflows · Reports", "Events and scheduling",
    ]),
    ("Context and memory", "上下文与记忆", ["Context", "Memory"]),
    ("Interfaces", "界面与交互", [
        "UI · Foundations", "UI · Chat and composer", "UI · Transcript model", "UI · Composer", "UI · Browser and tabs",
        "UI · Settings and catalog", "UI · Workspace and sidebar", "CLI and TUI",
    ]),
    ("Models and connections", "模型与外部接入", [
        "Providers · Requests and models", "Providers · Authentication",
        "Providers · Reliability", "Extensions and integrations", "Computer and browser use",
        "Channels",
    ]),
    ("Security and engineering", "安全与工程", [
        "Security and permissions", "Distribution", "Engineering · Testing and errors",
        "Engineering · Documentation and website",
    ]),
    ("Supporting material", "补充材料", [
        "Supporting · Prototypes", "Supporting · Implementation records", "Supporting · Research",
    ]),
]


# Explicit sidebar order for product pages (rel path or rel dir -> rank).
# Tutorial docs must read top-to-bottom; anything unlisted sorts after these,
# alphabetically (which is fine for the design-notes archive).
PAGE_ORDER: dict[str, int] = {
    "models/local-models.html": 3,
    "reference/design/runtime/agent-configuration/agent-configuration-ui.html": 990,
    "reference/design/runtime/agent-configuration/agent-core-configuration-ui.html": 991,
    "reference/design/runtime/agent-configuration/agent-capability-configuration-ui.html": 992,
    "reference/design/runtime/agent-configuration/agent-tool-configuration-ui.html": 993,
    "reference/design/runtime/agent-configuration/agent-configuration-plan.html": 994,
    "reference/design/runtime/workflows/report-delivery-intent.html": 1007,
    "README.html": 0,
    "start/GETTING_STARTED.html": 1,
    "start/daily-use.html": 2,
    "start/features.html": 3,
    "start/faq.html": 4,
    "install/install.html": 0,
    "install/download.html": 1,
    "install/upgrade.html": 2,
    "install/conversational-self-update.html": 3,
    "install/self-update-verification.html": 4,
    "install/self-update-rollback.html": 5,
    "install/development-checkout.html": 6,
    "install/profiles.html": 7,
    "capabilities/README.html": 0,
    "capabilities/applications.html": 1,
    "capabilities/framework-control.html": 2,
    "capabilities/agentic-programming": 1,
    "capabilities/workflows": 2,
    "capabilities/installing-programs.html": 3,
    "capabilities/skills.html": 4,
    "capabilities/commit-push-pr.html": 6,
    "capabilities/plugins.html": 7,
    "capabilities/mcp.html": 8,
    "capabilities/tools.html": 9,
    "capabilities/permissions.html": 10,
    "capabilities/environments.html": 10,
    "capabilities/lsp.html": 11,
    "capabilities/agents.html": 12,
    "capabilities/agents-md.html": 12,
    "capabilities/always-on-agents.html": 12,
    "capabilities/reminders.html": 12,
    "capabilities/event-triggers.html": 12,
    "capabilities/memory.html": 12,
    "capabilities/memory-settings.html": 12,
    "capabilities/goal.html": 12,
    "capabilities/meetings.html": 12,
    "capabilities/agentic-programming/philosophy.html": 1,
    "capabilities/agentic-programming/embedding-in-your-own-stack.html": 2,
    "capabilities/agentic-programming/writing-functions": 3,
    "capabilities/agentic-programming/choosing-the-next-step": 4,
    "capabilities/workflows/reports.html": 0,
    "interfaces/README.html": 0,
    "interfaces/desktop.html": 1,
    "interfaces/web.html": 2,
    "interfaces/other-devices.html": 3,
    "interfaces/tui.html": 4,
    "interfaces/cli.html": 5,
    "interfaces/acp.html": 6,
    "interfaces/openai-api.html": 7,
    "interfaces/voice-input.html": 8,
    "models/README.html": 0,
    "models/providers.html": 1,
    "models/auth.html": 2,
    "models/fast-tier.html": 3,
    "models/thinking-effort.html": 4,
    "models/token-tracking.html": 5,
    "integrations/anthropic.html": 0,
    "integrations/openclaw.html": 1,
    "integrations/channels.html": 2,
    "server/README.html": 0,
    "server/configuration.html": 1,
    "server/upgrading.html": 2,
    "server/backup.html": 3,
    "server/troubleshooting.html": 4,
    "reference/README.html": 0,
    "reference/API.html": 1,
    "reference/api": 2,
    "reference/cli.html": 3,
    "reference/config.html": 4,
    "reference/diagnostics.html": 5,
    "reference/session-export.html": 6,
    "reference/session-import.html": 7,
    "reference/output-styles.html": 8,
    "reference/comparisons": 9,
    "reference/comparisons/ai-agent-frameworks.html": 0,
    "reference/comparisons/related-projects.html": 1,
    "reference/design": 900,  # design-notes archive always last
    "reference/design/plans/implementation-status.html": 1,
    "reference/design/repository/structure.html": 2,
    "reference/design/repository/structure-implementation.html": 3,
    "reference/design/repository/integration.html": 4,
    "reference/design/docs-site/overview.html": 4,
    "reference/design/docs-site/authoring.html": 5,
    "reference/design/docs-site/governance.html": 6,
    # The context notes read in order: the layer, then compaction, then how the
    # blocks are composed and compared, then the two rendered companions.
    "reference/design/context/README.html": 0,
    "reference/design/context/overview.html": 1,
    "reference/design/context/compaction.html": 2,
    "reference/design/context/composition.html": 3,
    "reference/design/context/comparison.html": 4,
    "reference/design/context/compaction-diagram.html": 5,
    "reference/design/context/memory-introspection.html": 6,
    "reference/design/context/claude-code-compaction.html": 7,
    # The memory notes read in order: what it is, how it works, how others do it.
    "reference/design/memory/README.html": 0,
    "reference/design/memory/overview.html": 1,
    "reference/design/memory/written-marker.html": 2,
    "reference/design/memory/architecture.html": 4,
    "reference/design/memory/comparison.html": 5,
    "reference/design/memory/adoption.html": 6,
    # Within the design archive everything defaults to 999 (alphabetical).
    # >999 pins canonical security designs to the end of their section.
    "reference/design/runtime/security/sandbox-architecture.html": 1000,
    "reference/design/runtime/security/system-access.html": 1002,
    # Same treatment for agent collaboration: the design note first, then its
    # two rendered companions (our tool surface, then the eight reference
    # implementations compared).
    "reference/design/runtime/collaboration/agent-collaboration.html": 1002,
    "reference/design/runtime/agent/unified-agent.html": 1002,
    "reference/design/runtime/collaboration/agent-collab-architecture.html": 1003,
    "reference/design/runtime/collaboration/agent-collab-comparison.html": 1004,
    # Unified lifecycle and debugger control contract for all runtime owners.
    "reference/design/runtime/execution/control.html": 1005,
    "reference/design/runtime/reliability/durable-tool-results.html": 1005,
    "reference/design/runtime/reliability/goal-and-recovery.html": 1006,
    "reference/design/runtime/always-on-agents.html": 1006,
    # Center tabs: authoritative tab/group/view state and split-layout design.
    "reference/design/ui/workspace/center-tabs-and-split-layout.html": 1009,
    "reference/design/ui/browser/built-in-browser.html": 1010,
    "reference/design/ui/resources/session-resources.html": 1010,
    "reference/design/ui/sidebar/session-auto-rename.html": 1011,
    "reference/design/ui/browser/browser-extensions.html": 1011,
    "reference/design/ui/workspace/integrated-terminal.html": 1012,
    "reference/design/ui/composer/composer-local-attachment-paths.html": 1013,
    "reference/design/ui/composer/composer-responsive-controls.html": 1014,
    "reference/design/ui/composer/composer-options-menu.html": 1015,
    "reference/design/ui/composer/composer-fast-control.html": 1020,
    "reference/design/ui/programs/programs-source-categories.html": 1016,
    "reference/design/ui/visual-system/theme-system.html": 1017,
    "reference/design/ui/settings/settings-collapsible-columns.html": 1018,
    # Chat attachments: the delivery note first, its rendered companion
    # next, then the four-layer note on how attachments look and behave
    # inside the chat itself.
    "reference/design/ui/chat/attachment-handling.html": 1019,
    "reference/design/ui/chat/chat-attachments.html": 1021,
    "reference/design/ui/visual-system/file-type-icons.html": 1022,
    "reference/design/ui/browser/browser-control-surfaces.html": 1023,
    # The three whole-framework pages sit together at the end of the design
    # root: first how one conversation runs inside us, then how we compare to
    # the reference frameworks by design axis, then by feature list.
    "reference/design/framework-overview.html": 1020,
    "reference/design/framework-comparison/overview.html": 1021,
    "reference/design/framework-comparison/capability-differences.html": 1021.1,
    "reference/design/runtime/programs/application-runtime.html": 1023,
    "reference/design/framework-comparison/feature-matrix.html": 1022,
    # Distribution: authoritative conceptual design followed by its separate
    # implementation ledger.
    "reference/design/distribution/installation-packaging.html": 1035,
    "reference/design/distribution/automatic-updates.html": 1036,
    "reference/design/distribution/dev-instance.html": 1036.5,
    "reference/design/distribution/github-publishing.html": 1036.7,
    "reference/design/distribution/implementation-plan.html": 1037,
    "reference/design/distribution/windows-support.html": 1038,
    "reference/design/distribution/windows-command-shell.html": 1038.5,
    # Integrations: the MCP server design follows the alphabetical integration notes.
    "reference/design/integrations/extensions/mcp-server.html": 1040,
    "reference/design/integrations/openai-compatible-endpoint.html": 1040.5,
    "reference/design/integrations/remote-openprogram.html": 1040.7,
    "reference/design/integrations/nooa.html": 1041,

    # Test architecture and execution contracts.
    "reference/design/testing/test-system.html": 1050,
    # Agentic program: the four-layer note that unifies tools, skills and
    # agentic functions as one concept sits after the calling-framework
    # note it builds on.
    "reference/design/function/agentic-program.html": 1030,
}


def _order_key(page: Page) -> int:
    return PAGE_ORDER.get(page.key or page.rel.as_posix(), 999)


def build_tabs(docs_root: Path, pages: list[Page]) -> list[Tab]:
    """Split pages into ordered editorial sections with a discoverable fallback."""
    tabs: list[Tab] = []
    for key, (en, zh) in TABS.items():
        tab_pages = [p for p in pages if tab_of(p) == key]
        if not tab_pages:
            continue
        rel_str = lambda p: p.key or p.rel.as_posix()
        by_rel = {rel_str(p): p for p in tab_pages}
        placed: set[str] = set()

        sections: list[Section] = []
        for sec_en, sec_zh, paths in TAB_SECTIONS.get(key, []):
            sec_pages = [by_rel[r] for r in paths if r in by_rel]
            placed.update(r for r in paths if r in by_rel)
            if sec_pages:
                sections.append(Section(title=sec_en, title_zh=sec_zh, pages=sec_pages))

        # Anything unlisted lands in an automatic section named after its
        # directory, so new files never vanish from the sidebar. Design pages use editorial groups above; unclassified new pages
        # remain visible rather than disappearing.
        leftovers = [p for p in tab_pages if rel_str(p) not in placed]
        auto_base = Path(ARCHIVE_PREFIX.rstrip("/")) if key == "design" else Path(key)
        fallback_sections = _auto_sections(docs_root, leftovers, base_dir=auto_base)
        if key == "design":
            for section in fallback_sections:
                section.title = "Uncategorized · " + section.title
                section.title_zh = "待分类 · " + (section.title_zh or section.title)
        sections.extend(fallback_sections)

        if key == "design":
            by_title = {section.title: section for section in sections}
            ordered = []
            for area_en, area_zh, names in DESIGN_AREAS:
                for name in names:
                    section = by_title.pop(name, None)
                    if section is not None:
                        section.area = (area_en, area_zh)
                        ordered.append(section)
            for section in by_title.values():
                section.area = ("Uncategorized", "待分类")
                ordered.append(section)
            sections = ordered

        landing = sections[0].pages[0].out if sections and sections[0].pages else Path("index.html")
        tabs.append(Tab(key=key, title=en, title_zh=zh, sections=sections,
                        landing=landing))
    return tabs


def _dir_title(docs_root: Path, rel_dir: Path) -> tuple[str, str]:
    override = DIR_TITLES.get(str(rel_dir).replace("\\", "/"))
    if override:
        return override
    def first(*names: str) -> Path | None:
        return next((docs_root / rel_dir / name for name in names
                     if (docs_root / rel_dir / name).exists()), None)

    readme = first("README.html", "README.md")
    readme_zh = first("README.zh.html", "README.zh.md")
    title = extract_title(readme) if readme else prettify(rel_dir.name)
    title_zh = extract_title(readme_zh) if readme_zh else ""
    return title, title_zh


def _auto_sections(docs_root: Path, pages: list[Page], base_dir: Path) -> list[Section]:
    """Group pages into one flat section per directory, ordered by path.
    Nested dirs become their own sections titled 'Parent · Child'."""
    by_dir: dict[Path, list[Page]] = {}
    for p in pages:
        by_dir.setdefault(p.rel.parent, []).append(p)

    sections: list[Section] = []
    for rel_dir in sorted(by_dir, key=lambda d: str(d)):
        sec_pages = sorted(
            by_dir[rel_dir],
            key=lambda p: (_order_key(p), not p.is_readme, p.title.lower()))
        # section title: the dir chain below base_dir, joined for nested dirs
        try:
            chain = rel_dir.relative_to(base_dir).parts
        except ValueError:
            chain = rel_dir.parts
        if not chain:
            title, title_zh = _dir_title(docs_root, rel_dir)
        else:
            parts_en, parts_zh = [], []
            for i in range(len(chain)):
                en, zh = _dir_title(docs_root, base_dir.joinpath(*chain[:i + 1]))
                parts_en.append(en)
                parts_zh.append(zh or en)
            title = " · ".join(parts_en)
            title_zh = " · ".join(parts_zh)
        sections.append(Section(title=title, title_zh=title_zh, pages=sec_pages))
    return sections
