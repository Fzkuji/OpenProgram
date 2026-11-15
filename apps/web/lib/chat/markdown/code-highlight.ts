/**
 * Syntax highlighting for fenced code in chat. Only a fixed set of common
 * languages is registered (highlight.js core + per-language modules) so
 * the bundle stays small; unknown languages render as plain text.
 */
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import shell from "highlight.js/lib/languages/shell";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

const LANGUAGES = {
  bash, c, cpp, css, diff, dockerfile, go, ini, java, javascript, json,
  markdown, python, rust, shell, sql, typescript, xml, yaml,
};
for (const [name, def] of Object.entries(LANGUAGES)) hljs.registerLanguage(name, def);

function unescapeHtml(s: string): string {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&");
}

/** Highlight one escaped code body (as marked emits it). Returns the
 *  escaped body unchanged when the language is unknown or too large. */
export function highlightEscapedCode(escaped: string, lang: string): string {
  const language = lang.trim().toLowerCase();
  if (!language || !hljs.getLanguage(language) || escaped.length > 60_000) return escaped;
  try {
    return hljs.highlight(unescapeHtml(escaped), { language, ignoreIllegals: true }).value;
  } catch {
    return escaped;
  }
}
