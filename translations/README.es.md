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

<p align="center"><b>Un harness de agentes de código abierto donde los agentes son programas Python que se mejoran a sí mismos.</b></p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/.github/CHANGELOG.md"><img alt="Versión" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square">
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/">Sitio web</a> · <a href="#install">Instalación</a> · <a href="https://fzkuji.github.io/OpenProgram/docs/">Documentación</a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

## Novedades

- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — Varias claves e inicios de sesión por proveedor, con cambio automático al llegar a un límite; bloques de tareas en las respuestas e historial de ejecuciones para las tareas programadas. Desde esta versión, las versiones se publican en este repositorio.
- **2026-11-15 · v1.3.0** — Página de proveedores con estado, cuota y gasto; los agentes que trabajan en una conversación, listados en Activity; los archivos que crea un agente se abren solos.
- **2026-11-14 · v1.2.0** — Las ramas de una conversación se ejecutan en paralelo, Detener actúa al instante y llega la App para Windows.
- **2026-11-10 · v1.1.0** — Rebobinar desde cualquier mensaje enviado; la App llega a Linux.
- **2026-11-06 · v1.0.0** — Primera versión de la línea 1.x: App de escritorio con vistas divididas, uso del ordenador en segundo plano, reuniones y un planificador siempre activo.
- **2026-08-17 · v0.7.0** — Un navegador integrado junto a los archivos, el chat y la terminal, que los agentes pueden leer y manejar.
- **2026-03-31 · v0.1.0** — Primera versión pública: funciones agénticas, funciones de Python cuyos pasos ejecuta un modelo, registradas en un árbol de contexto.

<a id="install"></a>

## Instalación

```bash
curl -fsSL https://fzkuji.github.io/OpenProgram/install | sh
```

