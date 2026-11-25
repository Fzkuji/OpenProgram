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

<p align="center"><b>Агенты программируют что угодно.</b><br>Рабочие процессы, приложения со встроенным ИИ и сами агенты. Открытый harness для агентов, где агенты — это программы на Python.</p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/releases/latest"><img alt="Версия" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Fzkuji/OpenProgram?style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://arxiv.org/abs/2606.15874"><img alt="Paper" src="https://img.shields.io/badge/arXiv-2606.15874-b31b1b?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/"><b>Сайт</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/"><b>Документация</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/install/download.html"><b>Скачать приложение</b></a> · <a href="https://arxiv.org/abs/2606.15874"><b>Статья</b></a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="../docs/images/openprogram-tour-dark.webp">
      <source media="(prefers-color-scheme: light)" srcset="../docs/images/openprogram-tour-light.webp">
      <img src="../docs/images/openprogram-tour-light.webp" alt="Обзор OpenProgram: задача от начала до конца, постоянно работающие агенты, любые устройства и агенты, которые программируют что угодно" width="100%">
    </picture>
  </a>
</p>

<a id="install"></a>

## Установка

**macOS · Linux**

```bash
curl -fsSL https://fzkuji.github.io/OpenProgram/install | sh
```

**Windows (PowerShell)**

```powershell
irm https://fzkuji.github.io/OpenProgram/install.ps1 | iex
```

**Настольное приложение** — [macOS Apple Silicon](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-arm64.dmg) · [macOS Intel](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-x64.dmg) · [Windows](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-win-x64.exe) · [Linux (deb, AppImage)](https://fzkuji.github.io/OpenProgram/docs/install/download.html). Приложение обновляется само.

Затем запустите:

```bash
openprogram        # подключите модель, затем чат в терминале
openprogram web    # открыть рабочее пространство на http://localhost:18100
```

Если сайт не отвечает: `curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`. Другие варианты: [руководство по установке](https://fzkuji.github.io/OpenProgram/docs/install/install.html) · [начало работы](https://fzkuji.github.io/OpenProgram/docs/start/GETTING_STARTED.html).

## Почему OpenProgram

<table>
<tr><td><b>Агенты — это программы на Python</b></td><td>Ветвления, повторы и проверки — обычный код; <code>self(...)</code> — вызов модели. Решает модель, а Python гарантирует, что нужные шаги будут выполнены.</td></tr>
<tr><td><b>Они программируют себя сами</b></td><td>Агент дописывает новые методы в свой класс. Со следующего хода каждый из них — инструмент, который агент может вызвать.</td></tr>
<tr><td><b>Граф, а не переписка</b></td><td>Каждый ход, вызов модели и вызов функции — узел, который можно изучить, разветвить и воспроизвести. Субагенты работают в своей ветке и возвращают только результат.</td></tr>
<tr><td><b>Проверяемые решения</b></td><td>Модель выбирает из фиксированного меню, а код проверяет выбор до любого выполнения.</td></tr>
<tr><td><b>Одно рабочее пространство</b></td><td>Чаты, файлы, терминалы и встроенный браузер рядом — в настольном приложении, веб-интерфейсе, терминальном интерфейсе или CLI.</td></tr>
<tr><td><b>Работает с вашим компьютером</b></td><td>Управляет настольными приложениями, страницами браузера и удалёнными ВМ в фоне, не занимая ваш экран.</td></tr>
<tr><td><b>Всегда на связи</b></td><td>Задачи по расписанию, цели, которые переживают перезапуск, и чат через Telegram, Discord, Slack и WeChat.</td></tr>
<tr><td><b>Любая модель</b></td><td>Более 300 провайдеров и локальные модели; несколько ключей на провайдера с автоматическим переключением при достижении лимита.</td></tr>
</table>

Все возможности и их устройство: [указатель возможностей](https://fzkuji.github.io/OpenProgram/docs/reference/design/README.html).

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

См. [как писать агентов](https://fzkuji.github.io/OpenProgram/docs/capabilities/agentic-programming/writing-functions/agent.html).

## Новости

- **2026-11-25 · [v1.5.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.5.0)** — Доступ к этому компьютеру с телефона или откуда угодно через встроенный Tailscale, чаты на Codex, приложения, которые экспортируются как отдельные программы, и инструмент снимков экрана.
- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — Несколько ключей и входов на одного провайдера с автоматическим переключением при достижении лимита, блоки задач в ответах и история запусков у задач по расписанию.
- **2026-11-15 · v1.3.0** — Страница провайдеров с состоянием, квотой и расходами; агенты, работающие в разговоре, перечислены в Activity.

<details>
<summary>Ранее</summary>

- **2026-11-14 · v1.2.0** — Ветки одного разговора выполняются параллельно, «Стоп» срабатывает сразу, вышло приложение для Windows.
- **2026-11-10 · v1.1.0** — Откат от любого отправленного сообщения; приложение появилось для Linux.
- **2026-11-06 · v1.0.0** — Первый релиз линейки 1.x: настольное приложение с разделёнными окнами, работа с компьютером в фоне, встречи и постоянно работающий планировщик.
- **2026-08-17 · v0.7.0** — Встроенный браузер рядом с файлами, чатом и терминалом; агенты могут читать его и управлять им.
- **2026-03-31 · v0.1.0** — Первый публичный релиз: агентные функции — функции Python, шаги которых выполняет модель, с записью в дерево контекста.

</details>

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

## Участие

Сообщения об ошибках, воспроизведения, тесты, документация и код — всё приветствуется: [issues](https://github.com/Fzkuji/OpenProgram/issues) · [pull requests](https://github.com/Fzkuji/OpenProgram/pulls) · [руководство для участников](../.github/CONTRIBUTING.md).

<!-- contributors-avatars -->
<p>
<a href="https://github.com/FuzzyKuji"><img src="https://avatars.githubusercontent.com/u/9404270?v=4&amp;s=48" width="24" height="24" alt="Maintainer" /></a>
<a href="https://github.com/Qi202"><img src="https://github.com/Qi202.png?size=48" width="24" height="24" alt="Qi202" /></a>
<a href="https://github.com/GithungDang"><img src="https://github.com/GithungDang.png?size=48" width="24" height="24" alt="GithungDang" /></a>
<a href="https://github.com/basil-k-aji-dev"><img src="https://github.com/basil-k-aji-dev.png?size=48" width="24" height="24" alt="basil-k-aji-dev" /></a>
<a href="https://github.com/binyangzhu000-sudo"><img src="https://github.com/binyangzhu000-sudo.png?size=48" width="24" height="24" alt="binyangzhu000-sudo" /></a>
</p>
<!-- /contributors-avatars -->

## Лицензия

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji.
