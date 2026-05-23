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

import { useSessionStore } from "@/lib/session-store";
import { useWindowGlobals } from "@/components/sidebar/use-window-globals";

import styles from "./welcome-screen.module.css";

interface Example {
  name: string;
  label: string;
  icon: React.ReactNode;
}

const EXAMPLES: Example[] = [
  {
    name: "gui_agent",
    label: "Run gui_agent",
    icon: <MonitorIcon />,
  },
  {
    name: "research_agent",
    label: "Run research_agent",
    icon: <SearchIcon />,
  },
  {
    name: "wiki_agent",
    label: "Run wiki_agent",
    icon: <BookIcon />,
  },
  {
    name: "extract_pdf_figures",
    label: "Run extract_pdf_figures",
    icon: <ImageIcon />,
  },
];

export function WelcomeScreen() {
  const visible = useSessionStore((s) => s.welcomeVisible);
  const openFnForm = useSessionStore((s) => s.openFnForm);
  const fnFormFunction = useSessionStore((s) => s.fnFormFunction);
  const fnFormClosing = useSessionStore((s) => s.fnFormClosing);
  const setComposerInput = useSessionStore((s) => s.setComposerInput);
  const focusComposer = useSessionStore((s) => s.focusComposer);
  const { availableFunctions } = useWindowGlobals();

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
    // composer with "run <name> " so the click still does something.
    setComposerInput(`run ${name} `);
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
          Run agentic functions, create new ones, or ask questions. Type a
          command or natural language below.
        </div>
      </div>
      <div
        className={styles.examples}
        data-collapsed={collapsed ? "true" : "false"}
        aria-hidden={collapsed ? true : undefined}
        inert={collapsed || undefined}
      >
        {EXAMPLES.map((ex) => (
          <button
            key={ex.name}
            type="button"
            className={styles.example}
            onClick={(e) => pickExample(ex.name, e)}
          >
            {ex.icon}
            {ex.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ---- Icons (Phosphor-style, currentColor) -------------------------- */

function MonitorIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="18"
      height="18"
      fill="currentColor"
      viewBox="0 0 256 256"
    >
      <path d="M208,40H48A24,24,0,0,0,24,64V176a24,24,0,0,0,24,24h72v16H88a8,8,0,0,0,0,16h80a8,8,0,0,0,0-16H136V200h72a24,24,0,0,0,24-24V64A24,24,0,0,0,208,40Zm8,136a8,8,0,0,1-8,8H48a8,8,0,0,1-8-8V64a8,8,0,0,1,8-8H208a8,8,0,0,1,8,8Z" />
    </svg>
  );
}

function BookIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="18"
      height="18"
      fill="currentColor"
      viewBox="0 0 256 256"
    >
      <path d="M231.65,194.55,198.46,36.75a16,16,0,0,0-19-12.39L132.65,34.42a16.08,16.08,0,0,0-12.3,19l33.19,157.8A16,16,0,0,0,169.16,224a16.25,16.25,0,0,0,3.38-.36l46.81-10.06A16.09,16.09,0,0,0,231.65,194.55ZM136,69.28a8,8,0,0,1,9.34-6.37l46.81-10.06a8,8,0,0,1,3.32,15.66L148.7,78.57a8,8,0,0,1-9.34-6.37A7.72,7.72,0,0,1,136,69.28Zm-4.19,19.84a8,8,0,0,1,9.34-6.38l46.81-10.06a8,8,0,0,1,3.32,15.67l-46.81,10.06A8,8,0,0,1,135.16,92,7.72,7.72,0,0,1,131.84,89.12ZM216,208H40a16,16,0,0,1-16-16V64A16,16,0,0,1,40,48H96a8,8,0,0,1,0,16H40V192H216a8,8,0,0,1,0,16Z" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="18"
      height="18"
      fill="currentColor"
      viewBox="0 0 256 256"
    >
      <path d="M229.66,218.34l-50.07-50.06a88.11,88.11,0,1,0-11.31,11.31l50.06,50.07a8,8,0,0,0,11.32-11.32ZM40,112a72,72,0,1,1,72,72A72.08,72.08,0,0,1,40,112Z" />
    </svg>
  );
}

function ImageIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="18"
      height="18"
      fill="currentColor"
      viewBox="0 0 256 256"
    >
      <path d="M216,40H40A16,16,0,0,0,24,56V200a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A16,16,0,0,0,216,40Zm0,16V158.75l-26.07-26.06a16,16,0,0,0-22.63,0l-20,20-44-44a16,16,0,0,0-22.62,0L40,149.37V56ZM40,172l52-52,80,80H40Zm176,28H194.63l-36-36,20-20L216,181.38V200ZM144,100a12,12,0,1,1,12,12A12,12,0,0,1,144,100Z" />
    </svg>
  );
}
