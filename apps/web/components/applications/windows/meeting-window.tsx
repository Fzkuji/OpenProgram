"use client";

/**
 * The Meetings window: record a call on this computer (the microphone and
 * the computer's sound), watch the transcript arrive, and read past
 * meetings' summaries and transcripts. Recording only ever starts from the
 * Start button; the first Start explains the two macOS permissions first.
 * The recorder lives in lib/meetings/meeting-recorder, so leaving this tab
 * does not stop a recording.
 */
import { useCallback, useEffect, useState } from "react";
import { FileText, MessageCircle, Mic, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { SimpleSelect } from "@/components/ui/select";
import { ManageEmptyState, ManageIconButton, ManagePage, ManagePageHeader, ManageRow, ManageSummary, managePageStyles as shared } from "@/components/ui/manage-page";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Markdown } from "@/lib/format-utils/markdown";
import { isPermanent, retryDelay } from "@/lib/abilities/kept-read";
import { useTranslation } from "@/lib/i18n";
import { useApplicationList } from "@/lib/net/application-list";
import { useChanged } from "@/lib/net/ws/changes";
import { SECONDS, useNow } from "@/lib/clock/use-now";
import { pushPath } from "@/lib/nav/shallow-nav";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import {
  captureErrorMessage, chunkProgress, clock, languageLabel, recordingNote, speakerLabel, stateLabel, systemAudioOf,
  type MeetingList, type MeetingStatus, type TranscriptLine,
} from "@/lib/meetings/meeting-format";
import { useMeetingRecorder } from "@/lib/meetings/meeting-recorder";
import { SPOKEN_LANGUAGES } from "@/lib/voice/spoken-languages";
import styles from "./windows.module.css";

export const MEETING_APPLICATION = "meeting";
const EXPLAINED = "openprogram.meetings.permissionsExplained";
/** The spoken language the last meeting was started with ("" = detect), per viewer. */
const LAST_LANGUAGE = "openprogram.meetings.language";

function explained(): boolean {
  try { return localStorage.getItem(EXPLAINED) === "1"; } catch { return false; }
}

function lastLanguage(): string {
  try {
    const stored = localStorage.getItem(LAST_LANGUAGE) ?? "";
    return SPOKEN_LANGUAGES.some(language => language.code === stored) ? stored : "";
  } catch { return ""; }
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    // The status travels with the error: a 4xx is the server's final answer.
    throw Object.assign(new Error(String((body as { error?: string }).error || `HTTP ${response.status}`)), { status: response.status });
  }
  return body as T;
}

/**
 * Read until an answer comes: a failure warns and is asked again after a
 * growing pause; a 4xx is final and goes to `refused` instead. Returns a
 * function that stops it (the caller's effect cleanup).
 */
export function readQuietly<T>(what: string, read: () => Promise<T>, answer: (value: T) => void, refused: () => void): () => void {
  let live = true;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const attempt = (n: number) => {
    read().then(value => { if (live) answer(value); }, (reason: unknown) => {
      if (!live) return;
      if (isPermanent(reason)) { console.warn(`${what} refused`, reason); refused(); return; }
      console.warn(`${what} failed; reading again`, reason);
      timer = setTimeout(() => attempt(n + 1), retryDelay(n));
    });
  };
  attempt(0);
  return () => { live = false; if (timer) clearTimeout(timer); };
}

/** The meeting list as last read, so the window opened again shows it at once. */
let meetingsRead: MeetingList | null = null;

