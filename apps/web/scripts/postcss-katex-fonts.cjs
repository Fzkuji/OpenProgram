/** Supported Web/App browsers use WOFF2. Do not also ship KaTeX's WOFF/TTF fallbacks. */
module.exports = () => ({
  postcssPlugin: "openprogram-katex-woff2",
  AtRule: {
    "font-face": (rule) => {
      const family = rule.nodes?.find((node) => node.prop === "font-family")?.value;
      if (!family?.replace(/^["']|["']$/g, "").startsWith("KaTeX_")) return;
      rule.walkDecls("src", (declaration) => {
        const woff2 = declaration.value.match(/url\([^)]*\.woff2["']?\)\s*format\(["']woff2["']\)/);
        if (woff2) declaration.value = woff2[0];
      });
    },
  },
});
module.exports.postcss = true;
