"use client";

/**
 * Before a demonstration recording starts: what is recorded, the
 * microphone switch and input device, and Start. A missing system grant
 * is shown with a button that opens the right settings pane. The
 * composer shows it as a dialog; the Workflow Recording window shows the
 * same setup on its page.
 */
import { useEffect, useState } from "react";
import { Camera, EyeOff, Mic, MousePointerClick } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { SimpleSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useTranslation } from "@/lib/i18n";
import { startRecording, type RecordingStartError } from "@/lib/recording/recording-store";

const MIC_PREF = "openprogram.recording.mic";

function readMicPref(): boolean {
  try {
    return localStorage.getItem(MIC_PREF) !== "0";
  } catch {
    return true;
  }
}

/** The setup's state and Start. `onStarted` runs once the recording is live. */
export function useRecordSetup(composer: string, onStarted: () => void) {
  const { text } = useTranslation();
  const [mic, setMic] = useState(readMicPref);
  const [devices, setDevices] = useState<{ value: string; label: string }[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [engine, setEngine] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<RecordingStartError | null>(null);

  useEffect(() => {
    let live = true;
    fetch("/api/voice/status").then((r) => r.json())
      .then((status) => { if (live) setEngine(!!status?.resolved_engine); })
      .catch(() => { if (live) setEngine(null); });
    navigator.mediaDevices?.enumerateDevices?.().then((list) => {
      if (!live) return;
      const inputs = list.filter((d) => d.kind === "audioinput" && d.deviceId && d.deviceId !== "default");
      setDevices(inputs.map((d, i) => ({ value: d.deviceId, label: d.label || `${text("Microphone", "麦克风")} ${i + 1}` })));
    }).catch(() => {});
    return () => { live = false; };
  }, [text]);

  const narration = mic && engine !== false;

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      localStorage.setItem(MIC_PREF, mic ? "1" : "0");
    } catch {
      /* the choice is only a convenience */
    }
    const failed = await startRecording(composer, { mic: narration, deviceId: deviceId || undefined });
    setBusy(false);
    if (failed) setError(failed);
    else onStarted();
  };

  const openSettings = async () => {
    if (!error?.capability) return;
    await fetch(`/api/system/access/${encodeURIComponent(error.capability)}?open_settings=true`, { method: "POST" })
      .catch(() => {});
  };

  const rows = [
    {
      icon: <MousePointerClick className="size-4" />,
      title: text("Clicks, keys and typed text", "点击、按键和输入的文字"),
      detail: text("With the app, window and control each one went to.", "以及它们所在的应用、窗口和控件。"),
    },
    {
      icon: <Camera className="size-4" />,
      title: text("Screenshots", "截图"),
      detail: text("After clicks and Return, without OpenProgram's own windows.", "在点击和回车之后截取，不含 OpenProgram 自己的窗口。"),
    },
    {
      icon: <Mic className="size-4" />,
      title: text("Your narration", "你的讲解"),
      detail: text("Transcribed to text as you speak. The audio is not kept.", "边说边转成文字，不保存音频。"),
    },
    {
      icon: <EyeOff className="size-4" />,
      title: text("Never recorded", "不会记录"),
      detail: text("Passwords, password managers and anything in OpenProgram.", "密码、密码管理器，以及 OpenProgram 里的操作。"),
    },
  ];

  return { text, mic, setMic, devices, deviceId, setDeviceId, engine, busy, error, start, openSettings, rows };
}

/** What is recorded, the microphone choice and any start error. */
export function RecordSetupFields({ setup }: { setup: ReturnType<typeof useRecordSetup> }) {
  const { text, mic, setMic, devices, deviceId, setDeviceId, engine, busy, error, openSettings, rows } = setup;
  return (
    <>
      <ul className="flex flex-col gap-3 py-1">
        {rows.map((row) => (
          <li key={row.title} className="flex items-start gap-3">
            <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-text-muted">
              {row.icon}
            </span>
            <span className="min-w-0">
              <span className="block text-fs-base font-medium text-text-primary">{row.title}</span>
              <span className="block text-fs-xs text-text-muted">{row.detail}</span>
            </span>
          </li>
        ))}
      </ul>

      <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
        <label className="flex items-center justify-between gap-3 text-fs-base">
          <span>{text("Narrate with the microphone", "用麦克风讲解")}</span>
          <Switch checked={mic} onCheckedChange={setMic} disabled={busy} />
        </label>
        {mic && devices.length > 1 ? (
          <SimpleSelect
            className="max-w-none"
            value={deviceId}
            onValueChange={setDeviceId}
            aria-label={text("Microphone", "麦克风")}
            options={[{ value: "", label: text("System default", "系统默认") }, ...devices]}
          />
        ) : null}
        {mic && engine === false ? (
          <p className="text-fs-xs text-text-muted">
            {text(
              "No speech engine is set up, so narration is off. Choose one in Settings → Voice.",
              "还没有可用的语音识别服务，讲解不会录制。请在 设置 → 语音 里选择。",
            )}
          </p>
        ) : null}
      </div>

      {error ? (
        <div className="flex items-start justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-fs-xs text-destructive">
          <span>{error.message}</span>
          {error.capability ? (
            <Button type="button" variant="elevated" onClick={openSettings}>
              {text("Open Settings", "打开设置")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

export function RecordDialog({ composer, onClose }: { composer: string; onClose(): void }) {
  const setup = useRecordSetup(composer, onClose);
  const { text, busy, start } = setup;
  return (
    <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>{text("Record a workflow", "录制 Workflow")}</DialogTitle>
          <DialogDescription>
            {text(
              "Do the task once, the way you always do it. The agent turns the recording into a workflow and asks about anything unclear.",
              "按平时的做法把这件事做一遍。agent 会把录制做成 workflow，不清楚的地方会先问你。",
            )}
          </DialogDescription>
        </DialogHeader>

        <RecordSetupFields setup={setup} />

        <DialogFooter>
          <Button type="button" variant="elevated" disabled={busy} onClick={onClose}>
            {text("Cancel", "取消")}
          </Button>
          <Button type="button" disabled={busy} onClick={start}>
            {busy ? text("Starting…", "正在开始…") : text("Start recording", "开始录制")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
