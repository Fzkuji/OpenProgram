/**
 * Framework7's stylesheet, kept to the phone app's own parts.
 *
 * Framework7 styles plain class names (`.block`, `.grid`, `.card`, `.list`,
 * `.button`…) that the rest of the app also uses as Tailwind utilities or
 * its own classes, so its rules would land on the transcript and on every
 * desktop page shown inside the phone app. Each of its rules is rewritten
 * to match only inside the Framework7 root, and not inside an area the app
 * draws itself (`[data-not-f7]`), unless that area holds Framework7 parts
 * again (`[data-f7]`, the composer's controls), and not inside the app's
 * own marks set in Framework7's rows again. The added selectors sit in
 * `:where()`, so no rule changes its weight.
 *
 * Rules that only set Framework7's own variables (`--f7-*`) stay as they
 * are, and so do rules on `html`, `body` and `:root`.
 */

const SCOPE = ":where(.framework7-root, .framework7-root *)"
  + ":not(:where([data-not-f7] *:not([data-f7], [data-f7] *), [data-f7] [data-not-f7] *))";

// A pseudo-element (with the pseudo-classes it may carry) ends a selector;
// the scope goes before it.
const PSEUDO_ELEMENT = /(::?(?:before|after|first-line|first-letter)|::[\w-]+(?:\([^)]*\))?)(?::[\w-]+(?:\([^)]*\))?)*$/;
const GLOBAL_SUBJECT = /(^|[\s>+~])(html|body|:root)([.:#[][^\s>+~]*)?$/;

function scope(selector) {
  const trimmed = selector.trim();
  if (GLOBAL_SUBJECT.test(trimmed)) return trimmed;
  const pseudo = trimmed.match(PSEUDO_ELEMENT);
  if (pseudo) return trimmed.slice(0, pseudo.index) + SCOPE + pseudo[0];
  return trimmed + SCOPE;
}

module.exports = () => ({
  postcssPlugin: "openprogram-framework7-scope",
  Once(root) {
    const file = root.source?.input?.file ?? "";
    if (!/[\\/]framework7[\\/][^\\/]*\.css$/.test(file)) return;
    root.walkRules((rule) => {
      const parent = rule.parent;
      if (parent?.type === "atrule" && /keyframes$/i.test(parent.name)) return;
      const onlyVariables = rule.nodes?.every((node) => node.type !== "decl" || node.prop.startsWith("--"));
      if (onlyVariables) return;
      rule.selectors = rule.selectors.map(scope);
    });
  },
});
module.exports.postcss = true;
