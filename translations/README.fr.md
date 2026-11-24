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

<p align="center"><b>Un harness d’agents open source où les agents sont des programmes Python qui s’améliorent eux-mêmes.</b></p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/.github/CHANGELOG.md"><img alt="Version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square">
</p>

<p align="center">
  <a href="https://fuzzykuji.github.io/openprogram-site/">Site web</a> · <a href="#install">Installation</a> · <a href="https://fuzzykuji.github.io/openprogram-site/docs/">Documentation</a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

## Actualités

- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — Plusieurs clés et connexions par fournisseur, avec bascule automatique quand une limite est atteinte ; blocs de tâches dans les réponses et historique d’exécution pour les tâches planifiées. À partir de cette version, les versions sont publiées depuis ce dépôt.
- **2026-11-15 · v1.3.0** — Page des fournisseurs avec état, quota et dépenses ; les agents à l’œuvre dans une conversation listés dans Activity ; les fichiers créés par un agent s’ouvrent d’eux-mêmes.
- **2026-11-14 · v1.2.0** — Les branches d’une conversation s’exécutent côte à côte, Arrêter agit immédiatement et l’App Windows arrive.
- **2026-11-10 · v1.1.0** — Retour en arrière depuis n’importe quel message envoyé ; l’App arrive sur Linux.
- **2026-11-06 · v1.0.0** — Première version de la ligne 1.x : App de bureau avec vues partagées, utilisation de l’ordinateur en arrière-plan, réunions et planificateur permanent.
- **2026-08-17 · v0.7.0** — Un navigateur intégré à côté des fichiers, du chat et du terminal, que les agents peuvent lire et utiliser.
- **2026-03-31 · v0.1.0** — Première version publique : les fonctions agentiques, des fonctions Python dont un modèle exécute les étapes, enregistrées dans un arbre de contexte.

<a id="install"></a>

## Installation

```bash
curl -fsSL https://fuzzykuji.github.io/openprogram-site/install | sh
```

