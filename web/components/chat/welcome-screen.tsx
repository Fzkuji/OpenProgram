/**
 * WelcomeScreen — empty-chat onboarding panel.
 *
 * Renders the `{LLM}` logo, the "Agentic Programming" title, the help
 * text, and the four function-shortcut buttons. Visible whenever the
 * session store says so; mounted as a portal inside #welcome-mount
 * placeholder that PageShell leaves in the chat area.
 *
 * Example buttons hand off to `clickFnExample` which now opens the
 * React FunctionForm in the Composer.
 */
"use client";

import { cloneElement, isValidElement, useRef } from "react";

import { useSessionStore } from "@/lib/session-store";
import { useWindowGlobals } from "@/components/sidebar/use-window-globals";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  BookTextIcon,
  FrameIcon,
  MonitorIcon,
  SearchIcon,
} from "@/components/animated-icons";

import styles from "./welcome-screen.module.css";

interface Example {
  name: string;
  label: string;
  icon: React.ReactNode;
}

const EXAMPLES: Example[] = [
  { name: "gui_agent", label: "Run gui_agent", icon: <MonitorIcon size={18} /> },
  { name: "research_agent", label: "Run research_agent", icon: <SearchIcon size={18} /> },
  { name: "wiki_agent", label: "Run wiki_agent", icon: <BookTextIcon size={18} /> },
  { name: "extract_pdf_figures", label: "Run extract_pdf_figures", icon: <FrameIcon size={18} /> },
];

export function WelcomeScreen() {
  const visible = useSessionStore((s) => s.welcomeVisible);
  const openFnForm = useSessionStore((s) => s.openFnForm);
  const fnFormFunction = useSessionStore((s) => s.fnFormFunction);
  const fnFormClosing = useSessionStore((s) => s.fnFormClosing);
  const setComposerInput = useSessionStore((s) => s.setComposerInput);
  const focusComposer = useSessionStore((s) => s.focusComposer);
  const { availableFunctions } = useWindowGlobals();
  const { text } = useTranslation();

  if (!visible) return null;

  // Treat a closing form as already not-collapsed so the examples row
  // animates back in WITH the form shrinking, not a beat later when
  // `fnFormFunction` finally clears at the transition end.
  const collapsed = fnFormFunction !== null && !fnFormClosing;

  function pickExample(name: string, ev?: React.MouseEvent<HTMLButtonElement>) {
    // Blur the clicked button BEFORE flipping state — once `collapsed`
    // becomes true the example row gets `aria-hidden=true`, which
    // browsers complain about if the focused element is inside.
    if (ev?.currentTarget) ev.currentTarget.blur();
    else if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
    const fn = availableFunctions.find((f) => f.name === name);
    if (fn) {
      openFnForm(fn);
      return;
    }
    // Functions list hasn't streamed in yet — fall back to filling the
    // composer with the function name so the click still does something.
    // (The legacy `run <name>` text-command path is gone; if the user
    // edits + submits, this just sends as plain chat text now.)
    setComposerInput(`${name} `);
    focusComposer();
  }

  return (
    <div className={styles.welcome}>
      <div className={styles.top}>
        <div className={styles.logo}>
          {"{"}
          <span className={styles.l1}>L</span>
          <span className={styles.l2}>L</span>
          <span className={styles.m}>M</span>
          <span className={styles.caret} />
          {"}"}
        </div>
        <div className={styles.title}>Agentic Programming</div>
        <div className={styles.text}>
          {text(
            "Run agentic functions, create new ones, or ask questions. Type a command or natural language below.",
            "运行 Agentic 函数、创建新函数，或直接提问。可以在下方输入命令或自然语言。",
          )}
        </div>
      </div>
      <div
        className={styles.examples}
        data-collapsed={collapsed ? "true" : "false"}
        aria-hidden={collapsed ? true : undefined}
        inert={collapsed || undefined}
      >
        {EXAMPLES.map((ex) => (
          <ExampleButton key={ex.name} ex={ex} onClick={pickExample} />
        ))}
      </div>
    </div>
  );
}

/* ---- Example button — drives its animated icon from the WHOLE
   button's hover (claude.ai-style) via the icon's ref handle, so the
   card lights up + the glyph animates anywhere you hover the card. --- */

function ExampleButton({
  ex,
  onClick,
}: {
  ex: Example;
  onClick: (name: string, ev?: React.MouseEvent<HTMLButtonElement>) => void;
}) {
  const { text } = useTranslation();
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  const icon = isValidElement(ex.icon)
    ? cloneElement(ex.icon as React.ReactElement, { ref: iconRef } as Record<string, unknown>)
    : ex.icon;
  return (
    <button
      type="button"
      className={styles.example}
      onClick={(e) => onClick(ex.name, e)}
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
    >
      {icon}
      {text(ex.label, `运行 ${ex.name}`)}
    </button>
  );
}
