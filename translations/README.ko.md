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

<p align="center"><b>오픈소스 에이전트 하네스. 에이전트는 스스로 개선하는 Python 프로그램입니다.</b></p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/.github/CHANGELOG.md"><img alt="버전" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square">
</p>

<p align="center">
  <a href="https://fuzzykuji.github.io/openprogram-site/">웹사이트</a> · <a href="#install">설치</a> · <a href="https://fuzzykuji.github.io/openprogram-site/docs/">문서</a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

## 소식

- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — 프로바이더마다 여러 키와 로그인을 두고 한도에 닿으면 자동으로 다음 것으로 전환합니다. 답변은 작업 블록으로 묶이고, 예약 작업에 실행 기록이 생겼습니다. 이 버전부터 릴리스는 이 저장소에서 배포합니다.
- **2026-11-15 · v1.3.0** — 상태, 할당량, 지출을 보여 주는 프로바이더 페이지, 대화에서 일하는 에이전트 목록, 에이전트가 만든 파일 자동 열기.
- **2026-11-14 · v1.2.0** — 한 대화의 브랜치를 나란히 실행하고, 중지가 즉시 적용되며, Windows App이 나왔습니다.
- **2026-11-10 · v1.1.0** — 보낸 메시지 어디서든 되감기. App이 Linux를 지원합니다.
- **2026-11-06 · v1.0.0** — 1.x의 첫 릴리스: 분할 보기 데스크톱 App, 백그라운드 컴퓨터 사용, 회의, 상시 스케줄러.

<a id="install"></a>

## 설치

```bash
curl -fsSL https://fuzzykuji.github.io/openprogram-site/install | sh
```

