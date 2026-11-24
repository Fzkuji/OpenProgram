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

<p align="center"><b>オープンソースのエージェントハーネス。エージェントは自らを改良する Python プログラムです。</b></p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/.github/CHANGELOG.md"><img alt="バージョン" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square">
</p>

<p align="center">
  <a href="https://fuzzykuji.github.io/openprogram-site/">ウェブサイト</a> · <a href="#install">インストール</a> · <a href="https://fuzzykuji.github.io/openprogram-site/docs/">ドキュメント</a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

## ニュース

- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — プロバイダーごとに複数のキーとサインインを持ち、上限に達すると自動で次に切り替え。返信はタスクブロックにまとまり、スケジュールタスクに実行履歴が付きました。このバージョンからリリースはこのリポジトリで公開します。
- **2026-11-15 · v1.3.0** — 状態・クォータ・利用額を示すプロバイダーページ、会話で動くエージェントの一覧、エージェントが作ったファイルの自動表示。
- **2026-11-14 · v1.2.0** — 1 つの会話のブランチを並行実行、停止は即時に反映、Windows App を提供。
- **2026-11-10 · v1.1.0** — 送信済みの任意のメッセージから巻き戻し。App が Linux に対応。
- **2026-11-06 · v1.0.0** — 1.x 系の最初のリリース：分割ビューのデスクトップ App、バックグラウンドのコンピューター操作、会議、常駐スケジューラー。
- **2026-08-17 · v0.7.0** — ファイル、チャット、ターミナルの隣に内蔵ブラウザー。エージェントが読み取り、操作できます。
- **2026-03-31 · v0.1.0** — 最初の公開リリース：エージェント関数。モデルが各ステップを実行する Python 関数で、実行はコンテキストツリーに記録されます。

<a id="install"></a>

## インストール

```bash
curl -fsSL https://fuzzykuji.github.io/openprogram-site/install | sh
```

