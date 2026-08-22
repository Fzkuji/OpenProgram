"use client";

import { useEffect, useState } from "react";
import { useSkills } from "@/lib/state/skills-store";
import { SkillsList } from "./skills-list";
import { NewSkillDialog } from "./new-skill-dialog";
import { DiscoverySources } from "./discovery";
import { ManagePageHeader, ManageSubnav, managePageStyles as styles } from "@/components/ui/manage-page";
import { useTranslation } from "@/lib/i18n";

type Tab = "browse" | "discovery";

export function SkillsPage({
  embedded,
  query,
  newOpen: newOpenProp,
  onNewClose,
}: {
  embedded?: boolean;
  query?: string;
  newOpen?: boolean;
  onNewClose?: () => void;
} = {}) {
  const { t, text } = useTranslation();
  const { skills, fetchSkills, error } = useSkills();
  const [tab, setTab] = useState<Tab>("browse");
  const [localNewOpen, setLocalNewOpen] = useState(false);
  const newOpen = newOpenProp ?? localNewOpen;
  const closeNew = onNewClose ?? (() => setLocalNewOpen(false));

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  const tabs = [
    { id: "browse", label: text("Installed", "已安装"), count: skills.length },
    { id: "discovery", label: text("Discover", "发现") },
  ];
  const enabledCount = skills.filter((skill) => skill.enabled).length;

  const body = tab === "browse" ? <SkillsList externalFilter={query} /> : <DiscoverySources query={query} />;

  if (embedded) {
    return (
      <>
        <ManageSubnav
          tabs={tabs}
          activeTab={tab}
          onTabChange={(id) => setTab(id as Tab)}
          summary={text(
            `${skills.length} installed · ${enabledCount} available`,
            `已安装 ${skills.length} 个 · 可用 ${enabledCount} 个`,
          )}
        />
        {error && <div className={styles.errorBar}>{error}</div>}
        <div className={styles.body}>{body}</div>
        <NewSkillDialog open={newOpen} onClose={closeNew} />
      </>
    );
  }

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
    <div className={styles.view}>
      <ManagePageHeader
        title={t("nav.skills")}
        tabs={tabs}
        activeTab={tab}
        onTabChange={(id) => setTab(id as Tab)}
        actions={[
          { label: t("sidebar.refresh"), onClick: () => { void fetchSkills(); } },
          { label: text("Add skill", "添加技能"), onClick: () => setLocalNewOpen(true), primary: true },
        ]}
      />

      {error && <div className={styles.errorBar}>{error}</div>}

      <div className={styles.body}>
        {body}
      </div>

      <NewSkillDialog open={newOpen} onClose={closeNew} />
    </div>
    </div>
  );
}
