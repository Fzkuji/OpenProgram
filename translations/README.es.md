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

<p align="center"><b>Los agentes programan cualquier cosa.</b><br>Flujos de trabajo, apps con IA integrada y los propios agentes. Un harness de agentes de código abierto donde los agentes son programas Python.</p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/releases/latest"><img alt="Versión" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Fzkuji/OpenProgram?style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://arxiv.org/abs/2606.15874"><img alt="Paper" src="https://img.shields.io/badge/arXiv-2606.15874-b31b1b?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/"><b>Sitio web</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/"><b>Documentación</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/install/download.html"><b>Descargar la App</b></a> · <a href="https://arxiv.org/abs/2606.15874"><b>Artículo</b></a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="../docs/images/openprogram-tour-dark.webp">
      <source media="(prefers-color-scheme: light)" srcset="../docs/images/openprogram-tour-light.webp">
      <img src="../docs/images/openprogram-tour-light.webp" alt="Recorrido por OpenProgram: una tarea de principio a fin, agentes siempre activos, todos tus dispositivos y agentes que programan cualquier cosa" width="100%">
    </picture>
  </a>
</p>

<a id="install"></a>

## Instalación

**macOS · Linux**

```bash
curl -fsSL https://fzkuji.github.io/OpenProgram/install | sh
```

**Windows (PowerShell)**

```powershell
irm https://fzkuji.github.io/OpenProgram/install.ps1 | iex
```

**App de escritorio** — [macOS Apple Silicon](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-arm64.dmg) · [macOS Intel](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-x64.dmg) · [Windows](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-win-x64.exe) · [Linux (deb, AppImage)](https://fzkuji.github.io/OpenProgram/docs/install/download.html). La App se actualiza sola.

Después, ejecuta:

```bash
openprogram        # conecta un modelo y chatea en la terminal
openprogram web    # abre el espacio de trabajo en http://localhost:18100
```

Si el sitio web no responde: `curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`. Más opciones: [guía de instalación](https://fzkuji.github.io/OpenProgram/docs/install/install.html) · [primeros pasos](https://fzkuji.github.io/OpenProgram/docs/start/GETTING_STARTED.html).

## Por qué OpenProgram

<table>
<tr><td><b>Los agentes son programas Python</b></td><td>Las ramas, los reintentos y las comprobaciones son código normal; <code>self(...)</code> es una llamada al modelo. El modelo decide y Python garantiza que se ejecuten los pasos necesarios.</td></tr>
<tr><td><b>Se programan a sí mismos</b></td><td>Un agente escribe métodos nuevos en su propia clase. En el siguiente turno, cada uno es una herramienta que puede llamar.</td></tr>
<tr><td><b>Un grafo, no una transcripción</b></td><td>Cada turno, llamada al modelo y llamada a función es un nodo que puedes inspeccionar, ramificar y volver a ejecutar. Los subagentes trabajan en su propia rama y solo devuelven el resultado.</td></tr>
<tr><td><b>Decisiones comprobadas</b></td><td>El modelo elige de un menú fijo y el código valida la elección antes de ejecutar nada.</td></tr>
<tr><td><b>Un solo espacio de trabajo</b></td><td>Chats, archivos, terminales y un navegador integrado lado a lado, en la App de escritorio, la interfaz web, una interfaz de terminal o la CLI.</td></tr>
<tr><td><b>Maneja tu ordenador</b></td><td>Controla apps de escritorio, páginas del navegador y VM remotas en segundo plano, sin apoderarse de tu pantalla.</td></tr>
<tr><td><b>Siempre activo</b></td><td>Tareas programadas, objetivos que sobreviven a los reinicios y chat desde Telegram, Discord, Slack y WeChat.</td></tr>
<tr><td><b>Cualquier modelo</b></td><td>Más de 300 proveedores y modelos locales; varias claves por proveedor, con cambio automático al llegar a un límite.</td></tr>
</table>

Todas las funciones, con su diseño: [índice de funciones](https://fzkuji.github.io/OpenProgram/docs/reference/design/README.html).

## Un vistazo rápido

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

Consulta [cómo escribir agentes](https://fzkuji.github.io/OpenProgram/docs/capabilities/agentic-programming/writing-functions/agent.html).

## Novedades

- **2026-11-25 · [v1.5.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.5.0)** — Acceso a este ordenador desde el teléfono o desde cualquier lugar con Tailscale integrado, chats sobre Codex, aplicaciones que se exportan como software propio y una herramienta de capturas.
- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — Varias claves e inicios de sesión por proveedor, con cambio automático al llegar a un límite; bloques de tareas en las respuestas e historial de ejecuciones para las tareas programadas.
- **2026-11-15 · v1.3.0** — Página de proveedores con estado, cuota y gasto; los agentes que trabajan en una conversación, listados en Activity.

<details>
<summary>Anteriores</summary>

- **2026-11-14 · v1.2.0** — Las ramas de una conversación se ejecutan en paralelo, Detener actúa al instante y llega la App para Windows.
- **2026-11-10 · v1.1.0** — Rebobinar desde cualquier mensaje enviado; la App llega a Linux.
- **2026-11-06 · v1.0.0** — Primera versión de la línea 1.x: App de escritorio con vistas divididas, uso del ordenador en segundo plano, reuniones y un planificador siempre activo.
- **2026-08-17 · v0.7.0** — Un navegador integrado junto a los archivos, el chat y la terminal, que los agentes pueden leer y manejar.
- **2026-03-31 · v0.1.0** — Primera versión pública: funciones agénticas, funciones de Python cuyos pasos ejecuta un modelo, registradas en un árbol de contexto.

</details>

## Cita

```bibtex
@inproceedings{qi2026llmascode,
  title     = {LLM-as-Code: Agentic Programming for Agent Harness},
  author    = {Qi, Junjia and Fu, Zichuan and Gao, Jingtong and Zhang, Wenlin and Yan, Hanyu and Wu, Xian and Zhao, Xiangyu},
  booktitle = {KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)},
  year      = {2026},
  url       = {https://arxiv.org/abs/2606.15874},
}
```

## Contribuir

Informes, reproducciones, pruebas, documentación y código: todo es bienvenido: [issues](https://github.com/Fzkuji/OpenProgram/issues) · [pull requests](https://github.com/Fzkuji/OpenProgram/pulls) · [guía de contribución](../.github/CONTRIBUTING.md).

<!-- contributors-avatars -->
<p>
<a href="https://github.com/FuzzyKuji"><img src="https://avatars.githubusercontent.com/u/9404270?v=4&amp;s=48" width="24" height="24" alt="Maintainer" /></a>
<a href="https://github.com/Qi202"><img src="https://github.com/Qi202.png?size=48" width="24" height="24" alt="Qi202" /></a>
<a href="https://github.com/GithungDang"><img src="https://github.com/GithungDang.png?size=48" width="24" height="24" alt="GithungDang" /></a>
<a href="https://github.com/basil-k-aji-dev"><img src="https://github.com/basil-k-aji-dev.png?size=48" width="24" height="24" alt="basil-k-aji-dev" /></a>
<a href="https://github.com/binyangzhu000-sudo"><img src="https://github.com/binyangzhu000-sudo.png?size=48" width="24" height="24" alt="binyangzhu000-sudo" /></a>
</p>
<!-- /contributors-avatars -->

## Licencia

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji.
