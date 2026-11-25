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

<p align="center"><b>Les agents programment tout.</b><br>Des workflows, des applications avec l’IA intégrée, et les agents eux-mêmes. Un harness d’agents open source où les agents sont des programmes Python.</p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/releases/latest"><img alt="Version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Fzkuji/OpenProgram?style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://arxiv.org/abs/2606.15874"><img alt="Paper" src="https://img.shields.io/badge/arXiv-2606.15874-b31b1b?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/"><b>Site web</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/"><b>Documentation</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/install/download.html"><b>Télécharger l’App</b></a> · <a href="https://arxiv.org/abs/2606.15874"><b>Article</b></a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="../docs/images/openprogram-tour-dark.webp">
      <source media="(prefers-color-scheme: light)" srcset="../docs/images/openprogram-tour-light.webp">
      <img src="../docs/images/openprogram-tour-light.webp" alt="Visite d’OpenProgram : une tâche du début à la fin, des agents toujours actifs, tous vos appareils et des agents qui programment tout" width="100%">
    </picture>
  </a>
</p>

<a id="install"></a>

## Installation

**macOS · Linux**

```bash
curl -fsSL https://fzkuji.github.io/OpenProgram/install | sh
```

**Windows (PowerShell)**

```powershell
irm https://fzkuji.github.io/OpenProgram/install.ps1 | iex
```

**App de bureau** — [macOS Apple Silicon](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-arm64.dmg) · [macOS Intel](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-x64.dmg) · [Windows](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-win-x64.exe) · [Linux (deb, AppImage)](https://fzkuji.github.io/OpenProgram/docs/install/download.html). L’App se met à jour d’elle-même.

Puis lancez :

```bash
openprogram        # connecter un modèle, puis discuter dans le terminal
openprogram web    # ouvrir l’espace de travail sur http://localhost:18100
```

Si le site web ne répond pas : `curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`. Plus d’options : [guide d’installation](https://fzkuji.github.io/OpenProgram/docs/install/install.html) · [premiers pas](https://fzkuji.github.io/OpenProgram/docs/start/GETTING_STARTED.html).

## Pourquoi OpenProgram

<table>
<tr><td><b>Les agents sont des programmes Python</b></td><td>Les branches, les nouvelles tentatives et les vérifications sont du code ordinaire ; <code>self(...)</code> est un appel au modèle. Le modèle décide, et Python garantit que les étapes nécessaires s’exécutent.</td></tr>
<tr><td><b>Ils se programment eux-mêmes</b></td><td>Un agent écrit de nouvelles méthodes dans sa propre classe. Au tour suivant, chacune est un outil qu’il peut appeler.</td></tr>
<tr><td><b>Un graphe, pas une transcription</b></td><td>Chaque tour, appel au modèle et appel de fonction est un nœud que vous pouvez inspecter, ramifier et rejouer. Les sous-agents travaillent sur leur propre branche et ne renvoient que le résultat.</td></tr>
<tr><td><b>Des décisions vérifiées</b></td><td>Le modèle choisit dans un menu fixe, et le code valide ce choix avant toute exécution.</td></tr>
<tr><td><b>Un seul espace de travail</b></td><td>Chats, fichiers, terminaux et un navigateur intégré côte à côte, dans l’App de bureau, l’interface web, une interface terminal ou la CLI.</td></tr>
<tr><td><b>Utilise votre ordinateur</b></td><td>Pilote les applications de bureau, les pages du navigateur et les VM distantes en arrière-plan, sans prendre le contrôle de votre écran.</td></tr>
<tr><td><b>Toujours actif</b></td><td>Tâches planifiées, objectifs qui survivent aux redémarrages, et chat depuis Telegram, Discord, Slack et WeChat.</td></tr>
<tr><td><b>N’importe quel modèle</b></td><td>Plus de 300 fournisseurs et des modèles locaux ; plusieurs clés par fournisseur, avec bascule automatique quand une limite est atteinte.</td></tr>
</table>

Toutes les fonctionnalités, avec leur conception : [index des fonctionnalités](https://fzkuji.github.io/OpenProgram/docs/reference/design/README.html).

## En un coup d’œil

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

Voir [écrire des agents](https://fzkuji.github.io/OpenProgram/docs/capabilities/agentic-programming/writing-functions/agent.html).

## Actualités

- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — Plusieurs clés et connexions par fournisseur, avec bascule automatique quand une limite est atteinte ; blocs de tâches dans les réponses et historique d’exécution pour les tâches planifiées.
- **2026-11-15 · v1.3.0** — Page des fournisseurs avec état, quota et dépenses ; les agents à l’œuvre dans une conversation listés dans Activity.
- **2026-11-14 · v1.2.0** — Les branches d’une conversation s’exécutent côte à côte, Arrêter agit immédiatement et l’App Windows arrive.

<details>
<summary>Plus anciennes</summary>

- **2026-11-10 · v1.1.0** — Retour en arrière depuis n’importe quel message envoyé ; l’App arrive sur Linux.
- **2026-11-06 · v1.0.0** — Première version de la ligne 1.x : App de bureau avec vues partagées, utilisation de l’ordinateur en arrière-plan, réunions et planificateur permanent.
- **2026-08-17 · v0.7.0** — Un navigateur intégré à côté des fichiers, du chat et du terminal, que les agents peuvent lire et utiliser.
- **2026-03-31 · v0.1.0** — Première version publique : les fonctions agentiques, des fonctions Python dont un modèle exécute les étapes, enregistrées dans un arbre de contexte.

</details>

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

## Contribuer

Rapports, reproductions, tests, documentation et code sont tous les bienvenus : [issues](https://github.com/Fzkuji/OpenProgram/issues) · [pull requests](https://github.com/Fzkuji/OpenProgram/pulls) · [guide de contribution](../.github/CONTRIBUTING.md).

<!-- contributors-avatars -->
<p>
<a href="https://github.com/FuzzyKuji"><img src="https://avatars.githubusercontent.com/u/9404270?v=4&amp;s=48" width="24" height="24" alt="Maintainer" /></a>
<a href="https://github.com/Qi202"><img src="https://github.com/Qi202.png?size=48" width="24" height="24" alt="Qi202" /></a>
<a href="https://github.com/GithungDang"><img src="https://github.com/GithungDang.png?size=48" width="24" height="24" alt="GithungDang" /></a>
<a href="https://github.com/basil-k-aji-dev"><img src="https://github.com/basil-k-aji-dev.png?size=48" width="24" height="24" alt="basil-k-aji-dev" /></a>
<a href="https://github.com/binyangzhu000-sudo"><img src="https://github.com/binyangzhu000-sudo.png?size=48" width="24" height="24" alt="binyangzhu000-sudo" /></a>
</p>
<!-- /contributors-avatars -->

## Licence

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji.
