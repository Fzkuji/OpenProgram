# Framework Adoption Homepage Design

Status: superseded for distribution details by `docs/reference/design/distribution/installation-packaging.html`. The current product ships GUI, Research, and Wiki in every supported release.

## Goal

Present OpenProgram first as an installable, general-purpose agent framework. The homepage should help visitors understand what they can use immediately, install the framework, select a ready-made harness, or build their own agent. Agentic Programming and the related papers remain supporting technical context rather than the primary message.

## Audience

- Developers looking for a general-purpose Python agent framework.
- Users who want to install a ready-made GUI, research, or wiki agent.
- Framework developers who need model providers, tools, skills, memory, multi-agent execution, and inspectable interfaces.

## Information Architecture

1. Product-first hero.
2. Project facts.
3. Ready-made harnesses and a build-your-own path.
4. Quick Start with the supported installer and `openprogram` command.
5. Included framework capabilities.
6. Real interface screenshots.
7. Technical mechanisms: DAG Context, Agentic Workflow, and Event Infrastructure.
8. Related papers.
9. Final installation call to action.

## Hero

- Eyebrow: `Open-source agent framework`.
- Heading: `Build agents that run in Python.`
- Copy: state that OpenProgram includes the runtime, Web UI, terminal UI, model connections, tools, skills, memory, and multi-agent collaboration. State that users can install a ready-made harness or build their own agent.
- Primary action: `Install OpenProgram`, linking to Quick Start.
- Secondary action: `Explore documentation`.
- Visual: use a real OpenProgram Web UI screenshot. Move the code sample to the build-your-own card.
- Facts: Python 3.11+, supported macOS/Linux release hosts, remote browser access for Windows/mobile clients, Web/TUI/CLI, and AGPL-3.0. The current distribution matrix is authoritative in `docs/reference/design/distribution/installation-packaging.html`.

## Use or Build

The section heading is `Use an agent or build your own.` It contains four paths:

- GUI Agent: operate desktop applications and GUI environments.
- Research Agent: support literature research, experiments, and paper work.
- Wiki Agent: organize source material into a maintainable knowledge base.
- Build your own: show a compact `@agentic_function` example and link to the authoring documentation.

Each ready-made harness links to its existing repository or usage guide. State that GUI Agent, Research Agent, and Wiki Agent ship inside every supported release; third-party harnesses are additional extensions.

## Quick Start

Place Quick Start before the detailed capability explanation. Show the supported macOS/Linux installer followed by `openprogram`. Direct Windows users to remote browser access on a supported host and link advanced installation options to the installation guide. Keep copy controls and their fallback behavior.

## Included Capabilities

Describe capabilities using recognizable product functions rather than research terminology:

- Model providers.
- Tools and MCP.
- Skills.
- Memory.
- Multi-agent collaboration.
- Sessions and branches.
- Web UI, TUI, and CLI.
- Channel integrations.

Only advertise capabilities confirmed by the repository documentation. Do not add usage counts, customer claims, benchmark numbers, or unsupported compatibility claims.

## Technical Context and Papers

Keep DAG Context, Agentic Workflow, and Event Infrastructure after the product and interface sections. Keep the related-papers section near the end with only these titles and arXiv links:

- `LLM-as-Code: Agentic Programming for Agent Harness` — arXiv:2606.15874.
- `GUI-Lens: Coarse-to-Fine Cropping for GUI Grounding with General-Purpose VLMs` — arXiv:2608.03270.

Do not mention the KDD workshop on the homepage.

## Visual Direction

Retain the existing near-black background, teal and violet accents, Inter body type, monospace utility labels, restrained borders, and real product imagery. The identifying visual element is the direct transition from ready-made harness cards to the same OpenProgram runtime and build-your-own path. Do not introduce decorative animation or new external assets.

## Responsive and Accessibility Requirements

- Preserve semantic navigation, headings, sections, alternative text, keyboard focus, and reduced-motion behavior.
- Keep all content visible when JavaScript is disabled.
- Stack harness and capability cards on narrow screens without horizontal overflow.
- Preserve functional copy buttons with Clipboard API fallback.

## Verification

- Extend `scripts.docs_site.check_landing` to assert the new product-first copy, harness links, Quick Start order, capability section, both arXiv links, and absence of workshop copy.
- Run the landing-page check, documentation build, link check, and `git diff --check`.
- Inspect desktop and 390px mobile screenshots.
- Publish through the existing `Publish openprogram.io` workflow and verify the live HTML after GitHub Pages finishes building.
