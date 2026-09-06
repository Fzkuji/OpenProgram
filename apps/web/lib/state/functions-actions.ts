"use client";

import { useFunctions } from "./functions-store";
import { useSessionStore, type AgenticFunction } from "@/lib/session-store";
import { jsonFetch } from "@/lib/net/fetch-client";
import { showToast } from "@/lib/format-utils/toast";
import { translateText } from "@/lib/i18n";

let latestRefresh = 0;

/** Refresh both catalog readers. Failed requests preserve the last usable list. */
export async function refreshFunctionsList(signal?: AbortSignal): Promise<AgenticFunction[] | null> {
  const request = ++latestRefresh;
  const previous = useFunctions.getState().functions;
  try {
    const data = await jsonFetch<AgenticFunction[]>("/api/programs", { signal });
    if (!Array.isArray(data)) throw new TypeError("/api/programs must return an array");
    if (signal?.aborted) return null;
    const current = useFunctions.getState().functions;
    if (request !== latestRefresh || current !== previous) return current;
    useFunctions.getState().setFunctions(data);
    return data;
  } catch (error) {
    if (!signal?.aborted) console.error("Refresh functions failed:", error);
    return null;
  }
}

let latestLaunch = 0;

/** Resolve a Program and open its parameters without executing or sending it. */
export async function openFunctionForm(name: string, signal?: AbortSignal): Promise<void> {
  const launch = ++latestLaunch;
  const owner = useSessionStore.getState().activeChatKey;
  let fn = useFunctions.getState().functions.find((item) => item.name === name);
  let fetched: AgenticFunction[] | null = [];
  if (!fn) {
    fetched = await refreshFunctionsList(signal);
    fn = fetched?.find((item) => item.name === name);
  }
  if (signal?.aborted || launch !== latestLaunch || useSessionStore.getState().activeChatKey !== owner) return;
  if (fn) {
    useSessionStore.getState().openFnForm(fn);
    return;
  }
  showToast(fetched === null
    ? translateText("Programs could not be loaded. Try again.", "无法加载 Programs，请重试。")
    : translateText(`Program ${name} is unavailable. Check its installation and refresh Programs.`, `Program ${name} 不可用，请检查安装并刷新 Programs。`),
  { tone: "error" });
}
