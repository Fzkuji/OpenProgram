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

<p align="center"><b>Um harness de agentes de código aberto em que agentes são programas Python que melhoram a si mesmos.</b></p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/.github/CHANGELOG.md"><img alt="Versão" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square">
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/">Site</a> · <a href="#install">Instalação</a> · <a href="https://fzkuji.github.io/OpenProgram/docs/">Documentação</a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

## Novidades

- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — Várias chaves e logins por provedor, com troca automática ao atingir um limite; blocos de tarefas nas respostas e histórico de execuções para tarefas agendadas. A partir desta versão, as versões são publicadas neste repositório.
- **2026-11-15 · v1.3.0** — Página de provedores com estado, cota e gasto; os agentes que trabalham em uma conversa listados em Activity; os arquivos que um agente cria abrem sozinhos.
- **2026-11-14 · v1.2.0** — Ramos de uma conversa rodam lado a lado, Parar age na hora e chega o App para Windows.
- **2026-11-10 · v1.1.0** — Voltar a partir de qualquer mensagem enviada; o App chega ao Linux.
- **2026-11-06 · v1.0.0** — Primeira versão da linha 1.x: App de desktop com visões divididas, uso do computador em segundo plano, reuniões e um agendador sempre ativo.
- **2026-08-17 · v0.7.0** — Um navegador integrado ao lado dos arquivos, do chat e do terminal, que os agentes podem ler e operar.
- **2026-03-31 · v0.1.0** — Primeira versão pública: funções agênticas, funções Python cujos passos um modelo executa, registradas em uma árvore de contexto.

<a id="install"></a>

## Instalação

```bash
curl -fsSL https://fzkuji.github.io/OpenProgram/install | sh
```

