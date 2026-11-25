"use client";

/**
 * Add device: first what it is, then one step.
 *
 * - Phone or tablet: a one-time QR code (Codex: "Connect your phone to this Mac").
 *   With no way in yet, Same Wi-Fi and Anywhere are offered side by side.
 * - Another computer with OpenProgram: show its conversations here (paste
 *   the link it shows), or let it use this one (show this one's code).
 * - Server over SSH: a host from ~/.ssh/config, or ``user@host`` (Claude
 *   desktop's Add SSH connection); saved, then tested.
 * - Something else: described in a sentence and handed to the agent in a
 *   new chat, which finds the details and adds and tests it with
 *   ``openprogram machines``.
 * - Set up by hand: the full form.
 */
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ChevronRight, Globe, Monitor, Server, Smartphone, Sparkles, Wifi } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { useSessionStore } from "@/lib/session-store";
import { openNewChat } from "@/lib/tabs/navigation/open-session";
import styles from "../settings-page.module.css";
import { host, type Ways } from "./access-section";
import { machineRequest, SAVE_WAIT_MS, sshHosts, TEST_WAIT_MS, type Text } from "./device-requests";
import { PairingCode } from "./pairing-code";

type Step = "choose" | "phone" | "computer" | "ssh" | "agent";

function Choice({
  icon,
  title,
  sub,
  disabled,
  onClick,
}: { icon: ReactNode; title: string; sub: string; disabled?: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-lg border bg-bg-input px-3 py-2.5 text-left shadow-raised hover:shadow-raised-hover disabled:pointer-events-none disabled:opacity-50"
    >
      <span className="text-text-muted [&_svg]:size-5">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block text-fs-base font-medium">{title}</span>
        <span className="block text-fs-sm text-text-muted">{sub}</span>
      </span>
      <ChevronRight className="size-4 text-text-muted" aria-hidden="true" />
    </button>
  );
}

type Change = (body: Partial<Record<"lan" | "anywhere", boolean>>) => Promise<string>;

function hasWayIn(ways: Ways | null): boolean {
  return !!(ways?.lan.on || (ways?.anywhere?.on && ways.anywhere.state === "running"));
}

/** One way in, as a line under the code: on (where), or what is missing and the one click that fixes it. */
function WayRow({ label, on, note, action }: { label: string; on: boolean; note?: string | null; action?: ReactNode }) {
  return (
    <div className="flex min-h-8 items-center gap-2">
      <span className={`size-2 shrink-0 rounded-full ${on ? "bg-[var(--accent-green)]" : "bg-[var(--text-muted)] opacity-50"}`} aria-hidden="true" />
      <span className="font-medium">{label}</span>
      {note ? <span className="min-w-0 text-text-muted">{note}</span> : null}
      {action ? <span className="ml-auto shrink-0">{action}</span> : null}
    </div>
  );
}

/**
 * Where the other device reaches this computer from. With no way open the
 * two are offered side by side, each with its cost; once one is open the
 * code shows, with a line per way under it so the other can be opened in
 * place. Anywhere's sign-in happens here too, not behind the dialog.
 */
