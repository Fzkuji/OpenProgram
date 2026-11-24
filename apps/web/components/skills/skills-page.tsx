"use client";

import { useEffect, useState } from "react";
import { useSkills } from "@/lib/abilities/skills-store";
import { SkillsList } from "./skills-list";
import { NewSkillDialog } from "./new-skill-dialog";
import { DiscoverySources } from "./discovery";
import { ManagePageHeader, ManageSubnav, managePageStyles as styles } from "@/components/ui/manage-page";
import { useTranslation } from "@/lib/i18n";
import { PlusIcon } from "@/components/animated-icons";

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
  const { skills, fetchSkills, ready } = useSkills();
  const [tab, setTab] = useState<Tab>("browse");
  const [localNewOpen, setLocalNewOpen] = useState(false);
  const newOpen = newOpenProp ?? localNewOpen;
  const closeNew = onNewClose ?? (() => setLocalNewOpen(false));

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  const tabs = [
    { id: "browse", label: text("Installed", "已安装"), count: ready ? skills.length : undefined },
    { id: "discovery", label: text("Discover", "发现") },
  ];
  const enabledCount = skills.filter((skill) => skill.enabled).length;

  // Until the first list arrives the panel stays empty: "No skills yet"
  // then would be untrue. Later visits draw the kept list at once.
  const body = tab === "browse"
    ? ready ? <SkillsList externalFilter={query} /> : null
    : <DiscoverySources query={query} />;

  const content = (
    <>
      <ManageSubnav
        tabs={tabs}
        activeTab={tab}
        onTabChange={(id) => setTab(id as Tab)}
        summary={ready ? text(`${enabledCount} available`, `可用 ${enabledCount} 个`) : undefined}
        action={{
          label: text("Add skill", "添加技能"),
          onClick: () => setLocalNewOpen(true),
          icon: PlusIcon,
          primary: true,
        }}
        ariaLabel={text("Skill sections", "技能分区")}
        panelId="skills-panel"
      />
      <div
        id="skills-panel"
        role="tabpanel"
        aria-labelledby={`skills-panel-tab-${tab}`}
        className={styles.body}
      >{body}</div>
      <NewSkillDialog open={newOpen} onClose={closeNew} />
    </>
  );

  if (embedded) return content;

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
    <div className={styles.view}>
      <ManagePageHeader
        title={t("nav.skills")}
        actions={[
          { label: t("sidebar.refresh"), onClick: () => { void fetchSkills(); } },
        ]}
      />
      {content}
    </div>
    </div>
  );
}
