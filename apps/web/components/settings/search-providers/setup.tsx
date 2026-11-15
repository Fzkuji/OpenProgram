"use client";

import { Button } from "@/components/ui/button";
import styles from "../providers/provider-pane.module.css";
import type { SearchProvider } from "./types";
import { useTranslation } from "@/lib/i18n";

/**
 * "Setup" block in the provider detail panel: a "Get API key →" button
 * (when ``signup_url`` is present) plus a numbered list of
 * ``setup_steps`` from the catalog. Hidden entirely by the caller
 * when both fields are empty (e.g. zero-config DuckDuckGo).
 */
export function SearchProviderSetup({ provider }: { provider: SearchProvider }) {
  const { text } = useTranslation();
  const steps = provider.setup_steps || [];
  return (
    <div className="flex flex-col gap-[10px]">
      <div className={styles.detailSectionTitle}>
        <span>{text("Setup", "设置")}</span>
      </div>
      {provider.signup_url && (
        <div className={`${styles.detailRow} flex gap-[10px] items-center`}>
          <Button asChild variant="elevated">
            <a
              href={provider.signup_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {provider.env_var ? text("Get API key", "获取 API key") : text("Open website", "打开官网")} <span aria-hidden>→</span>
            </a>
          </Button>
          {provider.docs_url && provider.docs_url !== provider.signup_url && (
            <Button asChild variant="elevated">
              <a
                href={provider.docs_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                {text("Docs", "文档")}
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
