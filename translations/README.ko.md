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

<p align="center"><b>에이전트는 무엇이든 프로그래밍합니다.</b><br>워크플로, AI가 내장된 앱, 그리고 에이전트 자신까지. 에이전트가 Python 프로그램인 오픈소스 에이전트 하네스입니다.</p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/releases/latest"><img alt="버전" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Fzkuji/OpenProgram?style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://arxiv.org/abs/2606.15874"><img alt="Paper" src="https://img.shields.io/badge/arXiv-2606.15874-b31b1b?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/"><b>웹사이트</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/"><b>문서</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/install/download.html"><b>App 다운로드</b></a> · <a href="https://arxiv.org/abs/2606.15874"><b>논문</b></a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="../docs/images/openprogram-tour-dark.webp">
      <source media="(prefers-color-scheme: light)" srcset="../docs/images/openprogram-tour-light.webp">
      <img src="../docs/images/openprogram-tour-light.webp" alt="OpenProgram 둘러보기: 처음부터 끝까지 하나의 작업, 상주 에이전트, 모든 기기, 무엇이든 프로그래밍하는 에이전트" width="100%">
    </picture>
  </a>
</p>

<a id="install"></a>

## 설치

**macOS · Linux**

```bash
curl -fsSL https://fzkuji.github.io/OpenProgram/install | sh
```

**Windows (PowerShell)**

```powershell
irm https://fzkuji.github.io/OpenProgram/install.ps1 | iex
```

