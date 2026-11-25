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
<a id="quick-start"></a>
<a id="features"></a>
<a id="framework"></a>
<a id="workspace"></a>
<a id="agents-and-safety"></a>
<a id="models-and-integrations"></a>
<a id="acknowledgements"></a>

<p align="center"><b>Agent 可以編寫任何程式。</b><br>工作流程、內建 AI 的應用，以及 Agent 自己。開源 Agent Harness，Agent 本身就是 Python 程式。</p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/releases/latest"><img alt="版本" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Fzkuji/OpenProgram?style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://arxiv.org/abs/2606.15874"><img alt="Paper" src="https://img.shields.io/badge/arXiv-2606.15874-b31b1b?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/"><b>官網</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/README.html"><b>文件</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/install/download.html"><b>下載 App</b></a> · <a href="https://arxiv.org/abs/2606.15874"><b>論文</b></a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="../docs/images/openprogram-tour-dark.webp">
      <source media="(prefers-color-scheme: light)" srcset="../docs/images/openprogram-tour-light.webp">
      <img src="../docs/images/openprogram-tour-light.webp" alt="OpenProgram 導覽：一個任務從頭到尾、常駐 Agent、多裝置，以及能編寫任何程式的 Agent" width="100%">
    </picture>
  </a>
</p>

<a id="install"></a>

## 安裝

**macOS · Linux**

```bash
curl -fsSL https://fzkuji.github.io/OpenProgram/install | sh
```

**Windows（PowerShell）**

```powershell
irm https://fzkuji.github.io/OpenProgram/install.ps1 | iex
```

**桌面 App** — [macOS Apple Silicon](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-arm64.dmg) · [macOS Intel](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-x64.dmg) · [Windows](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-win-x64.exe) · [Linux（deb、AppImage）](https://fzkuji.github.io/OpenProgram/docs/install/download.html)。App 會自動更新。

接著執行：

```bash
openprogram        # 連接模型，然後在終端機裡聊天
openprogram web    # 開啟工作區，網址 http://localhost:18100
```

網站無法連線時：`curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`。更多方式：[安裝說明](https://fzkuji.github.io/OpenProgram/docs/install/install.html) · [快速上手](https://fzkuji.github.io/OpenProgram/docs/start/GETTING_STARTED.html)。

## 為什麼選 OpenProgram

<table>
<tr><td><b>Agent 就是 Python 程式</b></td><td>分支、重試和檢查都是一般的程式碼；<code>self(...)</code> 是一次模型呼叫。模型負責判斷，Python 確保必要的步驟一定會執行。</td></tr>
<tr><td><b>自己為自己寫程式</b></td><td>Agent 把新方法寫進自己的類別，下一輪起每個方法都是可呼叫的工具。</td></tr>
<tr><td><b>是圖，不是對話記錄</b></td><td>每輪對話、模型呼叫和函式呼叫都是節點，可以檢視、分支和重播。子 Agent 在自己的分支上工作，只交回結果。</td></tr>
<tr><td><b>受檢查的決策</b></td><td>模型從固定選項中挑選，程式碼先驗證再執行。</td></tr>
<tr><td><b>一個工作區</b></td><td>聊天、檔案、終端機和內建瀏覽器並排呈現，可在桌面 App、網頁介面、終端介面或 CLI 中使用。</td></tr>
<tr><td><b>操作你的電腦</b></td><td>在背景操作桌面應用程式、瀏覽器頁面和遠端虛擬機，不佔用你的螢幕。</td></tr>
<tr><td><b>常駐執行</b></td><td>排程任務、重新啟動後繼續的目標，以及在 Telegram、Discord、Slack 和微信裡對話。</td></tr>
<tr><td><b>任何模型</b></td><td>300 多個服務商與本機模型；每個服務商可放多組金鑰，到達限額時自動切換。</td></tr>
</table>

每項功能及其設計：[功能索引](https://fzkuji.github.io/OpenProgram/docs/reference/design/README.html)。

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

參見[撰寫 Agent](https://fzkuji.github.io/OpenProgram/docs/capabilities/agentic-programming/writing-functions/agent.html)。

## 動態

- **2026-11-25 · [v1.5.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.5.0)** — 用手機或在任何地方透過內建 Tailscale 存取這台電腦；對話可以交給 Codex 執行；應用程式可以匯出成獨立軟體；新增截圖工具。
- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — 每個服務商可放多組金鑰與登入，到達限額時自動切換；回覆依任務分塊；排程任務有執行紀錄。
- **2026-11-15 · v1.3.0** — 服務商頁面顯示健康狀態、額度與花費；Activity 列出對話中工作的 Agent。

<details>
<summary>更早</summary>

- **2026-11-14 · v1.2.0** — 同一對話的分支可以並行執行，停止立即生效，Windows App 上線。
- **2026-11-10 · v1.1.0** — 可以從任一則已傳送的訊息回退；App 支援 Linux。
- **2026-11-06 · v1.0.0** — 1.x 的第一個版本：具分割畫面的桌面 App、背景執行的電腦操作、會議與常駐排程器。
- **2026-08-17 · v0.7.0** — 內建瀏覽器，與檔案、對話和終端機並排，Agent 可以讀取並操作。
- **2026-03-31 · v0.1.0** — 第一個公開版本：Agentic 函式——由模型執行各步驟的 Python 函式，執行過程記錄在脈絡樹中。

</details>

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

## 參與貢獻

歡迎問題回報、重現、測試、文件與程式碼：[問題回報](https://github.com/Fzkuji/OpenProgram/issues) · [拉取請求](https://github.com/Fzkuji/OpenProgram/pulls) · [貢獻指南](../.github/CONTRIBUTING.md)。

<!-- contributors-avatars -->
<p>
<a href="https://github.com/FuzzyKuji"><img src="https://avatars.githubusercontent.com/u/9404270?v=4&amp;s=48" width="24" height="24" alt="Maintainer" /></a>
<a href="https://github.com/Qi202"><img src="https://github.com/Qi202.png?size=48" width="24" height="24" alt="Qi202" /></a>
<a href="https://github.com/GithungDang"><img src="https://github.com/GithungDang.png?size=48" width="24" height="24" alt="GithungDang" /></a>
<a href="https://github.com/basil-k-aji-dev"><img src="https://github.com/basil-k-aji-dev.png?size=48" width="24" height="24" alt="basil-k-aji-dev" /></a>
<a href="https://github.com/binyangzhu000-sudo"><img src="https://github.com/binyangzhu000-sudo.png?size=48" width="24" height="24" alt="binyangzhu000-sudo" /></a>
</p>
<!-- /contributors-avatars -->

## 授權

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji。
