"use client";

import { useEffect, useState } from "react";
import { useSkills } from "@/lib/state/skills-store";
import { SkillsList } from "./skills-list";
import { NewSkillDialog } from "./new-skill-dialog";
import { DiscoverySources } from "./discovery";
import { ManagePageHeader, managePageStyles as styles } from "@/components/ui/manage-page";
import { useTranslation } from "@/lib/i18n";

type Tab = "browse" | "discovery";

export function SkillsPage() {
  const { t, text } = useTranslation();
  const { skills, fetchSkills, error } = useSkills();
  const [tab, setTab] = useState<Tab>("browse");
  const [newOpen, setNewOpen] = useState(false);

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
    <div className={styles.view}>
      <ManagePageHeader
        title={t("nav.skills")}
        tabs={[
          { id: "browse", label: text("Browse", "浏览"), count: skills.length },
          { id: "discovery", label: text("Discovery", "发现") },
        ]}
        activeTab={tab}
        onTabChange={(id) => setTab(id as Tab)}
        actions={[
          { label: t("sidebar.refresh"), onClick: () => { void fetchSkills(); } },
          { label: text("New skill", "新建技能"), onClick: () => setNewOpen(true), primary: true },
        ]}
      />

      {error && <div className={styles.errorBar}>{error}</div>}

      <div className={styles.body}>
        {tab === "browse" ? <SkillsList /> : <DiscoverySources />}
      </div>

      <NewSkillDialog open={newOpen} onClose={() => setNewOpen(false)} />
    </div>
    </div>
  );
}
