// The page's own folder browser, for hosts where the system folder chooser cannot open.
import { useEffect, useState } from "react";
import { ArrowUp, Folder, GitBranch, Home } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { op } from "./api";
import { explain } from "./App";

interface Listing { path: string; parent: string; home: string; is_repo: boolean; entries: { name: string; path: string; is_repo: boolean }[] }

export function FolderDialog({ open, onOpenChange, start, onChoose }: {
  open: boolean; onOpenChange(open: boolean): void; start: string; onChoose(path: string): void;
}) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");

  const go = (path: string) => {
    setError("");
    op<Listing>("browse", path ? { path } : {}).then((l) => { setListing(l); setSelected(""); }).catch((e) => setError(explain(e.message)));
  };
  useEffect(() => { if (open) go(start); }, [open]);  // eslint-disable-line react-hooks/exhaustive-deps

  const target = selected || (listing?.is_repo ? listing.path : "");
  const targetIsRepo = selected ? !!listing?.entries.find((e) => e.path === selected)?.is_repo : !!listing?.is_repo;
  const crumbs = listing ? listing.path.split("/").filter(Boolean).map((part, i, all) => ({ part, path: "/" + all.slice(0, i + 1).join("/") })) : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="shadcn-scale gap-3 sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>选择项目文件夹</DialogTitle>
          <DialogDescription>选一个 Git 仓库。双击进入文件夹。</DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon-sm" aria-label="上一级" disabled={!listing?.parent} onClick={() => listing && go(listing.parent)}><ArrowUp /></Button>
          <Button variant="ghost" size="icon-sm" aria-label="主目录" onClick={() => go(listing?.home || "")}><Home /></Button>
          <nav aria-label="路径" className="flex min-w-0 flex-1 items-center gap-0.5 overflow-hidden text-[13px] text-text-muted">
            {crumbs.slice(-4).map((c, i, shown) => (
              <span key={c.path} className="flex min-w-0 items-center gap-0.5">
                <button type="button" onClick={() => go(c.path)} className={cn("truncate rounded px-1 py-0.5 hover:bg-[var(--bg-hover)]", i === shown.length - 1 && "font-medium text-text-bright")}>{c.part}</button>
                {i < shown.length - 1 ? <span aria-hidden="true">/</span> : null}
              </span>
            ))}
          </nav>
        </div>

        <div className="h-[320px] overflow-y-auto rounded-lg border border-border">
          <div role="listbox" aria-label="文件夹" className="p-1">
            {error ? <div className="p-3 text-sm text-[var(--accent-red)]">{error}</div> : null}
            {listing && !listing.entries.length ? <div className="p-3 text-sm text-text-muted">这里没有子文件夹。</div> : null}
            {listing?.entries.map((e) => (
              <button key={e.path} type="button" role="option" aria-selected={selected === e.path}
                onClick={() => setSelected(e.path)} onDoubleClick={() => go(e.path)}
                className={cn("flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-sm hover:bg-[var(--bg-hover)]",
                  selected === e.path && "bg-accent text-text-bright")}>
                {e.is_repo ? <GitBranch className="size-4 shrink-0 text-[var(--accent-blue)]" /> : <Folder className="size-4 shrink-0 text-[var(--folder-color)]" />}
                <span className="flex-1 truncate">{e.name}</span>
                {e.is_repo ? <Badge variant="secondary">Git 仓库</Badge> : null}
              </button>
            ))}
          </div>
        </div>

        <DialogFooter className="items-center sm:justify-between">
          <span className="truncate text-xs text-text-muted">{target ? (targetIsRepo ? "" : "这个文件夹不是 Git 仓库") : "选一个文件夹"}</span>
          <div className="flex gap-2">
            <Button variant="elevated" onClick={() => onOpenChange(false)}>取消</Button>
            <Button disabled={!target || !targetIsRepo} onClick={() => onChoose(target)}>打开</Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
