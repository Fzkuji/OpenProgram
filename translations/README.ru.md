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

<p align="center"><b>Открытый harness для агентов, где агенты — это программы на Python, которые сами себя улучшают.</b></p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/.github/CHANGELOG.md"><img alt="Версия" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square">
</p>

<p align="center">
  <a href="https://fuzzykuji.github.io/openprogram-site/">Сайт</a> · <a href="#install">Установка</a> · <a href="https://fuzzykuji.github.io/openprogram-site/docs/">Документация</a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

## Новости

- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — Несколько ключей и входов на одного провайдера с автоматическим переключением при достижении лимита, блоки задач в ответах и история запусков у задач по расписанию. Начиная с этой версии релизы публикуются в этом репозитории.
- **2026-11-15 · v1.3.0** — Страница провайдеров с состоянием, квотой и расходами; агенты, работающие в разговоре, перечислены в Activity; файлы, созданные агентом, открываются сами.
- **2026-11-14 · v1.2.0** — Ветки одного разговора выполняются параллельно, «Стоп» срабатывает сразу, вышло приложение для Windows.
- **2026-11-10 · v1.1.0** — Откат от любого отправленного сообщения; приложение появилось для Linux.
- **2026-11-06 · v1.0.0** — Первый релиз линейки 1.x: настольное приложение с разделёнными окнами, работа с компьютером в фоне, встречи и постоянно работающий планировщик.
- **2026-08-17 · v0.7.0** — Встроенный браузер рядом с файлами, чатом и терминалом; агенты могут читать его и управлять им.
- **2026-03-31 · v0.1.0** — Первый публичный релиз: агентные функции — функции Python, шаги которых выполняет модель, с записью в дерево контекста.

<a id="install"></a>

## Установка

```bash
curl -fsSL https://fuzzykuji.github.io/openprogram-site/install | sh
```

