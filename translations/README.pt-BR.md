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

<p align="center"><b>Agentes programam qualquer coisa.</b><br>Fluxos de trabalho, apps com IA integrada e os próprios agentes. Um harness de agentes de código aberto em que agentes são programas Python.</p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/releases/latest"><img alt="Versão" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Fzkuji/OpenProgram?style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://arxiv.org/abs/2606.15874"><img alt="Paper" src="https://img.shields.io/badge/arXiv-2606.15874-b31b1b?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/"><b>Site</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/"><b>Documentação</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/install/download.html"><b>Baixar o App</b></a> · <a href="https://arxiv.org/abs/2606.15874"><b>Artigo</b></a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="../docs/images/openprogram-tour-dark.webp">
      <source media="(prefers-color-scheme: light)" srcset="../docs/images/openprogram-tour-light.webp">
      <img src="../docs/images/openprogram-tour-light.webp" alt="Tour pelo OpenProgram: uma tarefa do início ao fim, agentes sempre ativos, todos os dispositivos e agentes que programam qualquer coisa" width="100%">
    </picture>
  </a>
</p>

<a id="install"></a>

## Instalação

**macOS · Linux**

```bash
curl -fsSL https://fzkuji.github.io/OpenProgram/install | sh
```

**Windows (PowerShell)**

```powershell
irm https://fzkuji.github.io/OpenProgram/install.ps1 | iex
```

**App de desktop** — [macOS Apple Silicon](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-arm64.dmg) · [macOS Intel](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-x64.dmg) · [Windows](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-win-x64.exe) · [Linux (deb, AppImage)](https://fzkuji.github.io/OpenProgram/docs/install/download.html). O App se atualiza sozinho.

Depois rode:

```bash
openprogram        # conecte um modelo e depois converse no terminal
openprogram web    # abre o espaço de trabalho em http://localhost:18100
```

Se o site não responder: `curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`. Mais opções: [guia de instalação](https://fzkuji.github.io/OpenProgram/docs/install/install.html) · [primeiros passos](https://fzkuji.github.io/OpenProgram/docs/start/GETTING_STARTED.html).

## Por que OpenProgram

<table>
<tr><td><b>Agentes são programas Python</b></td><td>Ramificações, novas tentativas e verificações são código comum; <code>self(...)</code> é uma chamada ao modelo. O modelo decide, e o Python garante que as etapas necessárias rodem.</td></tr>
<tr><td><b>Eles se programam</b></td><td>Um agente escreve novos métodos na própria classe. No turno seguinte, cada um é uma ferramenta que ele pode chamar.</td></tr>
<tr><td><b>Um grafo, não uma transcrição</b></td><td>Cada turno, chamada ao modelo e chamada de função é um nó que você pode inspecionar, ramificar e reproduzir. Subagentes trabalham no próprio ramo e devolvem só o resultado.</td></tr>
<tr><td><b>Decisões verificadas</b></td><td>O modelo escolhe em um menu fixo e o código valida a escolha antes de qualquer execução.</td></tr>
<tr><td><b>Um só espaço de trabalho</b></td><td>Chats, arquivos, terminais e um navegador embutido lado a lado, no App de desktop, na interface web, numa interface de terminal ou na CLI.</td></tr>
<tr><td><b>Opera o seu computador</b></td><td>Controla apps de desktop, páginas do navegador e VMs remotas em segundo plano, sem tomar a sua tela.</td></tr>
<tr><td><b>Sempre ativo</b></td><td>Tarefas agendadas, objetivos que sobrevivem a reinícios e chat pelo Telegram, Discord, Slack e WeChat.</td></tr>
<tr><td><b>Qualquer modelo</b></td><td>Mais de 300 provedores e modelos locais; várias chaves por provedor, com troca automática ao atingir um limite.</td></tr>
</table>

Todos os recursos, com o seu design: [índice de recursos](https://fzkuji.github.io/OpenProgram/docs/reference/design/README.html).

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

Veja [como escrever agentes](https://fzkuji.github.io/OpenProgram/docs/capabilities/agentic-programming/writing-functions/agent.html).

## Novidades

- **2026-11-25 · [v1.5.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.5.0)** — Acesse este computador pelo celular ou de qualquer lugar com o Tailscale integrado, conversas no Codex, aplicativos exportáveis como software próprio e uma ferramenta de captura de tela.
- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — Várias chaves e logins por provedor, com troca automática ao atingir um limite; blocos de tarefas nas respostas e histórico de execuções para tarefas agendadas.
- **2026-11-15 · v1.3.0** — Página de provedores com estado, cota e gasto; os agentes que trabalham em uma conversa listados em Activity.

<details>
<summary>Anteriores</summary>

- **2026-11-14 · v1.2.0** — Ramos de uma conversa rodam lado a lado, Parar age na hora e chega o App para Windows.
- **2026-11-10 · v1.1.0** — Voltar a partir de qualquer mensagem enviada; o App chega ao Linux.
- **2026-11-06 · v1.0.0** — Primeira versão da linha 1.x: App de desktop com visões divididas, uso do computador em segundo plano, reuniões e um agendador sempre ativo.
- **2026-08-17 · v0.7.0** — Um navegador integrado ao lado dos arquivos, do chat e do terminal, que os agentes podem ler e operar.
- **2026-03-31 · v0.1.0** — Primeira versão pública: funções agênticas, funções Python cujos passos um modelo executa, registradas em uma árvore de contexto.

</details>

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

## Como contribuir

Relatos, reproduções, testes, documentação e código são bem-vindos: [issues](https://github.com/Fzkuji/OpenProgram/issues) · [pull requests](https://github.com/Fzkuji/OpenProgram/pulls) · [guia de contribuição](../.github/CONTRIBUTING.md).

<!-- contributors-avatars -->
<p>
<a href="https://github.com/FuzzyKuji"><img src="https://avatars.githubusercontent.com/u/9404270?v=4&amp;s=48" width="24" height="24" alt="Maintainer" /></a>
<a href="https://github.com/Qi202"><img src="https://github.com/Qi202.png?size=48" width="24" height="24" alt="Qi202" /></a>
<a href="https://github.com/GithungDang"><img src="https://github.com/GithungDang.png?size=48" width="24" height="24" alt="GithungDang" /></a>
<a href="https://github.com/basil-k-aji-dev"><img src="https://github.com/basil-k-aji-dev.png?size=48" width="24" height="24" alt="basil-k-aji-dev" /></a>
<a href="https://github.com/binyangzhu000-sudo"><img src="https://github.com/binyangzhu000-sudo.png?size=48" width="24" height="24" alt="binyangzhu000-sudo" /></a>
</p>
<!-- /contributors-avatars -->

## Licença

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji.