Windows（v1.2.0 から）：`irm https://fuzzykuji.github.io/openprogram-site/install.ps1 | iex`。macOS デスクトップ App は DMG として[リリースページ](https://github.com/Fzkuji/OpenProgram/releases)で配布しています。Linux App は同じページに deb と AppImage があり、Windows App は v1.2.0 から提供します。Web サイトに接続できない場合：`curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`。その他の方法は[インストールガイド](https://fuzzykuji.github.io/openprogram-site/docs/install/install.html)（英語）を参照してください。

## クイックスタート

```bash
openprogram        # セットアップウィザード、続いてターミナルチャット
openprogram web    # ワークスペース（http://localhost:18100）
```

次へ：[はじめに](../docs/start/GETTING_STARTED.html) · [モデル設定](../docs/models/README.html) · [全ドキュメント](../docs/README.html)（英語）

## ひと目でわかる

```python
from openprogram import Agent

class SupportAgent(Agent):
    def triage(self, ticket: str) -> str:
        """Classify the ticket, then draft a reply."""
        kind = self(ticket, choices=["bug", "question"])
        if kind == "bug":
            logs = search_logs(ticket)  # 自分で用意するヘルパー関数
            return self(f"Reply using:\n{logs}")
        return self("Draft a short reply.")
```

判断はモデルが行い、必要な手順は Python が確実に実行します。[エージェントの書き方](../docs/capabilities/agentic-programming/writing-functions/agent.html)（英語）を参照してください。

## 機能

各項目は対応する設計ドキュメント（英語）にリンクしています。

### フレームワーク

| | |
|---|---|
| **[エージェントクラス](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/function/agentic-program.html)** | エージェントは Python クラス。分岐、リトライ、チェックはコードで、`self(...)` がモデル呼び出しです。 |
| **[実行 DAG](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/dag/overview.html)** | ユーザーのターン、モデル呼び出し、関数呼び出しはすべてノードとなり、確認、分岐、再実行ができます。 |
| **[コンテキスト](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/context/overview.html)** | 各呼び出しは伸び続ける会話履歴ではなく選ばれたノードを読み、自動で圧縮されます。 |
| **[セルフプログラミング](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/workflows/workflow.html)** | エージェントが自分のクラスに新しいメソッドを書き、次のターンからツールになります。 |
| **[検証付きの判断](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent/next-step-decision.html)** | モデルは決められた選択肢から選び、実行前にコードがその選択を検証します。 |
| **[サブエージェント](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/collaboration/agent-collaboration.html)** | サブエージェントは自分のブランチ（必要なら Git worktree）で動き、結果だけを返します。 |
| **[イベント](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/proactive/event-layer.html)** | 型付きのイベントバス。プラグインはイベントを監視したり、ツール呼び出しを止めたりできます。 |
| **[常駐エージェント](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/always-on-agents.html)** | バックグラウンドで動き続け、イベントに反応するエージェント。 |
| **[ゴールと復旧](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/reliability/goal-and-recovery.html)** | 長期のゴールは再起動後も残り、中断したところから再開します。 |
| **[フォールトトレランス](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/reliability/llm-fault-tolerance.html)** | すべてのモデル呼び出しにリトライ、タイムアウト、エラー分類があります。 |

### ワークスペース

| | |
|---|---|
| **[デスクトップと Web UI](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/README.html)** | macOS と Linux では App、v1.2.0 からは Windows でも App。Web UI はそのいずれでもサーバーでも使えます。 |
| **[分割ペイン](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/center-tabs-and-split-layout.html)** | チャット、ファイル、ターミナル、Web ページを並べて表示。 |
| **[内蔵ブラウザ](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/browser/built-in-browser.html)** | エージェントが読んで操作するブラウザを、そばで見守れます。 |
| **[統合ターミナル](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/integrated-terminal.html)** | ワークスペース内のターミナルをエージェントと共有。 |
| **[Computer use](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/computer-use/computer-and-browser-use.html)** | デスクトップアプリ、ブラウザのページ、リモート VM を操作します。 |
| **[CLI と TUI](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/cli/README.html)** | 同じエージェントをターミナルから。 |
| **[リモートアクセス](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/remote-web-access.html)** | 別のデバイスから自分のインスタンスにアクセス（本人のみ）。 |

### エージェントと安全性

| | |
|---|---|
| **[エージェント設定](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent-configuration/agent-configuration-ui.html)** | モデル、指示、ツール、メモリ権限を再利用できるエージェントとして保存。 |
| **[メモリ](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/memory/overview.html)** | 出典付きのメモリ。検索とバックグラウンドでの書き込みに対応。 |
| **[権限とサンドボックス](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/security/sandbox-architecture.html)** | ツール呼び出しをメモ付きで許可または拒否。コマンドはサンドボックスで実行されます。 |
| **[Programs](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/programs/programs-architecture.html)** | ツールとアプリケーションをインストール可能な Program として扱います。 |

### モデルと連携

| | |
|---|---|
| **[プロバイダー](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/README.html)** | Anthropic、OpenAI、Gemini など。プロバイダーごとに複数アカウント。 |
| **[ローカルモデル](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/models/local-models.html)** | ローカル推論サーバー。認証も設定可能。 |
| **[MCP](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/mcp-integration.html)** | MCP サーバーを使う、または OpenProgram を MCP サーバーとして公開。 |
| **[スキルとプラグイン](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/skills-and-plugins.html)** | エージェントの知識と呼び出せるツールを拡張します。 |
| **[チャンネル](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/channels/README.html)** | Telegram、Discord、Slack、WeChat からエージェントと会話。 |
| **[自動更新](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/distribution/automatic-updates.html)** | 署名済みリリースをその場で更新。 |

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

## ライセンス

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji。利用、改変、配布、ネットワークサービス提供に関する義務はライセンス本文に従います。

## 謝辞

コード、報告、再現、テスト、ドキュメント、レビューで貢献してくださった皆さんに感謝します。

<!-- contributors-avatars -->
<p>
<a href="https://github.com/FuzzyKuji"><img src="https://avatars.githubusercontent.com/u/9404270?v=4&amp;s=48" width="24" height="24" alt="Maintainer" /></a>
<a href="https://github.com/Qi202"><img src="https://github.com/Qi202.png?size=48" width="24" height="24" alt="Qi202" /></a>
<a href="https://github.com/GithungDang"><img src="https://github.com/GithungDang.png?size=48" width="24" height="24" alt="GithungDang" /></a>
<a href="https://github.com/basil-k-aji-dev"><img src="https://github.com/basil-k-aji-dev.png?size=48" width="24" height="24" alt="basil-k-aji-dev" /></a>
<a href="https://github.com/binyangzhu000-sudo"><img src="https://github.com/binyangzhu000-sudo.png?size=48" width="24" height="24" alt="binyangzhu000-sudo" /></a>
</p>
<!-- /contributors-avatars -->

[Issues](https://github.com/Fzkuji/OpenProgram/issues) · [Pull requests](https://github.com/Fzkuji/OpenProgram/pulls)