export function MeetingWindow() {
  const { text } = useTranslation();
  const { builtin } = useApplicationList();
  const enabled = builtin.find(item => item.id === MEETING_APPLICATION)?.enabled !== false;
  const recorder = useMeetingRecorder();
  const [list, setList] = useState<MeetingList | null>(meetingsRead);
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState("");
  const [asking, setAsking] = useState(false);
  const [live, setLive] = useState<MeetingStatus | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<MeetingStatus | null>(null);
  const recording = recorder.phase === "recording" || recorder.phase === "stopping";
  // The live clock counts whole seconds from the start (`startedAt` is on
  // the page's performance clock).
  const startedAt = recorder.active ? performance.timeOrigin + recorder.active.startedAt : 0;
  const now = useNow(recording && recorder.active ? SECONDS : null, startedAt);

  // A read that fails keeps the list on screen and is asked again, quietly;
  // a 4xx is final and is not asked again.
  const keepList = useCallback((read: MeetingList) => { meetingsRead = read; setList(read); }, []);
  const refresh = useCallback(async () => {
    try {
      keepList(await getJson<MeetingList>("/api/meetings"));
    } catch (reason) {
      console.warn("meeting list read failed", reason);
    }
  }, [keepList]);
  useEffect(() => {
    if (!enabled) return;
    return readQuietly("meeting list read", () => getJson<MeetingList>("/api/meetings"), keepList, () => {});
  }, [enabled, keepList]);
  useEffect(() => { setLanguage(lastLanguage()); }, []);

  // A meeting's state or counts changed (a chunk arrived or was
  // transcribed, the summary finished): the Worker says so.
  useChanged("meetings", () => {
    if (!enabled) return;
    const id = useMeetingRecorder.getState().active?.id;
    return Promise.all([
      refresh(),
      id ? getJson<MeetingStatus>(`/api/meetings/${id}`).then(setLive, () => {}) : undefined,
    ]);
  });
  useEffect(() => { if (!recording) setLive(null); }, [recording]);
  const activeId = useMeetingRecorder(state => state.active?.id);
  useEffect(() => {
    if (activeId) getJson<MeetingStatus>(`/api/meetings/${activeId}`).then(setLive, () => {});
  }, [activeId]);

  async function start() {
    try {
      localStorage.setItem(EXPLAINED, "1");
      localStorage.setItem(LAST_LANGUAGE, language);
    } catch { /* per-viewer convenience only */ }
    setAsking(false);
    const ok = await useMeetingRecorder.getState().start(title.trim(), language);
    if (ok) { setTitle(""); void refresh(); }
  }

  async function stop() {
    const status = await useMeetingRecorder.getState().stop();
    if (status) setSelected(status.id);
    void refresh();
  }

  function openConversation(row: MeetingStatus) {
    useCenterTabs.getState().openSessionTab(row.session_id, row.title);
    pushPath(`/s/${row.session_id}`);
  }

  async function remove(row: MeetingStatus) {
    setDeleting(null);
    await fetch(`/api/meetings/${row.id}`, { method: "DELETE" }).catch(() => {});
    if (selected === row.id) setSelected(null);
    void refresh();
  }

  async function summarizeAgain(row: MeetingStatus) {
    await fetch(`/api/meetings/${row.id}/summarize`, { method: "POST" }).catch(() => {});
    void refresh();
  }

  const error = recorder.error;
  const errorText = !error ? "" : error.kind === "capture"
    ? (error.name === "NotSupportedError"
      ? text("This window cannot record audio. Use the OpenProgram desktop app.", "这个窗口无法录音。请使用 OpenProgram 桌面 App。")
      : captureErrorMessage(error.name, text))
    : error.message;
  const meetings = list?.meetings ?? [];
  const chosen = meetings.find(row => row.id === selected) ?? null;

  return <ManagePage>
    <ManagePageHeader
      title={text("Meetings", "会议")}
      toolbar={list ? <ManageSummary>{text(`${meetings.length} meetings`, `${meetings.length} 场会议`)}</ManageSummary> : null}
    />
    {errorText && <div role="alert" className={shared.errorBar}>{errorText}</div>}
    <div className={shared.body}>
      <div className="mx-auto flex w-full max-w-[760px] flex-col gap-6">
        {!enabled ? (
          <ManageEmptyState compact icon={<Mic size={20} />} title={text("Meetings is turned off", "会议已关闭")} description={text("Turn it on on the Applications page.", "请在应用页打开它。")} />
        ) : <>
          {list && !list.ffmpeg && <p className={styles.notice}>{list.ffmpeg_missing}</p>}
          {list && !list.speech && <p className={styles.notice}>{text(
            "No speech engine is set up, so recordings cannot be transcribed. Add an OpenAI or Groq key in Settings → Model services, or install whisper.cpp; Settings → Voice chooses the engine.",
            "还没有可用的语音识别引擎，录音无法转写。请在“设置 → 模型服务”添加 OpenAI 或 Groq 的 key，或安装 whisper.cpp；引擎在“设置 → 语音”中选择。",
          )}</p>}

          {recording && recorder.active ? (
            <section className={styles.recordCard} aria-label={text("Recording", "录制中")}>
              <div className="flex items-center gap-[10px] min-w-0">
                <span className={styles.liveDot} aria-hidden />
                <strong className={styles.liveClock}>{clock(now - startedAt)}</strong>
                <span className={styles.rowDir}>{recorder.active.title}</span>
                <span className={styles.rowDir}>{languageLabel(live?.language ?? recorder.active.language, text)}</span>
              </div>
              <p className={styles.lead}>
                {recordingNote(live ? systemAudioOf(live) : recorder.active.systemAudio, text)}
                {" "}
                {live ? (() => {
                  const progress = chunkProgress(live);
                  return text(
                    `${progress.received} chunks uploaded, ${progress.processed} transcribed, ${live.lines ?? 0} transcript lines.`,
                    `已上传 ${progress.received} 段，已转写 ${progress.processed} 段，转写稿 ${live.lines ?? 0} 行。`,
                  );
                })() : text(`${recorder.uploaded} chunks uploaded.`, `已上传 ${recorder.uploaded} 段。`)}
                {recorder.uploadFailures > 0 && " " + text(`${recorder.uploadFailures} chunks could not be uploaded.`, `${recorder.uploadFailures} 段上传失败。`)}
              </p>
              <div className="flex justify-end">
                <Button type="button" variant="destructive" disabled={recorder.phase === "stopping"} onClick={() => void stop()}>
                  {recorder.phase === "stopping" ? text("Stopping…", "正在停止…") : text("Stop recording", "停止录制")}
                </Button>
              </div>
            </section>
          ) : (
            <section className={styles.recordCard} aria-label={text("New meeting", "新会议")}>
              <p className={styles.lead}>{text(
                "Start when your call begins. OpenProgram records your microphone as “Me” and the computer's sound as “Others”, transcribes both as you talk, and when you stop, writes a summary with decisions and action items in a new chat. Headphones give the cleanest transcript.",
                "通话开始时点开始。OpenProgram 把你的麦克风记为“我”，电脑声音记为“对方”，边说边转写；停止后会在一个新对话里写出摘要、决定和待办。戴耳机转写最干净。",
              )}</p>
              <div className="flex items-center gap-2">
                <Input className="min-w-0 flex-1" value={title} onChange={event => setTitle(event.target.value)} placeholder={text("Meeting name (optional)", "会议名称（可选）")} aria-label={text("Meeting name", "会议名称")} maxLength={160} />
                <SimpleSelect
                  className="w-auto shrink-0"
                  label={text("Spoken language", "说话语言")}
                  value={language}
                  onValueChange={setLanguage}
                  options={[
                    { value: "", label: text("Detect automatically", "自动识别") },
                    ...SPOKEN_LANGUAGES.map(({ code, name }) => ({ value: code, label: name })),
                  ]}
                />
              </div>
              <div className="flex justify-end">
                <Button type="button" disabled={recorder.phase !== "idle" || !list?.ffmpeg} onClick={() => explained() ? void start() : setAsking(true)}>
                  {recorder.phase === "starting" ? text("Starting…", "正在开始…") : text("Start recording", "开始录制")}
                </Button>
              </div>
            </section>
          )}

          <section aria-label={text("Past meetings", "过往会议")}>
            <h2 className={styles.heading}>{text("Past meetings", "过往会议")}</h2>
            {!list ? null : meetings.length === 0 ? (
              <ManageEmptyState compact icon={<Mic size={20} />} title={text("No meetings yet", "还没有会议")} description={text("Recorded meetings appear here with their transcript and summary.", "录过的会议会连同转写稿和摘要显示在这里。")} />
            ) : (
              <div>
                {meetings.map(row => <ManageRow
                  key={row.id}
                  icon={<Mic size={16} />}
                  name={row.title}
                  meta={row.state !== "done" ? <Badge variant="secondary">{stateLabel(row.state, text)}</Badge> : undefined}
                  description={`${new Date(row.started_at * 1000).toLocaleString()} · ${clock(row.duration_ms)} · ${languageLabel(row.language, text)}`}
                  onClick={() => setSelected(selected === row.id ? null : row.id)}
                  actions={<>
                    {row.session_id && <ManageIconButton label={text("Open summary chat", "打开总结对话")} onClick={() => openConversation(row)}><MessageCircle size={16} /></ManageIconButton>}
                    <ManageIconButton label={text("Delete meeting", "删除会议")} danger disabled={row.state === "recording"} onClick={() => setDeleting(row)}><Trash2 size={16} /></ManageIconButton>
                  </>}
                />)}
              </div>
            )}
          </section>

          {chosen && <MeetingDetail key={chosen.id} meeting={chosen} onSummarize={() => void summarizeAgain(chosen)} onOpenConversation={() => openConversation(chosen)} />}
        </>}
      </div>
    </div>
    {asking && <ConfirmDialog
      title={text("Before the first recording", "第一次录制之前")}
      description={text(
        "macOS will ask OpenProgram for two permissions. Screen & System Audio Recording lets it record the call's sound that this computer plays (nothing on the screen is recorded); Microphone records you. If you decline, allow OpenProgram later in System Settings → Privacy & Security, then reopen the App. Recording runs until you press Stop.",
        "macOS 会向 OpenProgram 询问两项权限。“录屏与系统录音”让它录下这台电脑播放的通话声音（不会录屏幕画面）；“麦克风”用来录你的声音。如果拒绝了，之后可在“系统设置 → 隐私与安全性”中允许 OpenProgram，然后重新打开 App。录制会一直进行，直到你点停止。",
      )}
      confirmLabel={text("Start recording", "开始录制")}
      onConfirm={() => void start()}
      onCancel={() => setAsking(false)}
    />}
    {deleting && <ConfirmDialog
      destructive
      title={text("Delete this meeting?", "删除这场会议？")}
      description={text("Its audio, transcript and summary are deleted. The summary chat stays.", "会删除它的录音、转写稿和摘要。总结对话会保留。")}
      confirmLabel={text("Delete", "删除")}
      onConfirm={() => void remove(deleting)}
      onCancel={() => setDeleting(null)}
    />}
  </ManagePage>;
}