В Windows (начиная с v1.2.0): `irm https://fuzzykuji.github.io/openprogram-site/install.ps1 | iex`. Настольное приложение для macOS — DMG на [странице релизов](https://github.com/Fzkuji/OpenProgram/releases); приложение для Linux доступно там же в виде deb и AppImage, а приложение для Windows появится в v1.1.1. Если сайт не отвечает: `curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`. Другие варианты — в [руководстве по установке](https://fuzzykuji.github.io/openprogram-site/docs/install/install.html) (на английском).

## Быстрый старт

```bash
openprogram        # мастер настройки, затем чат в терминале
openprogram web    # рабочее пространство на http://localhost:18100
```

Дальше: [начало работы](../docs/start/GETTING_STARTED.html) · [модели](../docs/models/README.html) · [вся документация](../docs/README.html) (на английском)

## Коротко

```python
from openprogram import Agent

class SupportAgent(Agent):
    def triage(self, ticket: str) -> str:
        """Classify the ticket, then draft a reply."""
        kind = self(ticket, choices=["bug", "question"])
        if kind == "bug":
            logs = search_logs(ticket)  # ваша собственная вспомогательная функция
            return self(f"Reply using:\n{logs}")
        return self("Draft a short reply.")
```

Решает модель; Python гарантирует, что нужные шаги будут выполнены. См. [как писать агентов](../docs/capabilities/agentic-programming/writing-functions/agent.html) (на английском).

## Возможности

Каждый пункт ведёт на свой проектный документ (на английском).

### Фреймворк

| | |
|---|---|
| **[Классы агентов](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/function/agentic-program.html)** | Агент — это класс Python; ветвления, повторы и проверки — код, `self(...)` — вызов модели. |
| **[DAG выполнения](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/dag/overview.html)** | Каждый ход пользователя, вызов модели и вызов функции — узел, который можно изучить, разветвить и воспроизвести. |
| **[Контекст](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/context/overview.html)** | Каждый вызов читает выбранные узлы, а не растущую переписку, с автоматическим сжатием. |
| **[Самопрограммирование](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/workflows/workflow.html)** | Агенты дописывают новые методы в свой класс; со следующего хода это инструменты. |
| **[Проверяемые решения](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent/next-step-decision.html)** | Модель выбирает из фиксированного меню, а код проверяет выбор до любого выполнения. |
| **[Субагенты](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/collaboration/agent-collaboration.html)** | Субагенты работают в своей ветке, при желании в Git worktree, и возвращают только результат. |
| **[События](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/proactive/event-layer.html)** | Типизированная шина событий; плагины наблюдают за событиями или блокируют вызовы инструментов. |
| **[Постоянно работающие агенты](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/always-on-agents.html)** | Агенты, которые продолжают работать в фоне и реагируют на события. |
| **[Цели и восстановление](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/reliability/goal-and-recovery.html)** | Долгосрочные цели переживают перезапуск и продолжаются с места остановки. |
| **[Отказоустойчивость](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/reliability/llm-fault-tolerance.html)** | Повторы, тайм-ауты и классификация ошибок для каждого вызова модели. |

### Рабочее пространство

| | |
|---|---|
| **[Настольный и веб-интерфейс](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/README.html)** | Приложение на macOS и Linux, а начиная с v1.2.0 и на Windows; веб-интерфейс — на любой из этих систем или на сервере. |
| **[Разделённые панели](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/center-tabs-and-split-layout.html)** | Чаты, файлы, терминалы и веб-страницы рядом. |
| **[Встроенный браузер](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/browser/built-in-browser.html)** | Браузер, который агент читает и которым управляет у вас на глазах. |
| **[Встроенный терминал](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/integrated-terminal.html)** | Терминалы в рабочем пространстве, общие с агентом. |
| **[Computer use](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/computer-use/computer-and-browser-use.html)** | Управление настольными приложениями, страницами браузера или удалённой ВМ. |
| **[CLI и TUI](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/cli/README.html)** | Тот же агент из терминала. |
| **[Удалённый доступ](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/remote-web-access.html)** | Доступ к своему экземпляру с другого устройства, только для владельца. |

### Агенты и безопасность

| | |
|---|---|
| **[Настраиваемые агенты](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent-configuration/agent-configuration-ui.html)** | Сохраните модель, инструкции, инструменты и доступ к памяти как переиспользуемого агента. |
| **[Память](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/memory/overview.html)** | Память с источниками, поиском и фоновой записью. |
| **[Права и песочница](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/security/sandbox-architecture.html)** | Разрешайте или запрещайте вызовы инструментов с пояснением; команды выполняются в песочнице. |
| **[Programs](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/programs/programs-architecture.html)** | Инструменты и приложения как устанавливаемые Programs. |

### Модели и интеграции

| | |
|---|---|
| **[Провайдеры](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/README.html)** | Anthropic, OpenAI, Gemini и другие, по несколько аккаунтов на провайдера. |
| **[Локальные модели](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/models/local-models.html)** | Локальные серверы инференса, с необязательной аутентификацией. |
| **[MCP](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/mcp-integration.html)** | Подключайте MCP-серверы или публикуйте OpenProgram как MCP-сервер. |
| **[Skills и плагины](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/skills-and-plugins.html)** | Расширяйте знания агента и набор доступных ему вызовов. |
| **[Каналы](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/channels/README.html)** | Общайтесь с агентами в Telegram, Discord, Slack и WeChat. |
| **[Автообновления](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/distribution/automatic-updates.html)** | Подписанные релизы, обновляющиеся на месте. |

## Цитирование

```bibtex
@inproceedings{qi2026llmascode,
  title     = {LLM-as-Code: Agentic Programming for Agent Harness},
  author    = {Qi, Junjia and Fu, Zichuan and Gao, Jingtong and Zhang, Wenlin and Yan, Hanyu and Wu, Xian and Zhao, Xiangyu},
  booktitle = {KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)},
  year      = {2026},
  url       = {https://arxiv.org/abs/2606.15874},
}
```

## Лицензия

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji. Обязательства по использованию, изменению, распространению и сетевому доступу определяются текстом лицензии.

## Благодарности

Спасибо всем, кто помогает кодом, сообщениями об ошибках, воспроизведениями, тестами, документацией и ревью.

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
