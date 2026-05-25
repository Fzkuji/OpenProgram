"use client";

import { Button } from "@/components/ui/button";
import styles from "../settings-page.module.css";
import type { SearchProvider } from "./types";

/**
 * "Setup" block in the provider detail panel: a "Get API key →" button
 * (when ``signup_url`` is present) plus a numbered list of
 * ``setup_steps`` from the catalog. Hidden entirely by the caller
 * when both fields are empty (e.g. zero-config DuckDuckGo).
 */
export function SearchProviderSetup({ provider }: { provider: SearchProvider }) {
  const steps = provider.setup_steps || [];
  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>Setup</span>
      </div>
      {provider.signup_url && (
        <div className={styles.detailRow}>
          <a
            className={styles.searchSetupGetKey}
            href={provider.signup_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            Get API key <span aria-hidden>→</span>
          </a>
          {provider.docs_url && provider.docs_url !== provider.signup_url && (
            <Button asChild variant="outline" size="sm">
              <a
                href={provider.docs_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Docs
              </a>
            </Button>
          )}
        </div>
      )}
      {steps.length > 0 && (
        <ol className={styles.searchSetupSteps}>
          {steps.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
    </div>
  );
}