En Windows (desde v1.2.0): `irm https://fzkuji.github.io/OpenProgram/install.ps1 | iex`. La App de escritorio para macOS es un DMG en la [página de versiones](https://github.com/Fzkuji/OpenProgram/releases); la App para Linux está allí como deb y AppImage, y la App para Windows llega con v1.1.1. Si el sitio web no responde: `curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`. Más opciones en la [guía de instalación](https://fzkuji.github.io/OpenProgram/docs/install/install.html) (en inglés).

## Inicio rápido

```bash
openprogram        # asistente de configuración y luego chat en la terminal
openprogram web    # espacio de trabajo en http://localhost:18100
```

Siguiente: [primeros pasos](../docs/start/GETTING_STARTED.html) · [modelos](../docs/models/README.html) · [toda la documentación](../docs/README.html) (en inglés)

## Un vistazo rápido

```python
from openprogram import Agent

class SupportAgent(Agent):
    def triage(self, ticket: str) -> str:
        """Classify the ticket, then draft a reply."""
        kind = self(ticket, choices=["bug", "question"])
        if kind == "bug":
            logs = search_logs(ticket)  # tu propia función auxiliar
            return self(f"Reply using:\n{logs}")
        return self("Draft a short reply.")
```

El modelo decide; Python garantiza que se ejecuten los pasos necesarios. Consulta [cómo escribir agentes](../docs/capabilities/agentic-programming/writing-functions/agent.html) (en inglés).

## Funciones

Cada entrada enlaza con su documento de diseño (en inglés).

### Framework

| | |
|---|---|
| **[Clases de agentes](https://fzkuji.github.io/OpenProgram/docs/reference/design/function/agentic-program.html)** | Un agente es una clase de Python; ramas, reintentos y comprobaciones son código, `self(...)` es una llamada al modelo. |
| **[DAG de ejecución](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/dag/overview.html)** | Cada turno del usuario, llamada al modelo y llamada a función es un nodo que puedes inspeccionar, ramificar y repetir. |
| **[Contexto](https://fzkuji.github.io/OpenProgram/docs/reference/design/context/overview.html)** | Cada llamada lee una selección de nodos en lugar de un historial que no para de crecer, con compactación automática. |
| **[Autoprogramación](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/workflows/workflow.html)** | Los agentes escriben métodos nuevos en su propia clase; se convierten en herramientas en el siguiente turno. |
| **[Decisiones verificadas](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/agent/next-step-decision.html)** | El modelo elige de un menú fijo y el código valida la elección antes de ejecutar nada. |
| **[Subagentes](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/collaboration/agent-collaboration.html)** | Los subagentes trabajan en su propia rama, opcionalmente en un worktree de Git, y solo devuelven su resultado. |
| **[Eventos](https://fzkuji.github.io/OpenProgram/docs/reference/design/proactive/event-layer.html)** | Un bus de eventos tipado; los plugins observan eventos o bloquean llamadas a herramientas. |
| **[Agentes siempre activos](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/always-on-agents.html)** | Agentes que siguen ejecutándose en segundo plano y reaccionan a eventos. |
| **[Objetivos y recuperación](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/reliability/goal-and-recovery.html)** | Los objetivos de larga duración sobreviven a reinicios y continúan donde se quedaron. |
| **[Tolerancia a fallos](https://fzkuji.github.io/OpenProgram/docs/reference/design/providers/reliability/llm-fault-tolerance.html)** | Reintentos, tiempos de espera y clasificación de errores en cada llamada al modelo. |

### Espacio de trabajo

| | |
|---|---|
| **[Escritorio e interfaz web](https://fzkuji.github.io/OpenProgram/docs/reference/design/ui/README.html)** | La App en macOS y Linux, y desde v1.2.0 también en Windows; la interfaz web en cualquiera de ellos o en un servidor. |
| **[Paneles divididos](https://fzkuji.github.io/OpenProgram/docs/reference/design/ui/workspace/center-tabs-and-split-layout.html)** | Chats, archivos, terminales y páginas web uno al lado del otro. |
| **[Navegador integrado](https://fzkuji.github.io/OpenProgram/docs/reference/design/ui/browser/built-in-browser.html)** | Un navegador que el agente lee y maneja mientras tú miras. |
| **[Terminal integrada](https://fzkuji.github.io/OpenProgram/docs/reference/design/ui/workspace/integrated-terminal.html)** | Terminales en el espacio de trabajo, compartidas con el agente. |
| **[Computer use](https://fzkuji.github.io/OpenProgram/docs/reference/design/integrations/computer-use/computer-and-browser-use.html)** | Maneja apps de escritorio, páginas del navegador o una VM remota. |
| **[CLI y TUI](https://fzkuji.github.io/OpenProgram/docs/reference/design/cli/README.html)** | El mismo agente desde la terminal. |
| **[Acceso remoto](https://fzkuji.github.io/OpenProgram/docs/reference/design/ui/remote-web-access.html)** | Accede a tu propia instancia desde otro dispositivo, solo tú. |

### Agentes y seguridad

| | |
|---|---|
| **[Agentes configurables](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/agent-configuration/agent-configuration-ui.html)** | Guarda modelo, instrucciones, herramientas y acceso a memoria como un Agente reutilizable. |
| **[Memoria](https://fzkuji.github.io/OpenProgram/docs/reference/design/memory/overview.html)** | Memoria con fuentes, búsqueda y escritura en segundo plano. |
| **[Permisos y sandbox](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/security/sandbox-architecture.html)** | Aprueba o rechaza llamadas a herramientas con una nota; los comandos se ejecutan en un sandbox. |
| **[Programs](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/programs/programs-architecture.html)** | Herramientas y aplicaciones como Programs instalables. |

### Modelos e integraciones

| | |
|---|---|
| **[Proveedores](https://fzkuji.github.io/OpenProgram/docs/reference/design/providers/README.html)** | Anthropic, OpenAI, Gemini y más, con varias cuentas por proveedor. |
| **[Modelos locales](https://fzkuji.github.io/OpenProgram/docs/reference/design/providers/models/local-models.html)** | Servidores de inferencia locales, con autenticación opcional. |
| **[MCP](https://fzkuji.github.io/OpenProgram/docs/reference/design/integrations/extensions/mcp-integration.html)** | Usa servidores MCP o expone OpenProgram como uno. |
| **[Skills y plugins](https://fzkuji.github.io/OpenProgram/docs/reference/design/integrations/extensions/skills-and-plugins.html)** | Amplía lo que el agente sabe y lo que puede llamar. |
| **[Canales](https://fzkuji.github.io/OpenProgram/docs/reference/design/channels/README.html)** | Habla con tus agentes desde Telegram, Discord, Slack y WeChat. |
| **[Actualizaciones automáticas](https://fzkuji.github.io/OpenProgram/docs/reference/design/distribution/automatic-updates.html)** | Versiones firmadas que se actualizan en su sitio. |

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

## Licencia

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji. Consulta la licencia para las obligaciones de uso, modificación, distribución y servicio en red.

## Agradecimientos

Gracias a quienes contribuyen con código, informes, reproducciones, pruebas, documentación y revisiones.

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
