<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../docs/images/logo-lockup.gif">
    <source media="(prefers-color-scheme: light)" srcset="../docs/images/logo-lockup-light.gif">
    <img src="../docs/images/logo-lockup.gif" alt="OpenProgram" width="440">
  </picture>
</p>

<!-- Compatibility anchors for existing incoming links. -->
<a id="1-agentic-function-the-primitive-everything-else-is-built-on"></a>
<a id="2-dag-context-for-native-multi-agent-systems"></a>
<a id="3-agentic-workflow-for-trustworthy-self-evolving-agents"></a>
<a id="4-event-infrastructure-for-proactive-agents"></a>
<a id="integrated-projects"></a>
<a id="why-openprogram"></a>
<a id="1-agentic-function--the-primitive-everything-else-is-built-on"></a>
<a id="2-dag-context--for-native-multi-agent-systems"></a>
<a id="3-agentic-workflow--for-trustworthy--self-evolving-agents"></a>
<a id="4-event-infrastructure--for-proactive-agents"></a>
<a id="also-in-the-product"></a>

<p align="center"><b>開源 Agent Harness：Agent 就是能自我改進的 Python 程式。</b></p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/.github/CHANGELOG.md"><img alt="版本" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square">
</p>

<p align="center">
  <a href="https://fuzzykuji.github.io/openprogram-site/">官網</a> · <a href="#install">安裝</a> · <a href="https://fuzzykuji.github.io/openprogram-site/docs/">文件</a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

## 動態

- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — 每個服務商可放多組金鑰與登入，到達限額時自動換下一組；回覆依任務分塊；排程任務有執行紀錄。從這一版起在本儲存庫發布。
- **2026-11-15 · v1.3.0** — 服務商頁面顯示健康狀態、額度與花費；Activity 列出對話中工作的 Agent；Agent 產生的檔案自動開啟。
- **2026-11-14 · v1.2.0** — 同一對話的分支可以並行執行，停止立即生效，Windows App 上線。
- **2026-11-10 · v1.1.0** — 可以從任一則已傳送的訊息回退；App 支援 Linux。
- **2026-11-06 · v1.0.0** — 1.x 的第一個版本：具分割畫面的桌面 App、背景執行的電腦操作、會議與常駐排程器。
- **2026-08-17 · v0.7.0** — 內建瀏覽器，與檔案、對話和終端機並排，Agent 可以讀取並操作。
- **2026-03-31 · v0.1.0** — 第一個公開版本：Agentic 函式——由模型執行各步驟的 Python 函式，執行過程記錄在脈絡樹中。

<a id="install"></a>

## 安裝

```bash
curl -fsSL https://fuzzykuji.github.io/openprogram-site/install | sh
```

