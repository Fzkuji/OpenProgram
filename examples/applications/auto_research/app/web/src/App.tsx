// The app's two places: the welcome page until a project is open, then the project's workspace.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { showToast } from "@/lib/format-utils/toast";
import { cancel, loadSaved, op, setProject, store, type Saved } from "./api";
import { baseName, type Overview, type Task } from "./model";
import { Welcome } from "./Welcome";
import { Workspace } from "./Workspace";
import { FolderDialog } from "./FolderDialog";

export type Route = { page: "overview" } | { page: "task"; task: string };

export interface Step { key: string; label: string; state: "run" | "done" | "fail"; seconds?: number }
export interface Job {
  id?: string; task: string; kind: "check"; title: string; started: number;
  steps: Step[]; done?: boolean; error?: string; result?: any;
}

interface Ctx {
  saved: Saved; home: string; view: Overview | null; route: Route; job: Job | null;
  go(route: Route): void; refresh(): Promise<void>;
  openFolder(): void; openProject(path: string): Promise<boolean>; closeProject(): void; forget(path: string): void;
  check(task: Task, inputs: Record<string, string>): void;
  stop(): void;
  /** Bumped when the project's files may have changed (a check ran, a reply ended). */
  changes: number; touched(): void;
}
const AppContext = createContext<Ctx>(null as unknown as Ctx);
export const useApp = () => useContext(AppContext);

/** The backend's error, in the author's words where it is one the author can act on. */
export function explain(message: string) {
  const repo = message.match(/(\S+) is not a Git repository/);
  if (repo) return `「${baseName(repo[1])}」不是 Git 仓库，没法在里面跑任务。`;
  if (/is not a folder|No such file/.test(message)) return "这个文件夹不存在了。";
  return message.replace(/^\w+Error: /, "");
}

export function App() {
  const [ready, setReady] = useState(false);
  const [saved, setSaved] = useState<Saved>({});
  const [home, setHome] = useState("");
  const [view, setView] = useState<Overview | null>(null);
  const [route, setRoute] = useState<Route>({ page: "overview" });
  const [job, setJob] = useState<Job | null>(null);
  const [welcomeError, setWelcomeError] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const [changes, setChanges] = useState(0);
  const jobRef = useRef<Job | null>(null);

  const updateJob = (change: (j: Job) => Job) => setJob((j) => (jobRef.current = j ? change(j) : j));

  const openProject = useCallback(async (path: string) => {
    setProject(path);
    try {
      const v = await op<Overview>("overview");
      setView(v); setRoute({ page: "overview" }); setWelcomeError("");
      setSaved(await store((s) => ({
        ...s, project: v.project,
        recent: [{ path: v.project, name: baseName(v.project), branch: v.branch, head: v.head, at: new Date().toISOString() },
          ...(s.recent || []).filter((r) => r.path !== v.project)].slice(0, 12),
      })));
      return true;
    } catch (e) {
      setProject(view?.project || "");
      if (view) showToast(explain((e as Error).message), { tone: "error" });
      else setWelcomeError(explain((e as Error).message));
      return false;
    }
  }, [view]);

  useEffect(() => {
    (async () => {
      const s = await loadSaved();
      setSaved(s);
      const found = await op<{ home: string; bound: string; bound_is_repo: boolean }>("projects").catch(() => null);
      setHome(found?.home || "");
      const start = s.project || (found?.bound_is_repo ? found.bound : "");
      if (start) {
        setProject(start);
        try { setView(await op<Overview>("overview")); } catch { setProject(""); }
      }
      setReady(true);
    })();
  }, []);

  const openFolder = useCallback(async () => {
    try {
      const got = await op<{ path?: string; cancelled?: boolean }>("pick_folder", view ? { start: view.project } : {});
      if (got.path) await openProject(got.path);
    } catch {
      setBrowsing(true);              // no system chooser here: the page's own folder browser
    }
  }, [view, openProject]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "o") { e.preventDefault(); openFolder(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openFolder]);

  const refresh = useCallback(async () => {
    try { setView(await op<Overview>("overview")); } catch (e) { showToast(explain((e as Error).message), { tone: "error" }); }
  }, []);

  const closeProject = () => { setView(null); setProject(""); setRoute({ page: "overview" }); store((s) => ({ ...s, project: "" })).then(setSaved); };
  const forget = (path: string) => store((s) => ({ ...s, recent: (s.recent || []).filter((r) => r.path !== path) })).then(setSaved);

  const onEvent = (e: any) => updateJob((j) => {
    const key = `${e.workflow || ""}|${e.stage || ""}`;
    const steps = [...j.steps];
    const open = steps.findIndex((s) => s.key === key && s.state === "run");
    const state: Step["state"] = e.error ? "fail" : e.event === "start" ? "run" : "done";
    if (open >= 0 && state !== "run") steps[open] = { ...steps[open], state, seconds: e.seconds };
    else if (!(e.stage || "").startsWith("run ")) steps.push({ key, label: e.stage || "", state, seconds: e.seconds });
    return { ...j, steps };
  });

  const start = async (task: Task, kind: Job["kind"], name: string, input: object) => {
    if (jobRef.current && !jobRef.current.done) { showToast("还有一个任务在跑，等它结束再开始", { tone: "warn" }); return; }
    const fresh: Job = { task: task.name, kind, title: task.title, started: Date.now(), steps: [] };
    jobRef.current = fresh; setJob(fresh);
    try {
      const result = await op(name, input, onEvent, (id) => updateJob((j) => ({ ...j, id })));
      updateJob((j) => ({ ...j, done: true, result }));
      await refresh();
      setChanges((n) => n + 1);
      showToast(`${task.title}：${result?.status === "FAIL" ? "有问题" : result?.status === "BLOCKED" ? "被挡住" : "通过"}`,
        { action: { label: "查看", run: () => setRoute({ page: "task", task: task.name }) } });
    } catch (e) {
      updateJob((j) => ({ ...j, done: true, error: explain((e as Error).message) }));
      showToast(`${task.title} 没有跑完：${explain((e as Error).message)}`, { tone: "error" });
    }
  };

  const ctx: Ctx = {
    saved, home, view, route, job, go: setRoute, refresh, openFolder, openProject, closeProject, forget,
    check: (task, inputs) => start(task, "check", "run_task", { task: task.name, inputs }),
    changes, touched: () => { setChanges((n) => n + 1); void refresh(); },
    stop: () => { if (jobRef.current?.id) cancel(jobRef.current.id).catch(() => {}); },
  };

  if (!ready) return <div className="shadcn-scale grid h-full place-items-center text-sm text-text-muted">正在打开…</div>;
  return (
    <AppContext.Provider value={ctx}>
      {view ? <Workspace /> : <Welcome error={welcomeError} onDismiss={() => setWelcomeError("")} />}
      <FolderDialog open={browsing} onOpenChange={setBrowsing} start={view?.project || home}
        onChoose={async (path) => { if (await openProject(path)) setBrowsing(false); }} />
    </AppContext.Provider>
  );
}
