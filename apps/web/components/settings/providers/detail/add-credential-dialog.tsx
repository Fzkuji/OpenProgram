"use client";

/**
 * Add a credential to a provider, or put a new one in a failing one's place
 * (docs/reference/design/providers/provider-management.html, "Adding a
 * credential"). One dialog for every way in:
 *
 *   choose → key | sign-in
 *
 * The choice step is the same choice list the sign-in panel uses
 * (MethodChoices), offering the provider's ways from the login-method
 * registry (`auth.methods`, its "Paste an API key" entry included); with
 * one way only it is skipped, with no flash of the list. The key step
 * checks a pasted key with the provider and shows what the check said;
 * the key is stored only by Add key / Replace key, with its name in the
 * same request, so Cancel or Escape leaves nothing behind and a key being
 * replaced keeps working until the new one is committed. A rejected key is
 * not stored. Replace keeps the row's name, place and id; signing in again
 * with the same account renews its row.
 */
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";

import { hostOf } from "../add/data";
import { KeyEntry, storeKey } from "../add/key-entry";
import { MethodChoices, ProviderLogin } from "../auth/provider-login";
import type { CredentialList, Provider } from "../types";
import { KEY_METHOD, credentialsUrl, maskedTail } from "./credential-model";
import { revalidateSettings, useSettingsData } from "@/lib/prefs/settings-cache";
import { waysIn } from "./credentials-section";

/** What the dialog was opened for. */
export type CredentialIntent =
  | { mode: "add"; prefer?: "key" | "login" }
  | { mode: "replace"; id: string; name: string }
  | { mode: "relogin"; id: string; name: string };

type Step = "choose" | "key" | "login";