function WaysIn({ ways, change, hint }: { ways: Ways | null; change: Change; hint: string }) {
  const { text } = useTranslation();
  const [problem, setProblem] = useState("");
  const flip = (body: Partial<Record<"lan" | "anywhere", boolean>>) => void change(body).then(setProblem);
  const lan = ways?.lan;
  const anywhere = ways?.anywhere;
  const open = hasWayIn(ways);
  const anywhereRunning = !!anywhere?.on && anywhere.state === "running";
  const problemLine = problem ? <p className="text-fs-sm text-destructive">{problem}</p> : null;

  if (!open && !anywhere?.on) {
    return (
      <div className="flex flex-col gap-2">
        <p className="pb-1 text-fs-base">{text("Where will it reach this computer from?", "它从哪里连这台电脑？")}</p>
        <Choice
          icon={<Wifi />}
          title={text("Same Wi-Fi", "同一 Wi-Fi")}
          sub={lan?.address
            ? text("Works at once. Not encrypted, and no notifications.", "马上能用；不加密，收不到通知。")
            : text("This computer is not on a local network.", "这台电脑没有连接局域网。")}
          disabled={!lan?.address}
          onClick={() => flip({ lan: true })}
        />
        {anywhere ? (
          <Choice
            icon={<Globe />}
            title={text("Anywhere", "任何地方")}
            sub={text(
              "Away from home too, over https, with notifications. Sign in to Tailscale once.",
              "出门也能用，走 https，能收通知；登录一次 Tailscale。",
            )}
            onClick={() => flip({ anywhere: true })}
          />
        ) : null}
        {problemLine}
      </div>
    );
  }

  const turnOn = (way: "lan" | "anywhere") => (
    <Button size="sm" variant="elevated" onClick={() => flip({ [way]: true })}>{text("Switch on", "打开")}</Button>
  );
  let anywhereNote: string | null = null;
  let anywhereAction: ReactNode = null;
  if (anywhere && !anywhere.on) {
    anywhereNote = text("Off; it can't connect away from home", "没开，出门连不上");
    anywhereAction = turnOn("anywhere");
  } else if (anywhere?.state === "running" && anywhere.address) {
    anywhereNote = host(anywhere.address);
  } else if (anywhere?.state === "needs_login" && anywhere.login_url) {
    anywhereNote = text("Sign in to Tailscale once to finish. It's free.", "登录一次 Tailscale 即可完成，免费。");
    anywhereAction = (
      <Button size="sm" onClick={() => window.open(anywhere.login_url!, "_blank", "noopener")}>{text("Sign in", "登录")}</Button>
    );
  } else if (anywhere?.state === "needs_funnel" && anywhere.enable_url) {
    anywhereNote = text("One more click in Tailscale lets it through.", "在 Tailscale 里再点一下就能连上。");
    anywhereAction = (
      <Button size="sm" onClick={() => window.open(anywhere.enable_url!, "_blank", "noopener")}>{text("Allow", "允许")}</Button>
    );
  } else if (anywhere?.state === "error") {
    anywhereNote = anywhere.message;
    // Off and on again starts a new sidecar; the one that failed is stopped.
    anywhereAction = (
      <Button
        size="sm"
        variant="elevated"
        onClick={() => void change({ anywhere: false }).then((first) => first ? setProblem(first) : flip({ anywhere: true }))}
      >
        {text("Try again", "重试")}
      </Button>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {/* A new code when the ways change, so its link carries every open address. */}
      {open ? <PairingCode key={`${!!lan?.on}-${anywhereRunning}`} hint={hint} /> : null}
      <div className={`flex flex-col gap-1 text-fs-sm ${open ? "border-t pt-3" : ""}`}>
        <WayRow
          label={text("Same Wi-Fi", "同一 Wi-Fi")}
          on={!!lan?.on}
          note={lan?.on && lan.address
            ? host(lan.address)
            : lan?.address ? text("Off", "没开") : text("Not on a local network", "没有连接局域网")}
          action={!lan?.on && lan?.address ? turnOn("lan") : null}
        />
        {anywhere ? (
          <WayRow label={text("Anywhere", "任何地方")} on={anywhereRunning} note={anywhereNote} action={anywhereAction} />
        ) : null}
      </div>
      {problemLine}
    </div>
  );
}

export function AddDeviceDialog({
  ways,
  change,
  onManual,
  onClose,
}: {
  ways: Ways | null;
  change: Change;
  onManual: () => void;
  onClose: () => void;
}) {
  const { text } = useTranslation();
  const [step, setStep] = useState<Step>("choose");

  const titles: Record<Step, string> = {
    choose: text("Add device", "添加设备"),
    phone: text("Add a phone or tablet", "添加手机或平板"),
    computer: text("Add another computer", "添加另一台电脑"),
    ssh: text("Add a server over SSH", "添加 SSH 服务器"),
    agent: text("Let the agent set it up", "交给智能体设置"),
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      {/* One column no wider than the dialog: the pairing link would otherwise widen it past the edge. */}
      <DialogContent className="grid-cols-[minmax(0,1fr)] sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>{titles[step]}</DialogTitle>
          {step === "choose" ? <DialogDescription>{text("What do you want to add?", "要添加什么设备？")}</DialogDescription> : null}
        </DialogHeader>
        {step === "choose" ? (
          <div className="flex flex-col gap-2">
            <Choice
              icon={<Smartphone />}
              title={text("Phone or tablet", "手机或平板")}
              sub={text("Use this computer's OpenProgram from it. Scan a code.", "在它上面用这台电脑的 OpenProgram，扫码即可。")}
              onClick={() => setStep("phone")}
            />
            <Choice
              icon={<Monitor />}
              title={text("Another computer with OpenProgram", "另一台装了 OpenProgram 的电脑")}
              sub={text("See its conversations here, or yours there.", "在这里看它的对话，或在那里用这台。")}
              onClick={() => setStep("computer")}
            />
            <Choice
              icon={<Server />}
              title={text("Server over SSH", "SSH 服务器")}
              sub={text("Run conversations in a folder on it.", "在它上面的文件夹里运行对话。")}
              onClick={() => setStep("ssh")}
            />
            <Choice
              icon={<Sparkles />}
              title={text("Something else — let the agent set it up", "其他设备，交给智能体")}
              sub={text("A virtual machine, Docker, a remote desktop… describe it.", "虚拟机、Docker、远程桌面……描述一下就行。")}
              onClick={() => setStep("agent")}
            />
            <button type="button" className="self-start pt-1 text-fs-sm text-text-muted hover:text-foreground" onClick={onManual}>
              {text("Set up by hand", "手动设置")}
            </button>
          </div>
        ) : null}
        {step === "phone" ? (
          <WaysIn
            ways={ways}
            change={change}
            hint={text(
              "Scan with the phone's camera. It opens OpenProgram in the phone's browser, already signed in.",
              "用手机相机扫码，会在手机浏览器里打开 OpenProgram，并且已经登录。",
            )}
          />
        ) : null}
        {step === "computer" ? <AnotherComputer ways={ways} change={change} onDone={onClose} /> : null}
        {step === "ssh" ? <SshServer onDone={onClose} /> : null}
        {step === "agent" ? <AskAgent onDone={onClose} /> : null}
        {step !== "choose" ? (
          <DialogFooter className="sm:justify-start">
            <Button variant="ghost" onClick={() => setStep("choose")}>{text("Back", "返回")}</Button>
          </DialogFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function AnotherComputer({
  ways,
  change,
  onDone,
}: {
  ways: Ways | null;
  change: Change;
  onDone: () => void;
}) {
  const { text } = useTranslation();
  const [direction, setDirection] = useState<"here" | "there">("here");
  const [link, setLink] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function connect(event: FormEvent) {
    event.preventDefault();
    if (!link.trim()) return;
    setBusy(true);
    setError("");
    const res = await machineRequest("connect_openprogram", { link: link.trim() }, text, SAVE_WAIT_MS);
    setBusy(false);
    if (res.ok) onDone();
    else setError(res.error || "");
  }

  return (
    <div className="flex flex-col gap-4">
      <ToggleGroup
        type="single"
        variant="segmented"
        value={direction}
        onValueChange={(value) => { if (value) setDirection(value as "here" | "there"); }}
        aria-label={text("Direction", "方向")}
      >
        <ToggleGroupItem value="here">{text("Show its conversations here", "在这里看它的对话")}</ToggleGroupItem>
        <ToggleGroupItem value="there">{text("Let it use this one", "让它用这台")}</ToggleGroupItem>
      </ToggleGroup>
      {direction === "here" ? (
        <form onSubmit={connect} className="flex flex-col gap-3">
          <p className="text-fs-sm text-text-muted">
            {text(
              "On the other computer open Settings → Devices → Add device → Another computer → Let it use this one, and paste its link here.",
              "在另一台电脑上打开 设置 → 设备 → 添加设备 → 另一台电脑 → 让它用这台，把链接粘贴到这里。",
            )}
          </p>
          <Input
            autoFocus
            spellCheck={false}
            className={styles.authMono}
            placeholder="http://192.168.1.20:18100/#pair=…"
            value={link}
            onChange={(e) => setLink(e.target.value)}
          />
          {error ? <p className={styles.rowStatusError}>{error}</p> : null}
          <Button type="submit" className="self-end" disabled={busy || !link.trim()}>{text("Connect", "连接")}</Button>
        </form>
      ) : (
        <WaysIn
          ways={ways}
          change={change}
          hint={text(
            "On the other computer: Add device → Another computer → Show its conversations here, then paste this link.",
            "在另一台电脑上：添加设备 → 另一台电脑 → 在这里看它的对话，然后粘贴这个链接。",
          )}
        />
      )}
    </div>
  );
}

function SshServer({ onDone }: { onDone: () => void }) {
  const { text } = useTranslation();
  const [hosts, setHosts] = useState<string[]>([]);
  const [target, setTarget] = useState("");
  const [state, setState] = useState<{ busy: boolean; error: string }>({ busy: false, error: "" });

  useEffect(() => {
    void sshHosts().then(setHosts);
  }, []);

  async function add(event: FormEvent) {
    event.preventDefault();
    const value = target.trim();
    if (!value) return;
    setState({ busy: true, error: "" });
    const res = await machineRequest(
      "save_machine", { machine: { name: value, connection: { kind: "ssh", target: value } } }, text, SAVE_WAIT_MS,
    );
    if (!res.ok || !res.id) {
      setState({ busy: false, error: res.error || "" });
      return;
    }
    const tested = await wsRequest<{ id: string; ok: boolean; message: string }>(
      "test_machine", { id: res.id }, "machine_test", (d) => d?.id === res.id, TEST_WAIT_MS, { requestId: true },
    ).catch(() => null);
    if (tested && !tested.ok) {
      setState({ busy: false, error: tested.message });
      return;
    }
    onDone();
  }

  return (
    <form onSubmit={add} className="flex flex-col gap-3">
      {hosts.length ? (
        <>
          <span className="text-fs-sm text-text-muted">{text("From your SSH configuration", "来自你的 SSH 配置")}</span>
          <div className="flex flex-wrap gap-2">
            {hosts.map((host) => (
              <Button
                key={host}
                type="button"
                variant="elevated"
                size="sm"
                aria-pressed={target === host}
                className="font-mono"
                onClick={() => setTarget(host)}
              >
                {host}
              </Button>
            ))}
          </div>
          <span className="pt-1 text-fs-sm text-text-muted">{text("Or type an address", "或者输入地址")}</span>
        </>
      ) : null}
      <Input
        autoFocus={!hosts.length}
        spellCheck={false}
        className={styles.authMono}
        placeholder="user@host"
        value={target}
        onChange={(e) => setTarget(e.target.value)}
      />
      <p className="text-fs-sm text-text-muted">
        {text(
          "Keys come from your SSH agent and ~/.ssh/config. The connection is tested before you use it.",
          "密钥来自你的 SSH agent 和 ~/.ssh/config。添加后会先测试连接。",
        )}
      </p>
      {state.error ? <p className={`${styles.rowStatusError} [overflow-wrap:anywhere]`}>{state.error}</p> : null}
      <Button type="submit" className="self-end" disabled={state.busy || !target.trim()}>{text("Add", "添加")}</Button>
    </form>
  );
}

function agentPrompt(description: string, text: Text): string {
  return text(
    `Add this device to OpenProgram so you can use it: ${description}\n\nFind the details yourself and ask me only for what you can't find. Add it with \`openprogram machines add\`, then check it with \`openprogram machines test\`.`,
    `把这台设备加到 OpenProgram 里，让你可以使用它：${description}\n\n需要的信息你自己去查，查不到的再问我。用 \`openprogram machines add\` 添加，再用 \`openprogram machines test\` 测试。`,
  );
}

function AskAgent({ onDone }: { onDone: () => void }) {
  const { text } = useTranslation();
  const [description, setDescription] = useState("");

  function start(event: FormEvent) {
    event.preventDefault();
    const value = description.trim();
    if (!value) return;
    const draft = openNewChat();
    useSessionStore.getState().setComposerInputFor(draft, agentPrompt(value, text));
    onDone();
  }

  return (
    <form onSubmit={start} className="flex flex-col gap-3">
      <Textarea
        autoFocus
        rows={3}
        value={description}
        placeholder={text(
          "The Ubuntu VM in UTM on this Mac; I want the agent to use its screen",
          "这台 Mac 上 UTM 里的 Ubuntu 虚拟机，想让智能体操作它的屏幕",
        )}
        onChange={(e) => setDescription(e.target.value)}
      />
      <p className="text-fs-sm text-text-muted">
        {text(
          "Opens a new chat with this request. The agent finds the details, asks what it can't find, adds the device and tests it.",
          "会打开一个新对话并填好请求。智能体自己查参数，查不到的再问你，然后添加并测试。",
        )}
      </p>
      <Button type="submit" className="self-end" disabled={!description.trim()}>{text("Start", "开始")}</Button>
    </form>
  );
}
