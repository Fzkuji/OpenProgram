<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-lockup.gif">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/logo-lockup-light.gif">
    <img src="docs/images/logo-lockup.gif" alt="OpenProgram" width="440">
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

<p align="center"><b>Ein Open-Source-Agent-Harness, in dem Agenten Python-Programme sind, die sich selbst verbessern.</b></p>

<p align="center">
  <a href="https://github.com/FuzzyKuji/OpenProgram/blob/main/.github/CHANGELOG.md"><img alt="Version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFuzzyKuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/FuzzyKuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square">
</p>

<p align="center">
  <a href="https://fuzzykuji.github.io/openprogram-site/">Website</a> · <a href="#install">Installation</a> · <a href="https://fuzzykuji.github.io/openprogram-site/docs/">Dokumentation</a>
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

- **Agenten sind Python-Klassen.** Kontrollfluss, Prüfungen und Wiederholungen sind normaler Code; `self(...)` ist ein Modellaufruf.
- **Selbstprogrammierung.** Agenten schreiben neue Methoden in ihre eigene Klasse, die ab dem nächsten Zug als Werkzeuge verfügbar sind.
- **Graph-Kontext.** Jeder Aufruf ist ein Knoten in einem DAG; Sub-Agenten laufen auf eigenen Zweigen und liefern nur ihr Ergebnis zurück.
- **Ein Arbeitsbereich.** Chat, Dateien, Terminal und ein integrierter Browser, den der Agent bedienen kann, nebeneinander.
- **Jedes Modell.** Anthropic, OpenAI, Gemini und lokale Server, mit mehreren Konten pro Anbieter.

<a id="install"></a>

## Installation

```bash
curl -fsSL https://fuzzykuji.github.io/openprogram-site/install | sh
```