export function AddCredentialDialog({
  open, onOpenChange, provider, intent, consoleUrl = null, onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  provider: Provider;
  intent: CredentialIntent;
  /** Where the provider's keys are made, when the status already knows it. */
  consoleUrl?: string | null;
  onDone?: () => void;
}) {
  const { text } = useTranslation();
  // The list read for this opening, tagged with the provider it is of, so a
  // dialog reused for another provider never reads the last one's ways.
  // The provider page already keeps this list, so the dialog opens with it;
  // each opening reads it again behind.
  const listUrl = credentialsUrl(provider.id);
  const list = useSettingsData<CredentialList>(open ? listUrl : null) ?? null;
  const [step, setStep] = useState<Step>("choose");
  const [method, setMethod] = useState("");
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [refused, setRefused] = useState(false);
  // A check has finished for the key the field holds now: Add waits for it.
  const [checked, setChecked] = useState(false);
  const [saving, setSaving] = useState(false);

  const { keyEntry, loginMethods } = waysIn(list?.auth.methods ?? []);
  const choices = intent.mode === "replace" ? []
    : intent.mode === "relogin" ? loginMethods
      : [
        ...(intent.prefer === "key" ? [] : loginMethods),
        ...(keyEntry && intent.prefer !== "login" ? [keyEntry] : []),
      ];
  const only = intent.mode === "replace" ? KEY_METHOD : choices.length === 1 ? choices[0].id : "";
  // A replace needs no list; every other way waits for this provider's.
  const ready = intent.mode === "replace" || !!list;
  // One way only: straight to it, decided while rendering (no flash).
  const shown: Step = step === "choose" && only ? (only === KEY_METHOD ? "key" : "login") : step;
  const picked = shown === "login" && step === "choose" ? only : method || choices[0]?.id || "";

  // Each opening starts over and reads this provider's list.
  useEffect(() => {
    if (!open) return;
    setStep("choose"); setMethod(""); setName(""); setKey(""); setRefused(false); setChecked(false); setSaving(false);
    void revalidateSettings(listUrl);
  }, [open, listUrl]);

  /** Back to the ways in: the key field starts empty when it comes back,
   *  so nothing it no longer shows stays armed for Add. */
  function backToChoices() {
    setStep("choose"); setKey(""); setRefused(false); setChecked(false);
  }

  /** Add key / Replace key: the one place a pasted key is stored. */
  async function commit() {
    if (!key || refused || !checked || saving) return;
    setSaving(true);
    try {
      const result = await storeKey(provider.id, key, {
        label: intent.mode === "add" ? name.trim() || undefined : undefined,
        replace: intent.mode === "replace" ? intent.id : undefined,
      });
      if (!result.ok) {
        showToast(result.error || text("The key was not saved.", "密钥没有保存。"), { tone: "error" });
        return;
      }
      onDone?.();
      onOpenChange(false);
    } catch {
      showToast(text("Couldn't save the key", "无法保存密钥"), { tone: "error" });
    } finally {
      setSaving(false);
    }
  }

  const back = choices.length > 1 && intent.mode !== "replace";
  const keysUrl = list?.auth.keys_url ?? consoleUrl;
  const count = list?.credentials.length ?? 0;
  const tail = key.length >= 8 ? key.slice(-4) : "";
  const loginTitle = intent.mode === "relogin"
    ? text(`Sign in again: ${intent.name}`, `重新登录：${intent.name}`)
    : text(`Add a ${provider.label} account`, `添加 ${provider.label} 账号`);
  const keyTitle = intent.mode === "replace"
    ? text(`Replace ${intent.name}`, `更换 ${intent.name}`)
    : text(`Add a key to ${provider.label}`, `给 ${provider.label} 添加密钥`);
  const prefer = intent.mode === "add" ? intent.prefer : undefined;
  const title = shown === "key" || (shown === "choose" && prefer === "key") ? keyTitle
    : shown === "login" || intent.mode === "relogin" || prefer === "login" ? loginTitle
      : text(`Connect ${provider.label}`, `连接 ${provider.label}`);
  const description = shown === "choose"
    ? (count && intent.mode === "add"
      ? text("Pick how to connect. It joins the list below your current account.", "选择接入方式。它会排在现有账号后面。")
      : text("Pick how to connect.", "选择接入方式。"))
    : shown === "key"
      ? (intent.mode === "replace"
        ? text("The new key keeps this key's name and place. It is checked first.", "新密钥沿用这个密钥的名称和位置，保存前先检查。")
        : text(`Checked with ${provider.label} before it is saved.`, `保存前先由 ${provider.label} 检查。`))
      : intent.mode === "relogin"
        ? text("Sign in with the same account to renew it.", "用同一个账号登录即可续上。")
        : text("It joins the list below the accounts you have.", "它会排在现有账号后面。");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-credential-dialog={intent.mode} data-step={ready ? shown : "loading"}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{ready ? description : null}</DialogDescription>
        </DialogHeader>

        {!ready ? null : shown === "choose" ? (
          <>
            <MethodChoices methods={choices} value={picked} onValueChange={setMethod} />
            <DialogFooter>
              <Button variant="ghost" onClick={() => onOpenChange(false)}>{text("Cancel", "取消")}</Button>
              <Button disabled={!picked} onClick={() => { setMethod(picked); setStep(picked === KEY_METHOD ? "key" : "login"); }}>{text("Continue", "继续")}</Button>
            </DialogFooter>
          </>
        ) : shown === "key" ? (
          <>
            <FieldGroup className="gap-4">
              {intent.mode === "add" ? (
                <Field>
                  <FieldLabel htmlFor={`credential-name-${provider.id}`}>
                    <span>{text("Name", "名称")}<span className="font-normal text-text-muted">{text(" (optional)", "（可选）")}</span></span>
                  </FieldLabel>
                  <Input id={`credential-name-${provider.id}`} value={name} onChange={(e) => setName(e.target.value)}
                    placeholder={tail ? text(`API key ${maskedTail(tail)}`, `API 密钥 ${maskedTail(tail)}`) : text("e.g. Personal", "例如：个人")} />
                </Field>
              ) : null}
              <KeyEntry providerId={provider.id} consoleUrl={keysUrl} host={hostOf(provider)} autoFocus checkOnly
                replace={intent.mode === "replace" ? intent.id : undefined} onValue={setKey} onRejected={setRefused} onChecked={setChecked} />
            </FieldGroup>
            <DialogFooter>
              {back ? (
                <Button variant="ghost" onClick={backToChoices}>{text("Back", "返回")}</Button>
              ) : (
                <Button variant="ghost" onClick={() => onOpenChange(false)}>{text("Cancel", "取消")}</Button>
              )}
              <Button disabled={!key || refused || !checked || saving} onClick={() => void commit()}>
                {saving ? <Spinner /> : null}
                {intent.mode === "replace" ? text("Replace key", "更换密钥") : text("Add key", "添加密钥")}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <ProviderLogin provider={{ ...provider, login_methods: loginMethods }} bare method={picked}
              onChanged={() => { onDone?.(); onOpenChange(false); }} />
            <DialogFooter>
              {back ? <Button variant="ghost" onClick={backToChoices}>{text("Back", "返回")}</Button>
                : <Button variant="ghost" onClick={() => onOpenChange(false)}>{text("Cancel", "取消")}</Button>}
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
