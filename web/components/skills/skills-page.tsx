"use client";

import { useEffect, useState } from "react";
import { useSkills } from "@/lib/skills-store";
import { SkillsList } from "./skills-list";
import { NewSkillDialog } from "./new-skill-dialog";
import { DiscoverySources } from "./discovery";
import { Button } from "@/components/ui/button";
import styles from "./skills-page.module.css";

type Tab = "browse" | "discovery";

export function SkillsPage() {
  const { skills, fetchSkills, error } = useSkills();
  const [tab, setTab] = useState<Tab>("browse");
  const [newOpen, setNewOpen] = useState(false);

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
    <div className={styles.view}>
      <div className={styles.topbar}>
        <span className={styles.title}>Skills</span>
        <div className={styles.tabs}>
          <button
            onClick={() => setTab("browse")}
            className={`${styles.tabBtn} ${tab === "browse" ? styles.active : ""}`}
          >
            Browse ({skills.length})
          </button>
          <button
            onClick={() => setTab("discovery")}
            className={`${styles.tabBtn} ${tab === "discovery" ? styles.active : ""}`}
          >
            Discovery
          </button>
        </div>
        <div className={styles.toolbar}>
          <Button size="sm" onClick={() => setNewOpen(true)}>New skill</Button>
        </div>
      </div>

      {error && <div className={styles.errorBar}>{error}</div>}

      {tab === "browse" ? (
        <div className={styles.singleColumn}>
          <SkillsList />
        </div>
      ) : (
        <div className={styles.singleColumn}>
          <div className={styles.singleColumnInner}>
            <DiscoverySources />
          </div>
        </div>
      )}

      <NewSkillDialog open={newOpen} onClose={() => setNewOpen(false)} />
    </div>
    </div>
  );
}