Sous Windows (à partir de v1.2.0) : `irm https://fuzzykuji.github.io/openprogram-site/install.ps1 | iex`. L’App de bureau macOS est un DMG disponible sur la [page des versions](https://github.com/Fzkuji/OpenProgram/releases) ; l’App Linux s’y trouve en deb et AppImage, et l’App Windows arrive avec v1.1.1. Si le site web ne répond pas : `curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`. Plus d’options dans le [guide d’installation](https://fuzzykuji.github.io/openprogram-site/docs/install/install.html) (en anglais).

## Démarrage rapide

```bash
openprogram        # assistant de configuration, puis chat dans le terminal
openprogram web    # espace de travail sur http://localhost:18100
```

Ensuite : [premiers pas](../docs/start/GETTING_STARTED.html) · [modèles](../docs/models/README.html) · [toute la documentation](../docs/README.html) (en anglais)

## En un coup d’œil

```python
from openprogram import Agent

class SupportAgent(Agent):
    def triage(self, ticket: str) -> str:
        """Classify the ticket, then draft a reply."""
        kind = self(ticket, choices=["bug", "question"])
        if kind == "bug":
            logs = search_logs(ticket)  # votre propre fonction utilitaire
            return self(f"Reply using:\n{logs}")
        return self("Draft a short reply.")
```

Le modèle décide ; Python garantit que les étapes nécessaires s’exécutent. Voir [écrire des agents](../docs/capabilities/agentic-programming/writing-functions/agent.html) (en anglais).

## Fonctionnalités

Chaque entrée renvoie vers son document de conception (en anglais).

### Framework

| | |
|---|---|
| **[Classes d’agents](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/function/agentic-program.html)** | Un agent est une classe Python ; branches, nouvelles tentatives et vérifications sont du code, `self(...)` est un appel au modèle. |
| **[DAG d’exécution](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/dag/overview.html)** | Chaque tour utilisateur, appel au modèle et appel de fonction est un nœud que vous pouvez inspecter, ramifier et rejouer. |
| **[Contexte](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/context/overview.html)** | Chaque appel lit une sélection de nœuds plutôt qu’un historique qui s’allonge, avec compaction automatique. |
| **[Auto-programmation](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/workflows/workflow.html)** | Les agents écrivent de nouvelles méthodes dans leur propre classe ; elles deviennent des outils au tour suivant. |
| **[Décisions vérifiées](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent/next-step-decision.html)** | Le modèle choisit dans un menu fixe et le code valide le choix avant toute exécution. |
| **[Sous-agents](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/collaboration/agent-collaboration.html)** | Les sous-agents travaillent sur leur propre branche, éventuellement dans un worktree Git, et ne renvoient que leur résultat. |
| **[Événements](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/proactive/event-layer.html)** | Un bus d’événements typé ; les plugins observent les événements ou filtrent les appels d’outils. |
| **[Agents permanents](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/always-on-agents.html)** | Des agents qui tournent en arrière-plan et réagissent aux événements. |
| **[Objectifs et reprise](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/reliability/goal-and-recovery.html)** | Les objectifs de longue durée survivent aux redémarrages et reprennent là où ils s’étaient arrêtés. |
| **[Tolérance aux pannes](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/reliability/llm-fault-tolerance.html)** | Nouvelles tentatives, délais d’expiration et classification des erreurs pour chaque appel au modèle. |

### Espace de travail

| | |
|---|---|
| **[Bureau et interface web](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/README.html)** | L’App sur macOS et Linux, et à partir de v1.2.0 sur Windows ; l’interface web sur chacun d’eux ou sur un serveur. |
| **[Panneaux partagés](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/center-tabs-and-split-layout.html)** | Chats, fichiers, terminaux et pages web côte à côte. |
| **[Navigateur intégré](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/browser/built-in-browser.html)** | Un navigateur que l’agent lit et pilote sous vos yeux. |
| **[Terminal intégré](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/integrated-terminal.html)** | Des terminaux dans l’espace de travail, partagés avec l’agent. |
| **[Computer use](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/computer-use/computer-and-browser-use.html)** | Pilotez des apps de bureau, des pages du navigateur ou une VM distante. |
| **[CLI et TUI](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/cli/README.html)** | Le même agent depuis le terminal. |
| **[Accès à distance](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/remote-web-access.html)** | Accédez à votre propre instance depuis un autre appareil, réservé au propriétaire. |

### Agents et sécurité

| | |
|---|---|
| **[Agents configurables](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent-configuration/agent-configuration-ui.html)** | Enregistrez modèle, instructions, outils et accès à la mémoire comme un Agent réutilisable. |
| **[Mémoire](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/memory/overview.html)** | Une mémoire sourcée, avec recherche et écriture en arrière-plan. |
| **[Permissions et sandbox](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/security/sandbox-architecture.html)** | Approuvez ou refusez les appels d’outils avec une note ; les commandes s’exécutent dans un sandbox. |
| **[Programs](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/programs/programs-architecture.html)** | Outils et applications sous forme de Programs installables. |

### Modèles et intégrations

| | |
|---|---|
| **[Fournisseurs](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/README.html)** | Anthropic, OpenAI, Gemini et d’autres, avec plusieurs comptes par fournisseur. |
| **[Modèles locaux](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/models/local-models.html)** | Serveurs d’inférence locaux, avec authentification facultative. |
| **[MCP](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/mcp-integration.html)** | Utilisez des serveurs MCP ou exposez OpenProgram comme serveur MCP. |
| **[Skills et plugins](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/skills-and-plugins.html)** | Étendez ce que l’agent sait et ce qu’il peut appeler. |
| **[Canaux](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/channels/README.html)** | Parlez à vos agents depuis Telegram, Discord, Slack et WeChat. |
| **[Mises à jour automatiques](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/distribution/automatic-updates.html)** | Des versions signées qui se mettent à jour sur place. |

## Citation

```bibtex
@inproceedings{qi2026llmascode,
  title     = {LLM-as-Code: Agentic Programming for Agent Harness},
  author    = {Qi, Junjia and Fu, Zichuan and Gao, Jingtong and Zhang, Wenlin and Yan, Hanyu and Wu, Xian and Zhao, Xiangyu},
  booktitle = {KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)},
  year      = {2026},
  url       = {https://arxiv.org/abs/2606.15874},
}
```

## Licence

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji. Consultez la licence pour les obligations d’utilisation, de modification, de distribution et de service en réseau.

## Remerciements

Merci à toutes les personnes qui contribuent par du code, des rapports, des reproductions, des tests, de la documentation et des relectures.

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
