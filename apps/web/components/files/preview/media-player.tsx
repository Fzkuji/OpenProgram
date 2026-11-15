"use client";

/** Audio and video with the App's own controls. The browser's built-in
 *  controls lay a black gradient over the picture whatever the theme;
 *  these sit on a small bar in the theme's surface colour, built from the
 *  shared Button and Slider. On a video the bar floats on the picture and
 *  shows only while the pointer (or focus) is on it; an audio file has
 *  nothing else to show, so its bar is always there. */
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState, type CSSProperties } from "react";
import { Maximize, Minimize, Pause, Play, Volume2, VolumeX } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import styles from "./media-player.module.css";

function clock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const whole = Math.floor(seconds);
  const h = Math.floor(whole / 3600), m = Math.floor((whole % 3600) / 60), s = whole % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export const MediaPlayer = forwardRef<HTMLMediaElement | null, {
  kind: "audio" | "video"; src: string; label: string; className?: string; style?: CSSProperties; onError?: () => void;
}>(function MediaPlayer({ kind, src, label, className, style, onError }, forwarded) {
  const { text } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement & HTMLAudioElement>(null);
  useImperativeHandle(forwarded, () => video.current as HTMLMediaElement, []);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [muted, setMuted] = useState(false);
  const [full, setFull] = useState(false);
  const [seeking, setSeeking] = useState<number | null>(null);

  useEffect(() => {
    const change = () => setFull(document.fullscreenElement === host.current);
    document.addEventListener("fullscreenchange", change);
    return () => document.removeEventListener("fullscreenchange", change);
  }, []);

  const toggle = useCallback(() => {
    const element = video.current;
    if (!element) return;
    if (element.paused || element.ended) void element.play().catch(() => undefined);
    else element.pause();
  }, []);

  const shown = seeking ?? time;
  return <div ref={host} className={[styles.player, "relative block min-w-0", className].filter(Boolean).join(" ")} data-kind={kind} data-playing={playing ? "" : undefined} data-full={full ? "" : undefined}>
    {kind === "video"
      ? <video
          ref={video}
          src={src}
          preload="metadata"
          playsInline
          aria-label={label}
          style={style}
          onClick={toggle}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onTimeUpdate={event => setTime(event.currentTarget.currentTime)}
          onLoadedMetadata={event => setDuration(event.currentTarget.duration)}
          onDurationChange={event => setDuration(event.currentTarget.duration)}
          onVolumeChange={event => setMuted(event.currentTarget.muted)}
          onError={onError}
      />
      : <audio
          ref={video}
          src={src}
          preload="metadata"
          aria-label={label}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onTimeUpdate={event => setTime(event.currentTarget.currentTime)}
          onLoadedMetadata={event => setDuration(event.currentTarget.duration)}
          onDurationChange={event => setDuration(event.currentTarget.duration)}
          onVolumeChange={event => setMuted(event.currentTarget.muted)}
          onError={onError}
      />}
    <div className={styles.bar}>
      <Button type="button" variant="ghost" size="icon-sm" onClick={toggle} aria-label={playing ? text("Pause", "暂停") : text("Play", "播放")}>
        {playing ? <Pause aria-hidden /> : <Play aria-hidden />}
      </Button>
      <span className={styles.time}>{clock(shown)} / {clock(duration)}</span>
      <Slider
        className="mx-[6px] min-w-[48px] flex-1"
        min={0}
        max={duration > 0 ? duration : 1}
        step={0.1}
        value={[Math.min(shown, duration || 0)]}
        onValueChange={([value]) => setSeeking(value)}
        onValueCommit={([value]) => { if (video.current) video.current.currentTime = value; setTime(value); setSeeking(null); }}
        aria-label={text("Seek", "播放进度")}
      />
      <Button type="button" variant="ghost" size="icon-sm" onClick={() => { if (video.current) video.current.muted = !video.current.muted; }} aria-label={muted ? text("Unmute", "取消静音") : text("Mute", "静音")}>
        {muted ? <VolumeX aria-hidden /> : <Volume2 aria-hidden />}
      </Button>
      {kind === "video" && <Button type="button" variant="ghost" size="icon-sm" onClick={() => {
        if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
        else void host.current?.requestFullscreen().catch(() => undefined);
      }} aria-label={full ? text("Exit full screen", "退出全屏") : text("Full screen", "全屏")}>
        {full ? <Minimize aria-hidden /> : <Maximize aria-hidden />}
      </Button>}
    </div>
  </div>;
});
