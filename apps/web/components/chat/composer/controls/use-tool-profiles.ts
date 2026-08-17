"use client";

import { useEffect, useState } from "react";

const DEFAULT_PROFILE = "__agent__";

export function useToolProfiles(sessionId: string | null) {
  const [toolProfiles, setToolProfiles] = useState<Record<string, string[]>>({});
  const [activeProfile, setActiveProfile] = useState(DEFAULT_PROFILE);

  useEffect(() => {
    fetch("/api/tool-profiles")
      .then((response) => response.json())
      .then((data) => setToolProfiles(data.profiles ?? {}))
      .catch(() => {});
  }, []);

  useEffect(() => setActiveProfile(DEFAULT_PROFILE), [sessionId]);

  return {
    toolProfiles,
    activeProfile,
    switchProfile: setActiveProfile,
  };
}