Unter Windows (ab v1.2.0): `irm https://fuzzykuji.github.io/openprogram-site/install.ps1 | iex`. Die macOS-Desktop-App ist ein DMG auf der [Release-Seite](https://github.com/FuzzyKuji/OpenProgram/releases); die Linux-App liegt dort als deb und AppImage, die Windows-App kommt mit v1.1.1. Falls die Website nicht antwortet: `curl -fsSL https://raw.githubusercontent.com/FuzzyKuji/OpenProgram/main/docs/_static_root/install.sh | sh`. Weitere Optionen in der [Installationsanleitung](https://fuzzykuji.github.io/openprogram-site/docs/install/install.html) (Englisch).

## Schnellstart

```bash
openprogram        # Einrichtungsassistent, danach Chat im Terminal
openprogram web    # Arbeitsbereich unter http://localhost:18100
```

Weiter: [Erste Schritte](docs/start/GETTING_STARTED.html) · [Modelle](docs/models/README.html) · [gesamte Dokumentation](docs/README.html) (Englisch)

## Auf einen Blick

```python
from openprogram import Agent

class SupportAgent(Agent):
    def triage(self, ticket: str) -> str:
        """Classify the ticket, then draft a reply."""
        kind = self(ticket, choices=["bug", "question"])
        if kind == "bug":
            logs = search_logs(ticket)  # deine eigene Hilfsfunktion
            return self(f"Reply using:\n{logs}")
        return self("Draft a short reply.")
```

Das Modell entscheidet; Python sorgt dafür, dass die nötigen Schritte ausgeführt werden. Siehe [Agenten schreiben](docs/capabilities/agentic-programming/writing-functions/agent.html) (Englisch).

## Funktionen

Jeder Eintrag verlinkt auf sein Designdokument (Englisch).

### Framework

| | |
|---|---|
| **[Agentenklassen](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/function/agentic-program.html)** | Ein Agent ist eine Python-Klasse; Verzweigungen, Wiederholungen und Prüfungen sind Code, `self(...)` ist ein Modellaufruf. |
| **[Ausführungs-DAG](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/dag/overview.html)** | Jeder Nutzerzug, Modellaufruf und Funktionsaufruf ist ein Knoten, den du ansehen, verzweigen und erneut abspielen kannst. |
| **[Kontext](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/context/overview.html)** | Jeder Aufruf liest eine Auswahl von Knoten statt eines immer längeren Verlaufs, mit automatischer Verdichtung. |
| **[Selbstprogrammierung](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/workflows/workflow.html)** | Agenten schreiben neue Methoden in ihre eigene Klasse; ab dem nächsten Zug sind es Werkzeuge. |
| **[Geprüfte Entscheidungen](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/execution/next-step-decision.html)** | Das Modell wählt aus einem festen Menü, und Code prüft die Wahl, bevor etwas ausgeführt wird. |
| **[Sub-Agenten](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/collaboration/agent-collaboration.html)** | Sub-Agenten laufen auf eigenen Zweigen, optional in einem Git-Worktree, und liefern nur ihr Ergebnis. |
| **[Ereignisse](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/proactive/event-layer.html)** | Ein typisierter Ereignisbus; Plugins beobachten Ereignisse oder sperren Werkzeugaufrufe. |
| **[Dauerhaft laufende Agenten](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/always-on-agents.html)** | Agenten, die im Hintergrund weiterlaufen und auf Ereignisse reagieren. |
| **[Ziele und Wiederherstellung](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/reliability/goal-and-recovery.html)** | Langfristige Ziele überstehen Neustarts und machen dort weiter, wo sie aufgehört haben. |
| **[Fehlertoleranz](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/reliability/llm-fault-tolerance.html)** | Wiederholungen, Zeitlimits und Fehlerklassifizierung für jeden Modellaufruf. |

### Arbeitsbereich

| | |
|---|---|
| **[Desktop- und Web-Oberfläche](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/README.html)** | Die App auf macOS und Linux, ab v1.2.0 auch auf Windows; die Weboberfläche auf jedem davon oder einem Server. |
| **[Geteilte Ansichten](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/center-tabs-and-split-layout.html)** | Chats, Dateien, Terminals und Webseiten nebeneinander. |
| **[Integrierter Browser](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/browser/built-in-browser.html)** | Ein Browser, den der Agent liest und bedient, während du zusiehst. |
| **[Integriertes Terminal](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/integrated-terminal.html)** | Terminals im Arbeitsbereich, gemeinsam mit dem Agenten genutzt. |
| **[Computer use](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/computer-and-browser-use.html)** | Desktop-Apps, Browserseiten oder eine entfernte VM bedienen. |
| **[CLI und TUI](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/cli/README.html)** | Derselbe Agent im Terminal. |
| **[Fernzugriff](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/remote-web-access.html)** | Von einem anderen Gerät auf deine eigene Instanz zugreifen, nur für dich. |

### Agenten und Sicherheit

| | |
|---|---|
| **[Konfigurierbare Agenten](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent-configuration/agent-configuration-ui.html)** | Modell, Anweisungen, Werkzeuge und Gedächtniszugriff als wiederverwendbaren Agenten speichern. |
| **[Gedächtnis](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/memory/overview.html)** | Gedächtnis mit Quellenangaben, Suche und Schreiben im Hintergrund. |
| **[Berechtigungen und Sandbox](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/security/sandbox-architecture.html)** | Werkzeugaufrufe mit einer Notiz erlauben oder ablehnen; Befehle laufen in einer Sandbox. |
| **[Programs](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/programs/programs-architecture.html)** | Werkzeuge und Anwendungen als installierbare Programs. |

### Modelle und Integrationen

| | |
|---|---|
| **[Anbieter](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/README.html)** | Anthropic, OpenAI, Gemini und weitere, mit mehreren Konten pro Anbieter. |
| **[Lokale Modelle](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/local-models.html)** | Lokale Inferenzserver, optional mit Authentifizierung. |
| **[MCP](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/mcp-integration.html)** | MCP-Server nutzen oder OpenProgram selbst als MCP-Server anbieten. |
| **[Skills und Plugins](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/skills-and-plugins.html)** | Erweitere, was der Agent weiß und aufrufen kann. |
| **[Kanäle](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/channels/README.html)** | Sprich mit deinen Agenten über Telegram, Discord, Slack und WeChat. |
| **[Automatische Updates](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/distribution/automatic-updates.html)** | Signierte Releases, die sich an Ort und Stelle aktualisieren. |

## Zitieren

```bibtex
@inproceedings{qi2026llmascode,
  title     = {LLM-as-Code: Agentic Programming for Agent Harness},
  author    = {Qi, Junjia and Fu, Zichuan and Gao, Jingtong and Zhang, Wenlin and Yan, Hanyu and Wu, Xian and Zhao, Xiangyu},
  booktitle = {KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)},
  year      = {2026},
  url       = {https://arxiv.org/abs/2606.15874},
}
```

## Lizenz

[AGPL-3.0](LICENSE) © 2026 FuzzyKuji. Pflichten zu Nutzung, Änderung, Weitergabe und Netzwerkdiensten regelt der Lizenztext.

## Danksagung

Danke an alle, die mit Code, Meldungen, Reproduktionen, Tests, Dokumentation und Reviews beitragen.

<!-- contributors-avatars -->
<p>
<a href="https://github.com/FuzzyKuji"><img src="https://avatars.githubusercontent.com/u/9404270?v=4&amp;s=48" width="24" height="24" alt="Maintainer" /></a>
<a href="https://github.com/Qi202"><img src="https://github.com/Qi202.png?size=48" width="24" height="24" alt="Qi202" /></a>
<a href="https://github.com/GithungDang"><img src="https://github.com/GithungDang.png?size=48" width="24" height="24" alt="GithungDang" /></a>
<a href="https://github.com/basil-k-aji-dev"><img src="https://github.com/basil-k-aji-dev.png?size=48" width="24" height="24" alt="basil-k-aji-dev" /></a>
<a href="https://github.com/binyangzhu000-sudo"><img src="https://github.com/binyangzhu000-sudo.png?size=48" width="24" height="24" alt="binyangzhu000-sudo" /></a>
</p>
<!-- /contributors-avatars -->

[Issues](https://github.com/FuzzyKuji/OpenProgram/issues) · [Pull Requests](https://github.com/FuzzyKuji/OpenProgram/pulls)