No Windows (a partir da v1.2.0): `irm https://fzkuji.github.io/OpenProgram/install.ps1 | iex`. O App de desktop para macOS é um DMG na [página de versões](https://github.com/Fzkuji/OpenProgram/releases); o App para Linux está lá como deb e AppImage, e o App para Windows chega com a v1.1.1. Se o site não responder: `curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`. Mais opções no [guia de instalação](https://fzkuji.github.io/OpenProgram/docs/install/install.html) (em inglês).

## Início rápido

```bash
openprogram        # assistente de configuração e depois chat no terminal
openprogram web    # espaço de trabalho em http://localhost:18100
```

A seguir: [primeiros passos](../docs/start/GETTING_STARTED.html) · [modelos](../docs/models/README.html) · [toda a documentação](../docs/README.html) (em inglês)

## Uma olhada rápida

```python
from openprogram import Agent

class SupportAgent(Agent):
    def triage(self, ticket: str) -> str:
        """Classify the ticket, then draft a reply."""
        kind = self(ticket, choices=["bug", "question"])
        if kind == "bug":
            logs = search_logs(ticket)  # sua própria função auxiliar
            return self(f"Reply using:\n{logs}")
        return self("Draft a short reply.")
```

O modelo decide; o Python garante que as etapas necessárias rodem. Veja [como escrever agentes](../docs/capabilities/agentic-programming/writing-functions/agent.html) (em inglês).

## Recursos

Cada item leva ao seu documento de design (em inglês).

### Framework

| | |
|---|---|
| **[Classes de agentes](https://fzkuji.github.io/OpenProgram/docs/reference/design/function/agentic-program.html)** | Um agente é uma classe Python; ramificações, novas tentativas e verificações são código, `self(...)` é uma chamada ao modelo. |
| **[DAG de execução](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/dag/overview.html)** | Cada turno do usuário, chamada ao modelo e chamada de função é um nó que você pode inspecionar, ramificar e reproduzir. |
| **[Contexto](https://fzkuji.github.io/OpenProgram/docs/reference/design/context/overview.html)** | Cada chamada lê uma seleção de nós em vez de um histórico que só cresce, com compactação automática. |
| **[Autoprogramação](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/workflows/workflow.html)** | Os agentes escrevem novos métodos na própria classe; eles viram ferramentas no turno seguinte. |
| **[Decisões verificadas](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/agent/next-step-decision.html)** | O modelo escolhe em um menu fixo e o código valida a escolha antes de qualquer execução. |
| **[Subagentes](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/collaboration/agent-collaboration.html)** | Subagentes rodam no próprio ramo, opcionalmente em um worktree do Git, e devolvem só o resultado. |
| **[Eventos](https://fzkuji.github.io/OpenProgram/docs/reference/design/proactive/event-layer.html)** | Um barramento de eventos tipado; plugins observam eventos ou bloqueiam chamadas de ferramentas. |
| **[Agentes sempre ativos](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/always-on-agents.html)** | Agentes que continuam rodando em segundo plano e reagem a eventos. |
| **[Objetivos e recuperação](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/reliability/goal-and-recovery.html)** | Objetivos de longa duração sobrevivem a reinícios e continuam de onde pararam. |
| **[Tolerância a falhas](https://fzkuji.github.io/OpenProgram/docs/reference/design/providers/reliability/llm-fault-tolerance.html)** | Novas tentativas, tempos limite e classificação de erros em toda chamada ao modelo. |

### Espaço de trabalho

| | |
|---|---|
| **[Desktop e interface web](https://fzkuji.github.io/OpenProgram/docs/reference/design/ui/README.html)** | O App no macOS e no Linux e, a partir da v1.2.0, também no Windows; a interface web em qualquer um deles ou em um servidor. |
| **[Painéis divididos](https://fzkuji.github.io/OpenProgram/docs/reference/design/ui/workspace/center-tabs-and-split-layout.html)** | Chats, arquivos, terminais e páginas web lado a lado. |
| **[Navegador embutido](https://fzkuji.github.io/OpenProgram/docs/reference/design/ui/browser/built-in-browser.html)** | Um navegador que o agente lê e controla enquanto você acompanha. |
| **[Terminal integrado](https://fzkuji.github.io/OpenProgram/docs/reference/design/ui/workspace/integrated-terminal.html)** | Terminais no espaço de trabalho, compartilhados com o agente. |
| **[Computer use](https://fzkuji.github.io/OpenProgram/docs/reference/design/integrations/computer-use/computer-and-browser-use.html)** | Controle apps de desktop, páginas do navegador ou uma VM remota. |
| **[CLI e TUI](https://fzkuji.github.io/OpenProgram/docs/reference/design/cli/README.html)** | O mesmo agente pelo terminal. |
| **[Acesso remoto](https://fzkuji.github.io/OpenProgram/docs/reference/design/ui/remote-web-access.html)** | Acesse sua própria instância de outro dispositivo, só você. |

### Agentes e segurança

| | |
|---|---|
| **[Agentes configuráveis](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/agent-configuration/agent-configuration-ui.html)** | Salve modelo, instruções, ferramentas e acesso à memória como um Agente reutilizável. |
| **[Memória](https://fzkuji.github.io/OpenProgram/docs/reference/design/memory/overview.html)** | Memória com fontes, busca e escrita em segundo plano. |
| **[Permissões e sandbox](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/security/sandbox-architecture.html)** | Aprove ou negue chamadas de ferramentas com uma nota; os comandos rodam em um sandbox. |
| **[Programs](https://fzkuji.github.io/OpenProgram/docs/reference/design/runtime/programs/programs-architecture.html)** | Ferramentas e aplicativos como Programs instaláveis. |

### Modelos e integrações

| | |
|---|---|
| **[Provedores](https://fzkuji.github.io/OpenProgram/docs/reference/design/providers/README.html)** | Anthropic, OpenAI, Gemini e outros, com várias contas por provedor. |
| **[Modelos locais](https://fzkuji.github.io/OpenProgram/docs/reference/design/providers/models/local-models.html)** | Servidores de inferência locais, com autenticação opcional. |
| **[MCP](https://fzkuji.github.io/OpenProgram/docs/reference/design/integrations/extensions/mcp-integration.html)** | Use servidores MCP ou exponha o OpenProgram como um. |
| **[Skills e plugins](https://fzkuji.github.io/OpenProgram/docs/reference/design/integrations/extensions/skills-and-plugins.html)** | Amplie o que o agente sabe e o que ele pode chamar. |
| **[Canais](https://fzkuji.github.io/OpenProgram/docs/reference/design/channels/README.html)** | Converse com seus agentes pelo Telegram, Discord, Slack e WeChat. |
| **[Atualizações automáticas](https://fzkuji.github.io/OpenProgram/docs/reference/design/distribution/automatic-updates.html)** | Versões assinadas que se atualizam no lugar. |

## Citação

```bibtex
@inproceedings{qi2026llmascode,
  title     = {LLM-as-Code: Agentic Programming for Agent Harness},
  author    = {Qi, Junjia and Fu, Zichuan and Gao, Jingtong and Zhang, Wenlin and Yan, Hanyu and Wu, Xian and Zhao, Xiangyu},
  booktitle = {KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)},
  year      = {2026},
  url       = {https://arxiv.org/abs/2606.15874},
}
```

## Licença

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji. Consulte a licença para as obrigações de uso, modificação, distribuição e serviço em rede.

## Agradecimentos

Obrigado a quem contribui com código, relatos, reproduções, testes, documentação e revisões.

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
