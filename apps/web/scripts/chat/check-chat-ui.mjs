/**
 * Rule: the chat transcript's CSS contracts (focus halo, timeline geometry,
 * hover-revealed actions, plain Conclusion content, composer compact mode)
 * and its re-render guards hold. linkedom computes no styles, so these stay
 * static; behaviour lives in tests/chat/messages/*.test.mjs.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";

const root = new URL("../../", import.meta.url);
const source = (path) => readFileSync(new URL(path, root), "utf8");
const cssDir = (dir) => readdirSync(new URL(dir, root), { recursive: true })
  .filter((name) => name.endsWith(".css")).sort()
  .map((name) => source(dir + name));

const chatCss = [...cssDir("app/styles/chat/"), ...cssDir("app/styles/dag/"), source("app/styles/tool-tones.css")].join("\n");
const baseCss = [...cssDir("app/styles/design/"), source("app/styles/themes/fallback.css"), ...cssDir("app/styles/base/")].join("\n");
const tabsCss = cssDir("components/center-tabs/strip/").join("\n");
const executionCss = source("app/styles/chat/execution/execution-strip.css");
const composerCss = source("components/chat/composer/composer.module.css");
const messageList = source("components/chat/messages/list/message-list.tsx");
const messageRail = source("components/chat/messages/navigation/message-rail.tsx");
const rules = (css) => [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)];

// ── Focus halo: mouse focus and non-tab buttons never draw an outer halo;
// top tabs keep a theme-owned focus ring.
assert.match(baseCss, /--accent-blue:\s*var\(--theme-accent\);/);
assert.match(baseCss, /--primary:\s*var\(--accent-blue\);/);
assert.match(baseCss, /--ring:\s*var\(--focus-ring\);/);
assert.match(
  baseCss,
  /button:focus:not\(\[role="tab"\]\),\s*\[role="button"\]:focus\s*\{[^}]*outline:\s*none\s*!important;[^}]*--tw-ring-shadow:\s*0 0 #0000\s*!important;[^}]*--tw-ring-offset-shadow:\s*0 0 #0000\s*!important;/s,
);
assert.match(
  baseCss,
  /button:focus-visible:not\(\[role="tab"\]\),\s*\[role="button"\]:focus-visible\s*\{[^}]*box-shadow:\s*none\s*!important;[^}]*filter:\s*brightness\(1\.08\);/s,
);
for (const theme of ["dark", "beige-dark"]) {
  assert.match(source(`app/styles/themes/${theme}.css`), /--focus-ring:\s*color-mix\(in srgb, var\(--text-bright\) 50%, transparent\);/);
}
for (const theme of ["light", "beige-light"]) {
  assert.match(source(`app/styles/themes/${theme}.css`), /--focus-ring:\s*color-mix\(in srgb, var\(--text-bright\) 45%, transparent\);/);
}
assert.match(source("app/styles/themes/aurora.css"), /--focus-ring:\s*color-mix\(in srgb, var\(--accent-cyan\) 60%, transparent\);/);
assert.match(tabsCss, /\.tab:has\(\.tabTarget:focus-visible\)\s*\{[^}]*outline:\s*2px solid var\(--focus-ring\)/s);
assert.match(tabsCss, /\.compoundTab:has\(\.compoundTarget:focus-visible\)\s*\{[^}]*outline:\s*2px solid var\(--focus-ring\)/s);

// ── Re-render guards. updateMessage returns a fresh messagesById per
// streaming token: the list selects only the last row's role, and the
// transcript never unloads history behind a virtualizer.
assert.doesNotMatch(messageList, /useSessionStore\(\(s\) => s\.messagesById\)/, "MessageList must not subscribe to the whole messagesById map");
assert.match(messageList, /s\.messagesById\[lastId\]\?\.role/);
for (const file of [messageList, messageRail]) {
  assert.doesNotMatch(file, /react-virtuoso|react-window|@tanstack\/react-virtual/, "the transcript must not use a virtual list");
}

// ── Tooltips only on controls: message actions and the edit box use
// HoverTip, never a native title; the context panel uses the shared menu frame.
const messageActions = source("components/chat/messages/bubbles/message-actions.tsx");
assert.match(messageActions, /<HoverTip label=\{<TipBody title=\{title\} \/>\}>/);
assert.doesNotMatch(messageActions, /<(?:button|Button)\b[^>]*\stitle=\{/, "message actions must not use native title tooltips");
assert.doesNotMatch(source("components/chat/messages/editing/message-editor.tsx"), /<(?:button|Button)\b[^>]*\stitle=\{/, "the edit box must not use native title tooltips");
assert.match(source("components/chat/badges/context-breakdown-panel.tsx"), /cn\(MENU_PANEL, "context-breakdown-panel w-\[320px\]"\)/);

// ── Markdown never widens the column: the .md-table wrapper is the scroll
// box, and long unbroken tokens wrap.
assert.match(chatCss, /\.message-content \.md-table \{[^}]*overflow-x:\s*auto;/);
assert.match(chatCss.slice(chatCss.indexOf(".message-content {"), chatCss.indexOf(".message-content pre {")), /overflow-wrap: anywhere/);
assert.match(chatCss.slice(chatCss.indexOf(".message-content code {"), chatCss.indexOf(".message-content pre code")), /overflow-wrap: anywhere/);

// ── Timestamps and actions appear together on hover or keyboard focus,
// always on touch; the footer sits one reply gap below the body.
assert.match(chatCss, /\.message-actions\s*\{[^}]*opacity:\s*0/s);
assert.match(chatCss, /\.message:hover \.message-actions,\s*\.attach-card:hover \.message-actions,\s*\.message-actions:focus-within\s*\{[^}]*opacity:\s*1/s);
assert.doesNotMatch(chatCss, /\.message \.message-actions-footer \.message-actions\s*\{[^}]*opacity:\s*1/s);
assert.match(chatCss, /@media\s*\(hover:\s*none\)[\s\S]*\.message \.message-actions,[\s\S]*\.attach-card \.message-actions[\s\S]*opacity:\s*1/);
assert.match(executionCss, /@media\s*\(hover:\s*none\)[\s\S]*\.runtime-card-host \.message-actions,[\s\S]*\.tl-step-act[\s\S]*opacity:\s*1/);
assert.match(chatCss, /\.message\.assistant \.message-actions-footer\s*\{[^}]*margin-top:\s*calc\(var\(--reply-gap, 12px\) - 3px\)/s);
assert.match(chatCss, /\.runtime-actions-footer\s*\{[^}]*margin-top:\s*14px/s);
assert.match(chatCss, /\.runtime-actions-footer\s*\{[^}]*padding-left:\s*0(?:px)?\s*;/s);

// ── Compaction: folded originals skip layout, dividers do not stay at the
// appear animation's start, the bar stays on one row, text controls underline.
assert.match(chatCss, /\.compaction-orig-fold\[data-open="0"\] \.compaction-orig-fold-inner\s*\{[\s\S]*?content-visibility:\s*hidden/);
assert.match(chatCss, /\.compaction-orig-fold\[data-open="1"\] \.compaction-orig-fold-inner\s*\{[\s\S]*?content-visibility:\s*visible/);
assert.match(chatCss, /\.message\.system-event[\s\S]*animation:\s*none[\s\S]*opacity:\s*1/);
assert.match(chatCss, /\.compaction-card-bar[\s\S]*flex-wrap:\s*nowrap/);
assert.match(chatCss, /\.text-hit:hover[\s\S]*text-decoration-color:\s*currentColor/);

// ── The workflow Conclusion is ordinary chat content, never a blockquote.
function assertPlainConclusionRules(css) {
  const conclusion = rules(css).filter(([, selectors]) => selectors.split(",").some((selector) =>
    /\.runtime-program-conclusion(?![\w-])/.test(selector.trim().split(/[\s>+~]+/).at(-1) ?? "")));
  assert.ok(conclusion.length > 0, "workflow Conclusion outer CSS rules must exist");
  for (const [, selector, declarations] of conclusion) {
    assert.doesNotMatch(declarations, /(?:border|padding)-(?:left|inline-start)(?:-[\w-]+)?\s*:/,
      `${selector.trim()} must render as ordinary chat content`);
  }
}
assertPlainConclusionRules(chatCss);
// The checker itself rejects quote-style variants whatever the class order.
assert.throws(() => assertPlainConclusionRules(`${chatCss}\n.is-error.runtime-program-conclusion:hover { border-inline-start: 2px solid red; }`));
assert.doesNotThrow(() => assertPlainConclusionRules(`${chatCss}\n.runtime-program-conclusion .message-content ul { padding-inline-start: 20px; }`));
assert.match(chatCss, /\.runtime-program-conclusion-summary\.message-content,\s*\.runtime-program-conclusion-result\.message-content\s*\{[^}]*padding:\s*0/s);

// ── Timeline geometry: tiles sit in the 34px indent centred on their row,
// rails run behind the tile centres, tones per kind and tool.
const iconRule = chatCss.match(/\.tl-step-icon\s*\{([^}]*)\}/)?.[1] ?? "";
assert.match(iconRule, /position:\s*absolute;[^}]*left:\s*-34px;[^}]*top:\s*50%;[^}]*transform:\s*translateY\(-50%\);[^}]*width:\s*24px;[^}]*height:\s*24px/s);
assert.match(iconRule, /border-radius:\s*var\(--radius-sm\);[^}]*color:\s*var\(--tone\);[^}]*background:\s*var\(--bg-primary\);/s);
assert.doesNotMatch(iconRule, /box-shadow/);
assert.match(chatCss, /\.tl-body::before\s*\{[^}]*left:\s*11\.5px;[^}]*width:\s*1px/s);
assert.match(chatCss, /\.tl-sub::before\s*\{[^}]*left:\s*9\.5px;[^}]*width:\s*1px/s);
assert.match(chatCss, /\.tl-sub\s+\.tl-step-icon\s*\{[^}]*left:\s*-30px;[^}]*width:\s*20px;[^}]*height:\s*20px/s);
for (const [kind, light, dark] of [
  ["thinking", "#7c3aed", "#b78cff"], ["function", "#d14a1f", "#ff9a66"],
  ["llm", "#00849a", "#45d7e8"], ["subagent", "#0b8a4b", "#4bd58a"],
]) {
  assert.match(chatCss, new RegExp(`--timeline-${kind}:\\s*light-dark\\(${light},\\s*${dark}\\)`));
  assert.match(chatCss, new RegExp(`\\.tl-step-icon\\.k-${kind}\\s*\\{[^}]*--tone:\\s*var\\(--timeline-${kind}\\)`));
}
for (const tone of ["shell", "edit", "read", "search", "web", "agent", "memory", "plan", "media", "system", "function"]) {
  assert.match(chatCss, new RegExp(`\\.tl-step-icon\\.t-${tone}\\s*\\{[^}]*--tone:`), `tool tone ${tone} needs a tile colour`);
}
assert.match(chatCss, /\.tl-step-icon\.is-running::after\s*\{[^}]*conic-gradient[^}]*animation:\s*tlOrbit/s);
assert.match(chatCss, /\.tl\s*\{[^}]*--timeline-line:\s*light-dark\(rgba\(36, 36, 32, 0\.22\),\s*rgba\(226, 225, 218, 0\.28\)\);/s);
assert.doesNotMatch(chatCss, /var\(--timeline-fill\)/, "expandable markers must not fall back to a neutral grey fill");
for (const [, selectors] of rules(chatCss).filter(([, selectors]) => selectors.includes(".tl-step-icon"))) {
  assert.equal(selectors.split(",").some((selector) => selector.includes(":hover")), false, "timeline tiles have no hover ring");
}

// ── Timeline motion: details open from a clipped zero-height state on the
// shared curves, and honour reduced motion.
assert.match(
  chatCss.match(/\.tl-step > \.tl-collapse\s*\{([^}]*)\}/)?.[1] ?? "",
  /opacity:\s*0;[^}]*transform:\s*translateY\(-3px\);[^}]*pointer-events:\s*none;[^}]*grid-template-rows var\(--motion-slow\) cubic-bezier\(0\.2,\s*0\.7,\s*0\.2,\s*1\)[\s\S]*opacity var\(--motion-medium\) var\(--ease-standard\)/s,
);
assert.match(chatCss, /\.tl-step > \.tl-collapse\.is-open\s*\{[^}]*opacity:\s*1;[^}]*transform:\s*translateY\(0\);[^}]*pointer-events:\s*auto/s);
assert.match(chatCss, /\.tl-collapse\s*\{[^}]*transition:\s*grid-template-rows var\(--motion-medium\) var\(--ease-standard\)/s);
assert.match(baseCss, /@media \(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?transition-duration:\s*0\.01ms !important;/);

// ── Row actions fade in with the row highlight; pointer focus does not pin
// them, keyboard focus reveals them.
assert.match(chatCss.match(/\.tl-step-act\s*\{([^}]*)\}/)?.[1] ?? "", /transition:\s*opacity var\(--motion-fast\) var\(--ease-standard\);/);
assert.doesNotMatch(chatCss, /\.tl-step-head:focus-within\s+\.tl-step-act/);
assert.match(chatCss, /\.tl-step-head:has\(:focus-visible\)\s+\.tl-step-act/);

// ── Function summary row: avatar column, baseline-aligned 28px toggle, and
// no vertical nudges anywhere in it (the chevron centres itself).
assert.match(chatCss, /\.tl-toggle\s*\{[^}]*display:\s*inline-flex[^}]*align-items:\s*baseline[^}]*height:\s*var\(--ui-button-h-sm\)[^}]*line-height:\s*28px[^}]*margin:\s*0 0 1\.5px[^}]*padding:\s*0/s);
assert.match(chatCss, /\.runtime-card-host\s*\{[^}]*margin:\s*20px 8px/s);
assert.match(chatCss, /\.runtime-program-run\s*\{[^}]*grid-template-columns:\s*28px minmax\(0, 1fr\)[^}]*align-items:\s*start/s);
assert.match(chatCss, /\.runtime-program-avatar\s*\{[^}]*width:\s*28px[^}]*height:\s*28px[^}]*border-radius:\s*var\(--radius-md\)/s);
assert.match(chatCss, /\.runtime-program-content\s*\{[^}]*min-width:\s*0/s);
function assertNoSummaryVerticalOffset(css) {
  for (const [, selector, declarations] of rules(css)) {
    if (/\.tl-chev/.test(selector) || !/\.runtime-program-avatar|\.runtime-program-content|\.tl-toggle/.test(selector)) continue;
    assert.doesNotMatch(declarations,
      /(?:^|;)\s*(?:top|bottom|translate|vertical-align|align-self|place-self|margin-top|margin-block(?:-start|-end)?|padding-top|padding-block(?:-start|-end)?|inset-block(?:-start|-end)?)\s*:/,
      `${selector.trim()} must not vertically offset the Function summary`);
    assert.doesNotMatch(declarations, /(?:^|;)\s*transform\s*:[^;]*translate/i, `${selector.trim()} must not translate the Function summary`);
  }
}
assertNoSummaryVerticalOffset(chatCss);
assert.throws(() => assertNoSummaryVerticalOffset(".runtime-program-avatar { transform: translateY(1px); }"));
assert.throws(() => assertNoSummaryVerticalOffset(".runtime-program-content > .tl > .tl-toggle > span { bottom: -1px; }"));
assert.match(chatCss, /\.tl-chev\s*\{[^}]*align-self:\s*center/s);
assert.doesNotMatch(chatCss, /\.tl-chev\s*\{[^}]*(?:translate|top|margin-top)\s*:/s);

// ── Composer control row: Buttons keep their own surface, and compact mode
// hides labels without display:none, measured on a transition-free copy
// (the measuring itself is tested in compact-controls-row.test.mjs).
const surfaceStart = composerCss.indexOf("/* Every control in this row is a shadcn Button (buttonVariants");
const compactStart = composerCss.indexOf("/* Narrow composer control labels", surfaceStart);
const compactEnd = composerCss.indexOf("/* The options (+) dropdown", compactStart);
assert.ok(surfaceStart >= 0 && compactStart > surfaceStart && compactEnd > compactStart, "composer control CSS blocks moved");
const surfaces = composerCss.slice(surfaceStart, compactStart);
for (const selector of [".plusBtn", ".toolChip", ".effortText"]) {
  assert.match(surfaces, new RegExp(`\\.controlsRow \\${selector}[^{]*\\{[^}]*background:\\s*revert-layer`, "s"));
}
assert.doesNotMatch(surfaces, /var\(--chip-bg\)/, "bottom-row controls must not paint their own chip fill over the Button");
const compact = composerCss.slice(compactStart, compactEnd);
assert.match(compact, /\.controlsRow\[data-compact="true"\]/);
assert.match(compact, /\.effortIcon\s*\{\s*color:\s*var\(--effort-color\)/);
assert.match(compact, /\.permission-badge[\s\S]*aspect-ratio:\s*1;/);
assert.match(compact, /\.agent-badge[\s\S]*aspect-ratio:\s*1;/);
assert.doesNotMatch(compact, /\.(?:permission-badge|agent-badge|effort-pill-host)[^{]*\{[^}]*display:\s*none/s);
assert.match(composerCss, /\.controlsRow\[data-measuring\] \*\s*\{[^}]*transition:\s*none !important/);

console.log("chat-ui checks passed");
