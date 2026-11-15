"use client";

/**
 * Voice — which speech-to-text engine the composer microphone uses.
 *
 * Engine, language and model are the `voice.*` settings (state.settings);
 * the status card shows what `auto` resolves to and which engines work
 * on this computer (/api/voice/status). The microphone itself has no
 * options of its own: everything about it is set here.
 */
import { platformKeys, useIsMacPlatform } from "@/lib/shortcut-keys";
import { jsonFetch } from "@/lib/net/fetch-client";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";
import { SPOKEN_LANGUAGES, type SpokenLanguage } from "@/lib/voice/spoken-languages";
import { SettingsDropdown, type DropdownOption } from "./general-section";
import { SettingsPage, SettingsRow, SettingsSection } from "../settings-ui";

type Engine = "auto" | "openai" | "groq" | "local";
type Language = "auto" | SpokenLanguage;

interface EngineRow {
  id: Exclude<Engine, "auto">;
  available: boolean;
  detail: string;
}

interface VoiceStatus {
  engine: Engine;
  language: Language;
  model: string;
  resolved_engine: Exclude<Engine, "auto"> | null;
  resolved_model: string;
  engines: EngineRow[];
  default_models: Record<Exclude<Engine, "auto">, string>;
}

export function VoiceSettings() {
  const { text } = useTranslation();
  const mac = useIsMacPlatform();
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [model, setModel] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/voice/status");
      if (!response.ok) throw new Error(String(response.status));
      const next: VoiceStatus = await response.json();
      setStatus(next);
      setModel(next.model);
    } catch (error) {
      setMessage(text(`Could not load voice settings: ${error}`, `无法加载语音设置：${error}`));
    }
  }, [text]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(key: string, value: string) {
    setMessage("");
    try {
      const result = await jsonFetch<{ error?: string }>("/api/settings", {
        method: "POST",
        body: JSON.stringify({ key, value }),
      });
      if (result.error) throw new Error(result.error);
      await load();
    } catch (error) {
      const reason = (error as Error).message;
      setMessage(text(`Save failed: ${reason}`, `保存失败：${reason}`));
    }
  }

  const engineName = (id: Engine) => ({
    auto: text("Auto", "自动"),
    openai: "OpenAI",
    groq: "Groq",
    local: text("Local whisper.cpp", "本机 whisper.cpp"),
  })[id];

  const engineOptions: DropdownOption<Engine>[] = (["auto", "openai", "groq", "local"] as Engine[])
    .map((id) => ({ value: id, label: engineName(id) }));
  const languageOptions: DropdownOption<Language>[] = [
    { value: "auto", label: text("Detect automatically", "自动识别") },
    ...SPOKEN_LANGUAGES.map(({ code, name }) => ({ value: code, label: name })),
  ];

  const detail = (row: EngineRow) => {
    if (row.id === "local") {
      if (!row.available) return text("whisper.cpp is not installed — run `brew install whisper-cpp`.", "未安装 whisper.cpp，运行 `brew install whisper-cpp` 安装。");
      return row.detail === "model ready"
        ? text("Installed, model downloaded.", "已安装，模型已下载。")
        : text("Installed; the model downloads on first use.", "已安装，第一次使用时下载模型。");
    }
    return row.available
      ? text("API key set.", "已设置 API key。")
      : text("No API key — add one in Settings → Providers.", "没有 API key，请在 设置 → 模型服务 里添加。");
  };

  return (
    <SettingsPage
      title={text("Voice", "语音")}
      description={text(
        `Speak into the microphone in the bottom-left corner of the input box, or hold ${platformKeys("⌥", mac)}Space while typing. Each phrase is transcribed when you pause and lands in the box for you to edit and send.`,
        `点输入框左下角的麦克风说话，或在输入时按住 ${platformKeys("⌥", mac)}空格。每说完一句停顿一下就会转写，文字落进输入框，改完再发。`,
      )}
    >
      {status ? <>
      <SettingsSection title={text("Speech to text", "语音转文字")}>
        <SettingsRow
          label={text("Engine", "识别服务")}
          description={status.resolved_engine
            ? text(
              `In use: ${engineName(status.resolved_engine)} · ${status.resolved_model}`,
              `当前使用：${engineName(status.resolved_engine)} · ${status.resolved_model}`,
            )
            : text("No engine is usable yet.", "还没有可用的识别服务。")}
        >
          <SettingsDropdown
            label={text("Engine", "识别服务")}
            value={status.engine}
            options={engineOptions}
            onChange={(value) => void save("voice.engine", value)}
          />
        </SettingsRow>
        <SettingsRow label={text("Spoken language", "说话语言")}>
          <SettingsDropdown
            label={text("Spoken language", "说话语言")}
            value={status.language}
            options={languageOptions}
            onChange={(value) => void save("voice.language", value)}
          />
        </SettingsRow>
        {status.engine !== "auto" ? (
          <SettingsRow
            label={text("Model", "模型")}
            description={text(
              `Empty uses ${status.default_models[status.engine]}.`,
              `留空使用 ${status.default_models[status.engine]}。`,
            )}
          >
            <Input
              className="font-mono"
              spellCheck={false}
              placeholder={status.default_models[status.engine]}
              value={model}
              onChange={(event) => setModel(event.target.value)}
              onBlur={() => {
                if (model.trim() !== status.model) void save("voice.model", model.trim());
              }}
            />
          </SettingsRow>
        ) : null}
      </SettingsSection>

      <SettingsSection title={text("Engines on this computer", "这台电脑上的识别服务")}>
        {status.engines.map((row) => (
          <SettingsRow key={row.id} label={engineName(row.id)} description={detail(row)}>
            <Badge variant={row.available ? "default" : "secondary"}>
              {row.available ? text("Ready", "可用") : text("Not set up", "未配置")}
            </Badge>
          </SettingsRow>
        ))}
      </SettingsSection>
      </> : null}

      {message ? <p className="text-fs-xs text-danger-soft">{message}</p> : null}
    </SettingsPage>
  );
}
