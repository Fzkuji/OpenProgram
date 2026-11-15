"use client";

/**
 * Web sources in the transcript: the links a provider-executed web search
 * returned (inside its timeline row) and the citations a text block carries
 * (one compact row under the text). Reading lives in
 * lib/chat/transcript/web-sources.ts.
 */
import { citationSources, type WebSource } from "@/lib/chat/transcript/web-sources";
import type { Citation } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

/** The sources of one provider search, one link per line. */
export function SearchSourceList({ sources }: { sources: WebSource[] }) {
  if (!sources.length) return null;
  return (
    <ol className="tl-sources">
      {sources.map((source) => (
        <li key={source.url}>
          <a href={source.url} target="_blank" rel="noopener noreferrer">{source.label}</a>
        </li>
      ))}
    </ol>
  );
}

/** The sources a text block cited, numbered in first-cited order and one per url. */
export function CitationSources({ citations }: { citations?: Citation[] }) {
  const { text } = useTranslation();
  const sources = citationSources(citations);
  if (!sources.length) return null;
  return (
    <nav className="message-citations" aria-label={text("Sources", "来源")}>
      {sources.map((source, index) => (
        <a
          key={source.url}
          className="message-citation"
          href={source.url}
          target="_blank"
          rel="noopener noreferrer"
        >
          <span className="message-citation-index">{index + 1}</span>
          <span className="message-citation-label">{source.label}</span>
        </a>
      ))}
    </nav>
  );
}
