"use client";

/**
 * Token Usage settings — read-only summary of token consumption across
 * every session on disk. Backed by GET /api/usage/summary, which
 * aggregates the already-recorded usage (no new instrumentation).
 *
 * Top: grand-total cards (input / output / total / cost / sessions /
 * turns). Below: a per-model breakdown table sourced from the
 * per-message history (the only layer carrying the model dimension).
 */
import { useEffect, useState } from "react";

import styles from "../settings-page.module.css";
import local from "./usage.module.css";
import { cachedFetch } from "@/lib/prefs/settings-cache";
import { useTranslation } from "@/lib/i18n";

type ModelRow = {
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  messages: number;
  cost: number | null;
};

type Summary = {
  totals: {
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    total_tokens: number;
    turns: number;
    sessions: number;
    sessions_with_usage: number;
    cost: number | null;
  };
  by_model: ModelRow[];
};

function fmtNum(n: number): string {
  return n.toLocaleString("en-US");
}

function fmtCost(c: number | null): string {
  if (c == null) return "—";
  if (c === 0) return "$0.00";
  if (c < 0.01) return "<$0.01";
  return "$" + c.toFixed(2);
}

export function TokenUsageSection() {
  const { t } = useTranslation();
  const [data, setData] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const d = await cachedFetch<Summary>("/api/usage/summary");
        if (alive) setData(d);
      } catch {
        if (alive) setError(true);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const totals = data?.totals;
  const cards = totals
    ? [
        { label: t("usage.card.input"), value: fmtNum(totals.input_tokens) },
        { label: t("usage.card.output"), value: fmtNum(totals.output_tokens) },
        { label: t("usage.card.total"), value: fmtNum(totals.total_tokens) },
        { label: t("usage.card.cost"), value: fmtCost(totals.cost) },
        { label: t("usage.card.sessions"), value: fmtNum(totals.sessions) },
        { label: t("usage.card.turns"), value: fmtNum(totals.turns) },
      ]
    : [];

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <h2 className={styles.pageTitle}>{t("settings.tab.usage")}</h2>
        <p className={styles.pageMeta}>{t("usage.desc")}</p>
      </div>
      <div className={styles.pageBody}>
        {loading && <div className={local.muted}>{t("usage.loading")}</div>}
        {error && <div className={local.muted}>{t("usage.error")}</div>}
        {!loading && !error && totals && (
          <>
            <div className={local.cards}>
              {cards.map((c) => (
                <div key={c.label} className={local.card}>
                  <div className={local.cardValue}>{c.value}</div>
                  <div className={local.cardLabel}>{c.label}</div>
                </div>
              ))}
            </div>

            <div className={local.tableWrap}>
              <h3 className={local.tableTitle}>{t("usage.byModel")}</h3>
              {data && data.by_model.length > 0 ? (
                <table className={local.table}>
                  <thead>
                    <tr>
                      <th>{t("usage.col.model")}</th>
                      <th>{t("usage.col.provider")}</th>
                      <th className={local.num}>{t("usage.col.input")}</th>
                      <th className={local.num}>{t("usage.col.output")}</th>
                      <th className={local.num}>{t("usage.col.cacheRead")}</th>
                      <th className={local.num}>{t("usage.col.messages")}</th>
                      <th className={local.num}>{t("usage.col.cost")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_model.map((r) => (
                      <tr key={r.model}>
                        <td className={local.modelCell}>{r.model}</td>
                        <td>{r.provider}</td>
                        <td className={local.num}>{fmtNum(r.input_tokens)}</td>
                        <td className={local.num}>{fmtNum(r.output_tokens)}</td>
                        <td className={local.num}>{fmtNum(r.cache_read_tokens)}</td>
                        <td className={local.num}>{fmtNum(r.messages)}</td>
                        <td className={local.num}>{fmtCost(r.cost)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className={local.muted}>{t("usage.empty")}</div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
