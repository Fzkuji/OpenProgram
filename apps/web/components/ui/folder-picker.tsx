"use client";

import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  requestNativeFolder,
  validateManualFolder,
} from "@/lib/folder-picker";
import { useTranslation } from "@/lib/i18n";

interface PendingManualPath {
  resolve(path: string | null): void;
}

/** Native folder selection with a server-path fallback for headless hosts. */
export function useFolderPicker(): {
  pickFolder(start?: string): Promise<string | null>;
  folderPickerDialog: ReactNode;
  manualOpen: boolean;
} {
  const { text } = useTranslation();
  const pending = useRef<PendingManualPath | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualPath, setManualPath] = useState("");
  const [manualError, setManualError] = useState<string | null>(null);
  const [validating, setValidating] = useState(false);

  const finishManual = useCallback((path: string | null) => {
    const request = pending.current;
    pending.current = null;
    setManualOpen(false);
    setManualError(null);
    setValidating(false);
    request?.resolve(path);
  }, []);

  useEffect(() => () => {
    pending.current?.resolve(null);
    pending.current = null;
  }, []);

  const requestManual = useCallback((start: string) => {
    pending.current?.resolve(null);
    return new Promise<string | null>((resolve) => {
      pending.current = { resolve };
      setManualPath(start);
      setManualError(null);
      setManualOpen(true);
    });
  }, []);

  const pickFolder = useCallback(async (start = "") => {
    const result = await requestNativeFolder(start);
    if (result.path) return result.path;
    if (!result.unsupported) return null;
    return requestManual(start);
  }, [requestManual]);

  async function submitManual(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const candidate = manualPath.trim();
    if (!candidate) {
      setManualError(text("Enter a folder path.", "请输入文件夹路径。"));
      return;
    }
    setValidating(true);
    setManualError(null);
    try {
      finishManual(await validateManualFolder(candidate));
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setManualError(
        text(`This folder is not available: ${detail}`, `此文件夹不可用：${detail}`),
      );
      setValidating(false);
    }
  }

  const folderPickerDialog = (
    <Dialog
      open={manualOpen}
      onOpenChange={(open) => {
        if (!open && !validating) finishManual(null);
      }}
    >
      <DialogContent className="sm:max-w-[560px]">
        <form className="grid gap-4" onSubmit={submitManual}>
          <DialogHeader>
            <DialogTitle>{text("Open folder on server", "打开服务器上的文件夹")}</DialogTitle>
            <DialogDescription>
              {text(
                "This OpenProgram worker has no graphical folder picker. Enter an absolute path on the machine running the worker.",
                "这个 OpenProgram worker 没有图形文件夹选择器。请输入运行 worker 的机器上的绝对路径。",
              )}
            </DialogDescription>
          </DialogHeader>
          <label className="grid gap-2 text-sm font-medium">
            {text("Absolute folder path", "文件夹绝对路径")}
            <input
              autoFocus
              value={manualPath}
              onChange={(event) => setManualPath(event.target.value)}
              placeholder={text("/home/user/project", "/home/user/project")}
              className="h-10 w-full rounded-md border border-border bg-background px-3 font-mono text-sm text-foreground outline-none focus:ring-2 focus:ring-ring"
              aria-invalid={manualError ? true : undefined}
            />
          </label>
          {manualError ? (
            <p className="text-sm text-destructive" role="alert">{manualError}</p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="ghost" disabled={validating} onClick={() => finishManual(null)}>
              {text("Cancel", "取消")}
            </Button>
            <Button type="submit" disabled={validating || !manualPath.trim()}>
              {validating ? text("Checking…", "正在检查…") : text("Open folder", "打开文件夹")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );

  return { pickFolder, folderPickerDialog, manualOpen };
}
