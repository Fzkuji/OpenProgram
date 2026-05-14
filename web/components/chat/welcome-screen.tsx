/**
 * WelcomeScreen — empty-chat onboarding panel.
 *
 * Renders the `{LLM}` logo, the "Agentic Programming" title, the help
 * text, and the four function-shortcut buttons (Run analyze_sentiment,
 * Deep work, Create function, Edit function). Visible whenever the
 * session store says so; mounted as a portal inside #welcome-mount
 * placeholder that PageShell leaves in the chat area.
 *
 * Example buttons hand off to `clickFnExample` which now opens the
 * React FunctionForm in the Composer.
 */
"use client";

import { useSessionStore } from "@/lib/session-store";
import { useLegacyGlobals } from "@/components/sidebar/use-legacy-globals";

import styles from "./welcome-screen.module.css";

interface Example {
  name: string;
  label: string;
  icon: React.ReactNode;
}

const EXAMPLES: Example[] = [
  {
    name: "analyze_sentiment",
    label: "Run analyze_sentiment",
    icon: <SmileIcon />,
  },
  {
    name: "deep_work",
    label: "Deep work",
    icon: <BookIcon />,
  },
  {
    name: "create",
    label: "Create function",
    icon: <DocumentIcon />,
  },
  {
    name: "edit",
    label: "Edit function",
    icon: <WandIcon />,
  },
];

export function WelcomeScreen() {
  const visible = useSessionStore((s) => s.welcomeVisible);
  const openFnForm = useSessionStore((s) => s.openFnForm);
  const fnFormFunction = useSessionStore((s) => s.fnFormFunction);
  const setComposerInput = useSessionStore((s) => s.setComposerInput);
  const focusComposer = useSessionStore((s) => s.focusComposer);
  const { availableFunctions } = useLegacyGlobals();

  if (!visible) return null;

  const collapsed = fnFormFunction !== null;

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

function SmileIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="18"
      height="18"
      fill="currentColor"
      viewBox="0 0 256 256"
    >
      <path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216ZM80,108a12,12,0,1,1,12,12A12,12,0,0,1,80,108Zm96,0a12,12,0,1,1-12-12A12,12,0,0,1,176,108Zm-1.07,48c-10.29,17.79-27.4,28-46.93,28s-36.63-10.2-46.92-28a8,8,0,1,1,13.84-8c7.47,12.91,19.21,20,33.08,20s25.61-7.1,33.07-20a8,8,0,0,1,13.86,8Z" />
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

function DocumentIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="18"
      height="18"
      fill="currentColor"
      viewBox="0 0 256 256"
    >
      <path d="M80,64a8,8,0,0,1,8-8h80a8,8,0,0,1,0,16H88A8,8,0,0,1,80,64Zm8,48h80a8,8,0,0,0,0-16H88a8,8,0,0,0,0,16Zm40,16H88a8,8,0,0,0,0,16h40a8,8,0,0,0,0-16ZM216,88V216a16,16,0,0,1-16,16H56a16,16,0,0,1-16-16V40A16,16,0,0,1,56,24H168a8,8,0,0,1,5.66,2.34l40,40A8,8,0,0,1,216,72Zm-56-8h28.69L176,67.31V80ZM200,216V96H168a8,8,0,0,1-8-8V56H56V216H200Z" />
    </svg>
  );
}

function WandIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="18"
      height="18"
      fill="currentColor"
      viewBox="0 0 256 256"
    >
      <path d="M226.76,69a8,8,0,0,0-12.84-2.88l-40.3,37.19-17.23-3.7-3.7-17.23,37.19-40.3A8,8,0,0,0,187,29.24,72,72,0,0,0,88,96a72.34,72.34,0,0,0,3.79,16.76L33.17,159.05a32,32,0,0,0,45.26,45.26l46.29-58.58A72.34,72.34,0,0,0,144,152,72,72,0,0,0,226.76,69ZM144,136a56.5,56.5,0,0,1-18-2.93,8,8,0,0,0-8.58,2.13L67.06,193.25a16,16,0,0,1-22.62-22.62l58.05-50.31a8,8,0,0,0,2.13-8.58A56.5,56.5,0,0,1,104,96a56,56,0,0,1,97.61-37.42l-30.69,33.24a8,8,0,0,0-1.85,6.36l5.21,24.23a8,8,0,0,0,6.15,6.15l24.23,5.21,33.24-30.69A56,56,0,0,1,144,136Z" />
    </svg>
  );
}
