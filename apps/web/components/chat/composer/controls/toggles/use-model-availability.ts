"use client";

import { useCallback } from "react";
import { useQuery } from "@tanstack/react-query";

import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";
import { api } from "@/lib/net/api";

export function useModelAvailability() {
  const { text } = useTranslation();
  const { data: enabledModels } = useQuery({
    queryKey: ["models-enabled"],
    queryFn: api.listEnabledModels,
  });
  const promptNeedModel = useCallback(() => {
    showToast(
      text(
        "No model configured — enable one before sending.",
        "还没配置模型 — 发送前请先启用一个模型。",
      ),
      {
        tone: "warn",
        link: {
          label: text("Open Providers →", "去配置服务商 →"),
          href: "/settings/providers?add=1",
        },
      },
    );
  }, [text]);

  return {
    noEnabledModels: (enabledModels ?? []).length === 0,
    promptNeedModel,
  };
}