Windows(v1.2.0부터): `irm https://fuzzykuji.github.io/openprogram-site/install.ps1 | iex`. macOS 데스크톱 App은 DMG로 [릴리스 페이지](https://github.com/Fzkuji/OpenProgram/releases)에서 받을 수 있습니다. Linux App은 같은 페이지에 deb와 AppImage로 있고, Windows App은 v1.2.0부터 제공됩니다. 웹사이트에 연결되지 않으면: `curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`. 다른 방법은 [설치 안내](https://fuzzykuji.github.io/openprogram-site/docs/install/install.html)(영어)를 보세요.

## 빠른 시작

```bash
openprogram        # 설정 마법사, 이어서 터미널 채팅
openprogram web    # 작업 공간 http://localhost:18100
```

다음: [시작하기](../docs/start/GETTING_STARTED.html) · [모델 설정](../docs/models/README.html) · [전체 문서](../docs/README.html)(영어)

## 한눈에 보기

```python
from openprogram import Agent

class SupportAgent(Agent):
    def triage(self, ticket: str) -> str:
        """Classify the ticket, then draft a reply."""
        kind = self(ticket, choices=["bug", "question"])
        if kind == "bug":
            logs = search_logs(ticket)  # 직접 만든 도우미 함수
            return self(f"Reply using:\n{logs}")
        return self("Draft a short reply.")
```

판단은 모델이 하고, 꼭 필요한 단계는 Python이 반드시 실행합니다. [에이전트 작성하기](../docs/capabilities/agentic-programming/writing-functions/agent.html)(영어)를 보세요.

## 기능

각 항목은 해당 설계 문서(영어)로 연결됩니다.

### 프레임워크

| | |
|---|---|
| **[에이전트 클래스](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/function/agentic-program.html)** | 에이전트는 Python 클래스입니다. 분기, 재시도, 검사는 코드이고 `self(...)`가 모델 호출입니다. |
| **[실행 DAG](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/dag/overview.html)** | 사용자 턴, 모델 호출, 함수 호출이 모두 노드가 되어 살펴보고, 분기하고, 다시 실행할 수 있습니다. |
| **[컨텍스트](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/context/overview.html)** | 각 호출은 계속 늘어나는 대화 기록 대신 선택된 노드를 읽으며, 자동으로 압축됩니다. |
| **[셀프 프로그래밍](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/workflows/workflow.html)** | 에이전트가 자기 클래스에 새 메서드를 쓰면 다음 턴부터 도구가 됩니다. |
| **[검증된 결정](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent/next-step-decision.html)** | 모델은 정해진 선택지에서 고르고, 실행 전에 코드가 그 선택을 검증합니다. |
| **[하위 에이전트](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/collaboration/agent-collaboration.html)** | 하위 에이전트는 자기 브랜치(필요하면 Git worktree)에서 실행되고 결과만 돌려줍니다. |
| **[이벤트](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/proactive/event-layer.html)** | 타입이 있는 이벤트 버스. 플러그인은 이벤트를 관찰하거나 도구 호출을 막을 수 있습니다. |
| **[상시 실행 에이전트](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/always-on-agents.html)** | 백그라운드에서 계속 실행되며 이벤트에 반응하는 에이전트. |
| **[목표와 복구](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/reliability/goal-and-recovery.html)** | 장기 목표는 재시작 후에도 남아 중단된 곳에서 이어집니다. |
| **[장애 허용](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/reliability/llm-fault-tolerance.html)** | 모든 모델 호출에 재시도, 시간 제한, 오류 분류가 있습니다. |

### 작업 공간

| | |
|---|---|
| **[데스크톱과 웹 UI](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/README.html)** | macOS와 Linux에서는 App, v1.2.0부터는 Windows에서도 App을 씁니다. 웹 UI는 이 모든 시스템과 서버에서 쓸 수 있습니다. |
| **[분할 창](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/center-tabs-and-split-layout.html)** | 채팅, 파일, 터미널, 웹 페이지를 나란히 배치. |
| **[내장 브라우저](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/browser/built-in-browser.html)** | 에이전트가 읽고 조작하는 브라우저를 옆에서 지켜볼 수 있습니다. |
| **[통합 터미널](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/integrated-terminal.html)** | 작업 공간 안의 터미널을 에이전트와 함께 씁니다. |
| **[Computer use](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/computer-use/computer-and-browser-use.html)** | 데스크톱 앱, 브라우저 페이지, 원격 VM을 조작합니다. |
| **[CLI와 TUI](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/cli/README.html)** | 같은 에이전트를 터미널에서. |
| **[원격 접속](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/remote-web-access.html)** | 다른 기기에서 내 인스턴스에 접속(본인만). |

### 에이전트와 안전

| | |
|---|---|
| **[에이전트 설정](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent-configuration/agent-configuration-ui.html)** | 모델, 지시, 도구, 메모리 권한을 재사용 가능한 에이전트로 저장. |
| **[메모리](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/memory/overview.html)** | 출처가 있는 메모리. 검색과 백그라운드 기록을 지원합니다. |
| **[권한과 샌드박스](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/security/sandbox-architecture.html)** | 메모와 함께 도구 호출을 승인하거나 거부합니다. 명령은 샌드박스에서 실행됩니다. |
| **[Programs](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/programs/programs-architecture.html)** | 도구와 애플리케이션을 설치 가능한 Program으로 다룹니다. |

### 모델과 연동

| | |
|---|---|
| **[제공자](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/README.html)** | Anthropic, OpenAI, Gemini 등. 제공자마다 여러 계정. |
| **[로컬 모델](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/models/local-models.html)** | 로컬 추론 서버, 인증은 선택 사항. |
| **[MCP](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/mcp-integration.html)** | MCP 서버를 쓰거나 OpenProgram을 MCP 서버로 공개합니다. |
| **[스킬과 플러그인](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/skills-and-plugins.html)** | 에이전트의 지식과 호출할 수 있는 도구를 확장합니다. |
| **[채널](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/channels/README.html)** | Telegram, Discord, Slack, WeChat에서 에이전트와 대화. |
| **[자동 업데이트](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/distribution/automatic-updates.html)** | 서명된 릴리스를 그 자리에서 업데이트. |

## 인용

```bibtex
@inproceedings{qi2026llmascode,
  title     = {LLM-as-Code: Agentic Programming for Agent Harness},
  author    = {Qi, Junjia and Fu, Zichuan and Gao, Jingtong and Zhang, Wenlin and Yan, Hanyu and Wu, Xian and Zhao, Xiangyu},
  booktitle = {KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)},
  year      = {2026},
  url       = {https://arxiv.org/abs/2606.15874},
}
```

## 라이선스

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji. 사용, 수정, 배포, 네트워크 서비스에 관한 의무는 라이선스 원문을 따릅니다.

## 감사의 말

코드, 문제 보고, 재현, 테스트, 문서, 리뷰로 기여해 주신 분들께 감사드립니다.

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
