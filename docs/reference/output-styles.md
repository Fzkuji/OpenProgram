# Output styles

An output style is a block of text appended to the system prompt describing **how** replies should be written. It changes tone, length, and structure; it does not change what the agent can do, which tools it has, or which model it uses.

One style is active at a time. It applies to every model call that goes through the prompt assembler: chat turns, agentic function bodies, and background agents.

## Switching styles

In the TUI:

```
/style              # list every style, marking the active one
/style concise      # switch
/style default      # back to no extra guidance
```

From the shell:

```bash
openprogram config get agent.output_style
openprogram config set agent.output_style concise
```

The Web settings page renders the same setting as a dropdown under the Agent group.

The style is a global preference stored as `agent.output_style` in `~/.openprogram/config.json`, so it persists across sessions and restarts. A change applies to the next turn.

## Built-in styles

| Name | Effect |
|------|------|
| `default` | Appends nothing. Identical to having no output style at all. |
| `concise` | Answers in as few words as the question allows; result first, no preamble or closing summary. |
| `explanatory` | Gives the answer, then the reasoning, the trade-offs weighed, and how a change fits its surroundings. |
| `direct` | States conclusions without hedging, drops caveats that do not change the decision, and corrects wrong premises plainly. |
| `detailed` | Covers edge cases, failure modes, and assumptions; structures longer answers with headings or lists. |

## Custom styles

A custom style is a markdown file whose **filename is the style name** and whose body is the text appended to the prompt. Discovery mirrors how skills are found, in ascending precedence:

1. Built-in, from the table above
2. User, `~/.openprogram/output-styles/<name>.md`
3. Project, `<cwd>/output-styles/<name>.md`

Later sources win on a name collision, so a project file named `concise.md` replaces the built-in of that name, and a user file replaces it everywhere except in a project that overrides it again.

```bash
mkdir -p ~/.openprogram/output-styles
cat > ~/.openprogram/output-styles/lab-notes.md <<'EOF'
## Output style: lab notes

Report work as a lab notebook entry. State what was tried, what was observed,
and what it implies. Record negative results as plainly as positive ones.
EOF

openprogram config set agent.output_style lab-notes
```

YAML frontmatter is stripped if present, so a file can carry a `description:` for its own bookkeeping without that text reaching the model. A file whose body is empty is ignored rather than registered as a blank style.

## Where the text lands

Output styles are a registered context component (`output_style`, layer L0), so the text enters the prompt through the single assembler in `openprogram/context/components.py` rather than being tacked onto a user message.

Within L0 the style sits after the identity and tool-use guidance and **before** the agent's own inline system prompt. An agent's specific instructions therefore come later in the prompt and win where the two conflict.

Because `default` produces an empty string and empty components are dropped, the default configuration assembles a prompt byte-for-byte identical to one built with no output-style component registered.

## Related

- [Configuration](config.md) — the full settings registry and `openprogram config`
- [Context composition](design/context/composition.md) — the layered assembler that owns the prompt
