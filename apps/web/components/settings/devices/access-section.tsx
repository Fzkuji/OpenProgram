"use client";

/**
 * Open this computer from: Same Wi-Fi (a listener on the local-network
 * address) and Anywhere (Tailscale built into OpenProgram, through a
 * public https address). Both apply at once. Reaching an address is not
 * enough to sign in: every device is added with a one-time code first.
 *
 * Route: ``/api/auth/remote-access``.
 */
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useTranslation } from "@/lib/i18n";
import { jsonFetch } from "@/lib/net/fetch-client";
import { SettingsRow, SettingsSection } from "../settings-ui";

export interface Lan {
  on: boolean;
  address: string;
}

export interface Anywhere {
  on: boolean;
  state: "off" | "starting" | "needs_login" | "needs_funnel" | "running" | "error";
  address: string | null;
  login_url: string | null;
  enable_url: string | null;
  message: string | null;
}

export interface Ways {
  lan: Lan;
  anywhere?: Anywhere;
}

/** The ways in, read now and again every few seconds while Anywhere is still starting or waiting on a sign-in. */
export function useWays(): [Ways | null, (change: Partial<Record<"lan" | "anywhere", boolean>>) => Promise<string>] {
  const [ways, setWays] = useState<Ways | null>(null);
  const read = useCallback(() => jsonFetch<Ways>("/api/auth/remote-access").then(setWays).catch(() => {}), []);
  useEffect(() => {
    void read();
  }, [read]);
  const waiting = !!ways?.anywhere?.on && ways.anywhere.state !== "running" && ways.anywhere.state !== "error";
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(() => void read(), 2000);
    return () => window.clearInterval(timer);
  }, [waiting, read]);
  const change = useCallback(async (body: Partial<Record<"lan" | "anywhere", boolean>>) => {
    try {
      setWays(await jsonFetch<Ways>("/api/auth/remote-access", { method: "PUT", body: JSON.stringify(body) }));
      return "";
    } catch (error) {
      return (error as Error).message;
    }
  }, []);
  return [ways, change];
}

export function host(address: string): string {
  try {
    return new URL(address).host;
  } catch {
    return address;
  }
}

export function AccessSection({ ways, change }: { ways: Ways | null; change: ReturnType<typeof useWays>[1] }) {
  const { text } = useTranslation();
  const [problem, setProblem] = useState("");
  const lan = ways?.lan;
  const anywhere = ways?.anywhere;

  async function flip(way: "lan" | "anywhere", on: boolean) {
    setProblem(await change({ [way]: on }));
  }

  let anywhereText = text(
    "Away from home too, through Tailscale built into OpenProgram. Nothing to install on your phone.",
    "离开家也能用，通过 OpenProgram 内置的 Tailscale。手机上什么都不用装。",
  );
  let anywhereAction = null;
  if (anywhere?.on) {
    if (anywhere.state === "running" && anywhere.address) {
      anywhereText = text(`At ${host(anywhere.address)}`, `地址：${host(anywhere.address)}`);
    } else if (anywhere.state === "needs_login" && anywhere.login_url) {
      anywhereText = text("Sign in to Tailscale once to finish. It's free.", "登录一次 Tailscale 即可完成，免费。");
      anywhereAction = (
        <Button onClick={() => window.open(anywhere.login_url!, "_blank", "noopener")}>
          {text("Sign in", "登录")}
        </Button>
      );
    } else if (anywhere.state === "needs_funnel" && anywhere.enable_url) {
      anywhereText = text("One more click in Tailscale lets your phone reach it.", "在 Tailscale 里再点一下，手机就能连上。");
      anywhereAction = (
        <Button onClick={() => window.open(anywhere.enable_url!, "_blank", "noopener")}>
          {text("Allow", "允许")}
        </Button>
      );
    } else if (anywhere.state === "error") {
      anywhereText = anywhere.message || anywhereText;
    }
  }

  return (
    <SettingsSection title={text("Open this computer from", "从哪里打开这台电脑")}>
      <SettingsRow
        label={text("Same Wi-Fi", "同一 Wi-Fi")}
        description={!lan ? undefined : lan.address
          ? text(
            `Phones and computers on this network, at ${host(lan.address)}.`,
            `这个网络里的手机和电脑，地址：${host(lan.address)}。`,
          )
          : text("This computer is not on a local network.", "这台电脑没有连接局域网。")}
      >
        <Switch
          aria-label={text("Same Wi-Fi", "同一 Wi-Fi")}
          checked={!!lan?.on}
          disabled={!lan?.address}
          onCheckedChange={(on) => void flip("lan", on)}
        />
      </SettingsRow>
      {anywhere ? (
        <SettingsRow label={text("Anywhere", "任何地方")} description={anywhereText}>
          {anywhereAction}
          <Switch
            aria-label={text("Anywhere", "任何地方")}
            checked={anywhere.on}
            onCheckedChange={(on) => void flip("anywhere", on)}
          />
        </SettingsRow>
      ) : null}
      {problem ? <p className="py-3 text-fs-base text-destructive">{problem}</p> : null}
    </SettingsSection>
  );
}