/** One meeting's summary and transcript. */
function MeetingDetail({ meeting, onSummarize, onOpenConversation }: {
  meeting: MeetingStatus; onSummarize: () => void; onOpenConversation: () => void;
}) {
  const { text } = useTranslation();
  const [lines, setLines] = useState<TranscriptLine[] | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  const finished = meeting.state === "done" || meeting.state === "interrupted";

  useEffect(() => {
    // A read that fails is asked again, quietly, with backoff; its tab stays
    // as it was meanwhile. A 4xx is final: the meeting has no such text.
    const stops = [readQuietly(
      "meeting transcript read",
      () => getJson<{ lines: TranscriptLine[] }>(`/api/meetings/${meeting.id}/transcript`),
      body => setLines(body.lines),
      () => setLines([]),
    )];
    if (meeting.has_summary) {
      stops.push(readQuietly(
        "meeting summary read",
        () => getJson<{ markdown: string }>(`/api/meetings/${meeting.id}/summary`),
        // The transcript tab shows the transcript; the file's <details> copy is for readers of summary.md.
        body => setSummary(body.markdown.split("\n<details>")[0]),
        () => setSummary(""),
      ));
    } else setSummary("");
    return () => { for (const stop of stops) stop(); };
  }, [meeting.id, meeting.has_summary, meeting.state, meeting.transcribed]);

  return <section className={styles.recordCard} aria-label={meeting.title}>
    <Tabs defaultValue={meeting.has_summary ? "summary" : "transcript"}>
      <div className="flex items-center justify-between gap-3">
        <TabsList variant="line" aria-label={text("Meeting details", "会议详情")}>
          <TabsTrigger value="summary">{text("Summary", "摘要")}</TabsTrigger>
          <TabsTrigger value="transcript">{text("Transcript", "转写稿")}</TabsTrigger>
        </TabsList>
        <div className="flex gap-2">
          {meeting.session_id && <Button type="button" variant="ghost" size="sm" onClick={onOpenConversation}><MessageCircle size={14} aria-hidden />{text("Open chat", "打开对话")}</Button>}
          {finished && <Button type="button" variant="ghost" size="sm" onClick={onSummarize}><FileText size={14} aria-hidden />{text("Summarize again", "重新总结")}</Button>}
        </div>
      </div>
      <TabsContent value="summary">
        {summary === null ? null
          : summary ? <div className={styles.meetingSummary}><Markdown source={summary} escapeRawHtml /></div>
            : <p className={styles.lead}>{meeting.state === "transcribing" || meeting.state === "summarizing"
              ? text("The summary is being written.", "摘要正在生成。")
              : meeting.summary_error || text("No summary yet.", "还没有摘要。")}</p>}
      </TabsContent>
      <TabsContent value="transcript">
        {lines === null ? null
          : lines.length === 0 ? <p className={styles.lead}>{text("Nothing was transcribed.", "没有转写出内容。")}</p>
            : <ol className={styles.transcript}>
              {lines.map((line, index) => <li key={index} className={styles.transcriptLine} data-speaker={line.speaker.split(":")[0]}>
                <span className={styles.transcriptTime}>{clock(line.start_ms)}</span>
                <span className={styles.transcriptSpeaker}>{speakerLabel(line.speaker, text)}</span>
                <span className={styles.transcriptText}>{line.text}</span>
              </li>)}
            </ol>}
        {meeting.system_audio === "unavailable" && <p className={styles.notice}>{text(
          "The computer's sound was not recorded in this meeting; the other side was heard only through your microphone.",
          "这场会议没有录到电脑声音；对方的声音只通过你的麦克风录到。",
        )}</p>}
        {meeting.failed > 0 && <p className={styles.notice}>{text(
          `${meeting.failed} chunks could not be transcribed: ${meeting.errors.at(-1) ?? ""}`,
          `${meeting.failed} 段无法转写：${meeting.errors.at(-1) ?? ""}`,
        )}</p>}
      </TabsContent>
    </Tabs>
  </section>;
}