Windows（v1.2.0 起）：`irm https://fuzzykuji.github.io/openprogram-site/install.ps1 | iex`。macOS 桌面 App 是 DMG，可在[發行頁](https://github.com/Fzkuji/OpenProgram/releases)下載；Linux App 是同一頁上的 deb 和 AppImage，Windows App 從 v1.2.0 起提供。網站無法連線時：`curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`。更多方式見[安裝說明](https://fuzzykuji.github.io/openprogram-site/docs/install/install.html)（英文）。

## 快速開始

```bash
openprogram        # 設定精靈，然後進入終端機聊天
openprogram web    # 工作區，網址 http://localhost:18100
```

接下來：[快速上手](../docs/start/GETTING_STARTED.html) · [模型設定](../docs/models/README.html) · [全部文件](../docs/README.html)（英文）

## 一眼看懂

```python
from openprogram import Agent

class SupportAgent(Agent):
    def triage(self, ticket: str) -> str:
        """Classify the ticket, then draft a reply."""
        kind = self(ticket, choices=["bug", "question"])
        if kind == "bug":
            logs = search_logs(ticket)  # 你自己的輔助函式
            return self(f"Reply using:\n{logs}")
        return self("Draft a short reply.")
```

模型負責判斷，Python 確保必要的步驟一定會執行。參見[撰寫 Agent](../docs/capabilities/agentic-programming/writing-functions/agent.html)（英文）。

## 功能

每一項都連結到對應的設計文件（英文）。

### 框架

| | |
|---|---|
| **[Agent 類別](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/function/agentic-program.html)** | Agent 是一個 Python 類別；分支、重試和檢查都是程式碼，`self(...)` 是一次模型呼叫。 |
| **[執行 DAG](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/dag/overview.html)** | 每則使用者訊息、模型呼叫和函式呼叫都是節點，可以檢視、分支和重播。 |
| **[上下文](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/context/overview.html)** | 每次呼叫讀取挑選出的節點，而不是不斷變長的對話記錄，並會自動壓縮。 |
| **[自我程式設計](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/workflows/workflow.html)** | Agent 把新方法寫進自己的類別，下一輪起就能當作工具使用。 |
| **[受檢查的決策](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent/next-step-decision.html)** | 模型從固定選項中挑選，程式碼先驗證再執行。 |
| **[子 Agent](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/collaboration/agent-collaboration.html)** | 子 Agent 在自己的分支上執行，可使用獨立的 Git worktree，只交回結果。 |
| **[事件](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/proactive/event-layer.html)** | 型別化事件匯流排；外掛可以觀察事件或攔截工具呼叫。 |
| **[常駐 Agent](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/always-on-agents.html)** | 在背景持續執行、對事件做出反應的 Agent。 |
| **[目標與復原](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/reliability/goal-and-recovery.html)** | 長期目標在重新啟動後繼續，從中斷處復原。 |
| **[容錯](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/reliability/llm-fault-tolerance.html)** | 每次模型呼叫都有重試、逾時和錯誤分類。 |

### 工作區

| | |
|---|---|
| **[桌面與網頁介面](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/README.html)** | macOS 和 Linux 上用 App，v1.2.0 起 Windows 也有 App；網頁介面可在這些系統或伺服器上使用。 |
| **[分割畫面](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/center-tabs-and-split-layout.html)** | 聊天、檔案、終端機和網頁並排呈現。 |
| **[內建瀏覽器](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/browser/built-in-browser.html)** | Agent 能讀取和操作網頁，你在一旁觀看。 |
| **[整合終端機](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/integrated-terminal.html)** | 工作區裡的終端機，與 Agent 共用。 |
| **[Computer use](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/computer-use/computer-and-browser-use.html)** | 操作桌面應用程式、瀏覽器頁面或遠端虛擬機。 |
| **[CLI 與終端介面](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/cli/README.html)** | 在終端機裡使用同一個 Agent。 |
| **[遠端存取](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/remote-web-access.html)** | 從其他裝置存取你自己的實例，僅限本人。 |

### Agent 與安全

| | |
|---|---|
| **[Agent 設定](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent-configuration/agent-configuration-ui.html)** | 把模型、指令、工具和記憶權限儲存成可重複使用的 Agent。 |
| **[記憶](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/memory/overview.html)** | 有來源依據的記憶，支援檢索和背景寫入。 |
| **[權限與沙箱](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/security/sandbox-architecture.html)** | 批准或拒絕工具呼叫並附上說明；指令在沙箱中執行。 |
| **[Programs](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/programs/programs-architecture.html)** | 工具和應用程式都是可安裝的 Program。 |

### 模型與整合

| | |
|---|---|
| **[模型服務](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/README.html)** | Anthropic、OpenAI、Gemini 等，每個服務商可設定多個帳號。 |
| **[本機模型](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/models/local-models.html)** | 本機推論服務，可選用身分驗證。 |
| **[MCP](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/mcp-integration.html)** | 使用 MCP 伺服器，或把 OpenProgram 作為 MCP 伺服器提供。 |
| **[Skills 與外掛](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/skills-and-plugins.html)** | 擴充 Agent 的知識和可呼叫的工具。 |
| **[聊天頻道](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/channels/README.html)** | 在 Telegram、Discord、Slack 和微信裡與 Agent 對話。 |
| **[自動更新](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/distribution/automatic-updates.html)** | 經過簽署的發行版本，原地更新。 |

## 引用

```bibtex
@inproceedings{qi2026llmascode,
  title     = {LLM-as-Code: Agentic Programming for Agent Harness},
  author    = {Qi, Junjia and Fu, Zichuan and Gao, Jingtong and Zhang, Wenlin and Yan, Hanyu and Wu, Xian and Zhao, Xiangyu},
  booktitle = {KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)},
  year      = {2026},
  url       = {https://arxiv.org/abs/2606.15874},
}
```

## 授權

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji。使用、修改、散布及網路服務的義務以授權條款全文為準。

## 致謝

感謝提供程式碼、問題回報、重現、測試、文件與審查的貢獻者。

<!-- contributors-avatars -->
<p>
<a href="https://github.com/FuzzyKuji"><img src="https://avatars.githubusercontent.com/u/9404270?v=4&amp;s=48" width="24" height="24" alt="Maintainer" /></a>
<a href="https://github.com/Qi202"><img src="https://github.com/Qi202.png?size=48" width="24" height="24" alt="Qi202" /></a>
<a href="https://github.com/GithungDang"><img src="https://github.com/GithungDang.png?size=48" width="24" height="24" alt="GithungDang" /></a>
<a href="https://github.com/basil-k-aji-dev"><img src="https://github.com/basil-k-aji-dev.png?size=48" width="24" height="24" alt="basil-k-aji-dev" /></a>
<a href="https://github.com/binyangzhu000-sudo"><img src="https://github.com/binyangzhu000-sudo.png?size=48" width="24" height="24" alt="binyangzhu000-sudo" /></a>
</p>
<!-- /contributors-avatars -->

[問題回報](https://github.com/Fzkuji/OpenProgram/issues) · [拉取請求](https://github.com/Fzkuji/OpenProgram/pulls)
