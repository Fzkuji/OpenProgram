"use client";

import { useEffect, useState } from "react";

import { useSessionScope } from "@/lib/session-store/session-scope";

const DEFAULT_PROFILE = "__agent__";

export function useToolProfiles() {
  const [toolProfiles, setToolProfiles] = useState<Record<string, string[]>>({});
  const activeProfile = useSessionScope((s) => s.settings.toolsProfile ?? DEFAULT_PROFILE);
  const patchSettings = useSessionScope((s) => s.patchSettings);

  useEffect(() => {
    fetch("/api/tool-profiles")
      .then((response) => response.json())
      .then((data) => setToolProfiles(data.profiles ?? {}))
      .catch(() => {});
  }, []);


  return {
    toolProfiles,
    activeProfile,
    switchProfile: (toolsProfile: string) => patchSettings({ toolsProfile }),
  };
}
