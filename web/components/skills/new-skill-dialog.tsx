"use client";

import { useState } from "react";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { useSkills } from "@/lib/skills-store";

export function NewSkillDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { createSkill } = useSkills();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const reset = () => {
    setName(""); setDescription(""); setCategory(""); setBody(""); setErr(null);
  };

  const submit = async () => {
    if (!name.trim()) { setErr("name is required"); return; }
    setBusy(true);
    setErr(null);
    try {
      await createSkill({ name: name.trim(), description, category, body });
      reset();
      onClose();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) { reset(); onClose(); } }}>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>New skill</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label htmlFor="skill-name">Name</Label>
            <Input id="skill-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="my-skill" />
          </div>
          <div>
            <Label htmlFor="skill-desc">Description</Label>
            <Input id="skill-desc" value={description} onChange={(e) => setDescription(e.target.value)}
              placeholder="One-line trigger description" />
          </div>
          <div>
            <Label htmlFor="skill-cat">Category</Label>
            <Input id="skill-cat" value={category} onChange={(e) => setCategory(e.target.value)} placeholder="devops" />
          </div>
          <div>
            <Label htmlFor="skill-body">Body (markdown)</Label>
            <textarea id="skill-body" value={body} onChange={(e) => setBody(e.target.value)}
              rows={10}
              className="mt-1 w-full rounded-md border border-[var(--border)] bg-[var(--bg-secondary)] p-2 font-mono text-xs"
              placeholder="# My Skill&#10;&#10;What this skill does..." />
          </div>
          {err && <div className="text-xs text-destructive">{err}</div>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => { reset(); onClose(); }}>Cancel</Button>
          <Button onClick={submit} disabled={busy}>{busy ? "Creating…" : "Create"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