**데스크톱 App** — [macOS Apple Silicon](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-arm64.dmg) · [macOS Intel](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-x64.dmg) · [Windows](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-win-x64.exe) · [Linux (deb, AppImage)](https://fzkuji.github.io/OpenProgram/docs/install/download.html). App은 스스로 업데이트됩니다.

그다음 실행:

```bash
openprogram        # 모델을 연결한 뒤 터미널에서 채팅
openprogram web    # 작업 공간 열기 http://localhost:18100
```

웹사이트에 연결되지 않으면: `curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`. 다른 방법: [설치 안내](https://fzkuji.github.io/OpenProgram/docs/install/install.html) · [시작하기](https://fzkuji.github.io/OpenProgram/docs/start/GETTING_STARTED.html).

## 왜 OpenProgram인가

<table>
<tr><td><b>에이전트는 Python 프로그램</b></td><td>분기, 재시도, 검사는 일반 코드이고 <code>self(...)</code>가 모델 호출입니다. 판단은 모델이 하고, 꼭 필요한 단계는 Python이 반드시 실행합니다.</td></tr>
<tr><td><b>스스로 프로그래밍</b></td><td>에이전트가 자기 클래스에 새 메서드를 씁니다. 다음 턴부터 각 메서드는 호출할 수 있는 도구가 됩니다.</td></tr>
<tr><td><b>대화 기록이 아닌 그래프</b></td><td>모든 턴, 모델 호출, 함수 호출이 노드가 되어 살펴보고, 분기하고, 다시 실행할 수 있습니다. 하위 에이전트는 자기 브랜치에서 일하고 결과만 돌려줍니다.</td></tr>
<tr><td><b>검증된 결정</b></td><td>모델은 정해진 선택지에서 고르고, 실행 전에 코드가 그 선택을 검증합니다.</td></tr>
<tr><td><b>하나의 작업 공간</b></td><td>채팅, 파일, 터미널, 내장 브라우저를 나란히. 데스크톱 App, 웹 UI, 터미널 UI, CLI에서 씁니다.</td></tr>
<tr><td><b>컴퓨터 조작</b></td><td>데스크톱 앱, 브라우저 페이지, 원격 VM을 백그라운드에서 조작하며 화면을 빼앗지 않습니다.</td></tr>
<tr><td><b>상시 실행</b></td><td>예약 작업, 재시작 후에도 남는 목표, Telegram, Discord, Slack, WeChat에서의 대화.</td></tr>
<tr><td><b>어떤 모델이든</b></td><td>300개 이상의 프로바이더와 로컬 모델. 프로바이더마다 여러 키를 두고 한도에 닿으면 자동으로 전환합니다.</td></tr>
</table>

모든 기능과 그 설계: [기능 색인](https://fzkuji.github.io/OpenProgram/docs/reference/design/README.html).

## 한눈에 보기

```python
from openprogram import Agent

class SupportAgent(Agent):
    def triage(self, ticket: str) -> str:
        """Classify the ticket, then draft a reply."""
        kind = self(ticket, choices=["bug", "question"])
        if kind == "bug":
            logs = search_logs(ticket)  # your own helper
            return self(f"Reply using:\n{logs}")
        return self("Draft a short reply.")
```

[에이전트 작성하기](https://fzkuji.github.io/OpenProgram/docs/capabilities/agentic-programming/writing-functions/agent.html)를 보세요.

## 소식

- **2026-11-25 · [v1.5.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.5.0)** — 휴대폰이나 내장 Tailscale로 어디서든 이 컴퓨터에 접속하고, Codex에서 돌아가는 채팅, 독립 소프트웨어로 내보낼 수 있는 애플리케이션, 스크린샷 도구가 추가되었습니다.
- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — 프로바이더마다 여러 키와 로그인을 두고 한도에 닿으면 자동으로 전환합니다. 답변에 작업 블록이 생기고, 예약 작업에 실행 기록이 생겼습니다.
- **2026-11-15 · v1.3.0** — 상태, 할당량, 지출을 보여 주는 프로바이더 페이지. 대화에서 일하는 에이전트가 Activity에 표시됩니다.

<details>
<summary>이전 소식</summary>

- **2026-11-14 · v1.2.0** — 한 대화의 브랜치를 나란히 실행하고, 중지가 즉시 적용되며, Windows App이 나왔습니다.
- **2026-11-10 · v1.1.0** — 보낸 메시지 어디서든 되감기. App이 Linux를 지원합니다.
- **2026-11-06 · v1.0.0** — 1.x의 첫 릴리스: 분할 보기 데스크톱 App, 백그라운드 컴퓨터 사용, 회의, 상시 스케줄러.
- **2026-08-17 · v0.7.0** — 파일, 채팅, 터미널 옆의 내장 브라우저. 에이전트가 읽고 조작할 수 있습니다.
- **2026-03-31 · v0.1.0** — 첫 공개 릴리스: 에이전트 함수. 모델이 각 단계를 수행하는 Python 함수이며, 실행은 컨텍스트 트리에 기록됩니다.

</details>

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

## 기여

문제 보고, 재현, 테스트, 문서, 코드 모두 환영합니다: [Issues](https://github.com/Fzkuji/OpenProgram/issues) · [Pull requests](https://github.com/Fzkuji/OpenProgram/pulls) · [기여 안내](../.github/CONTRIBUTING.md).

<!-- contributors-avatars -->
<p>
<a href="https://github.com/FuzzyKuji"><img src="https://avatars.githubusercontent.com/u/9404270?v=4&amp;s=48" width="24" height="24" alt="Maintainer" /></a>
<a href="https://github.com/Qi202"><img src="https://github.com/Qi202.png?size=48" width="24" height="24" alt="Qi202" /></a>
<a href="https://github.com/GithungDang"><img src="https://github.com/GithungDang.png?size=48" width="24" height="24" alt="GithungDang" /></a>
<a href="https://github.com/basil-k-aji-dev"><img src="https://github.com/basil-k-aji-dev.png?size=48" width="24" height="24" alt="basil-k-aji-dev" /></a>
<a href="https://github.com/binyangzhu000-sudo"><img src="https://github.com/binyangzhu000-sudo.png?size=48" width="24" height="24" alt="binyangzhu000-sudo" /></a>
</p>
<!-- /contributors-avatars -->

## 라이선스

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji.
