"use client";

/**
 * Long-paste auto-attach.
 *
 * A paste over `LONG_PASTE_THRESHOLD` characters is stashed in the
 * module-level `pasteStore` and replaced in the textarea by a
 * `[Pasted #N +M lines]` token, so the draft stays readable; `submit`
 * expands the tokens back before sending. Image clipboard items take
 * priority and route to the attachment hook instead.
 *
 * The store is process-wide, so a paste survives session switches and is
 * referenced by id from whichever draft holds its token; entries no draft
 * references anymore are GC'd here.
 */
import React, { useCallback, useEffect, useState } from "react";

import { useSessionStore } from "@/lib/session-store";
import {
  collectImagesFromTransfer,
  type PendingImage,
} from "../attach/image-attach";
import {
  LONG_PASTE_THRESHOLD,
  missingPasteIds,
  pasteStore,
  placeholderToken,
  referencedPasteIds,
} from "./paste-store";

export interface PasteTokensOptions {
  input: string;
  setInput(next: string): void;
  /** Chat key the paste belongs to — a late image decode must land in the
   *  bucket that was focused when the paste happened, never the current one. */
  activeChatKey: string | null;
  addImagesForOwner(ownerKey: string | null, images: PendingImage[]): void;
  setImageError(message: string | null): void;
}

export function usePasteTokens({
  input,
  setInput,
  activeChatKey,
  addImagesForOwner,
  setImageError,
}: PasteTokensOptions) {
  // Subscribing to the store rerenders the chip row whenever a paste is
  // added or removed.
  const [pasteTick, setPasteTick] = useState(0);
  useEffect(() => pasteStore.subscribe(() => setPasteTick((t) => t + 1)), []);

  const pastedEntries = React.useMemo(() => {
    const referenced = referencedPasteIds(input);
    // Only show chips for tokens still present in the live draft.
    // Include lost ones (chips in "missing" state) so the user sees
    // them and can remove the dead tokens.
    const live = pasteStore.list().filter((e) => referenced.has(e.id));
    const liveIds = new Set(live.map((e) => e.id));
    const out = [...live];
    referenced.forEach((id) => {
      if (!liveIds.has(id)) {
        out.push({ id, content: "", numLines: 0 });
      }
    });
    return out.sort((a, b) => a.id - b.id);
    // pasteTick is read implicitly by re-running this memo on tick bump.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input, pasteTick]);

  const pasteMissing = React.useMemo(
    () => missingPasteIds(input),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [input, pasteTick],
  );

  // GC paste store entries that no draft references anymore. Watches
  // composerDrafts AND the live ``input`` (the current session's
  // draft). Without this, removing a session leaves its pastes stuck
  // in the store forever.
  const composerDrafts = useSessionStore((s) => s.composerDrafts);
  useEffect(() => {
    const all = new Set<number>();
    referencedPasteIds(input).forEach((id) => all.add(id));
    for (const k in composerDrafts) {
      referencedPasteIds(composerDrafts[k]).forEach((id) => all.add(id));
    }
    pasteStore.retainOnly(all);
  }, [composerDrafts, input]);

  const onPaste = useCallback(
    (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
      // Image clipboard items take priority — if the user copied a
      // screenshot we want to attach it, never paste raw binary into
      // the textarea. Browsers populate ``items[].kind === "file"``
      // for image pastes from screenshot tools and most file
      // managers; ordinary text pastes leave items empty / "string".
      const items = e.clipboardData?.items;
      if (items) {
        let hasImage = false;
        for (let i = 0; i < items.length; i++) {
          if (items[i].kind === "file"
              && items[i].type.startsWith("image/")) {
            hasImage = true;
            break;
          }
        }
        if (hasImage) {
          const pasteOwnerKey = activeChatKey;
          e.preventDefault();
          void collectImagesFromTransfer(e.clipboardData!)
            .then(({ images, errors }) => {
              if (images.length) addImagesForOwner(pasteOwnerKey, images);
              // A file that failed to read (oversize / unsupported) must
              // say so — matching the drop path. Before this, a pasted
              // 12 MB screenshot produced no chip and no message at all.
              if (errors.length
                  && useSessionStore.getState().activeChatKey === pasteOwnerKey) {
                setImageError(errors.join("; "));
              }
            })
            .catch((err) => {
              if (useSessionStore.getState().activeChatKey === pasteOwnerKey) {
                setImageError(String(err));
              }
            });
          return;
        }
      }
      const text = e.clipboardData?.getData("text") ?? "";
      if (text.length < LONG_PASTE_THRESHOLD) return;
      // Replace the textarea selection with our placeholder token and
      // stash the real content in the paste store.
      e.preventDefault();
      const entry = pasteStore.add(text);
      const token = placeholderToken(entry);
      const ta = e.currentTarget;
      const start = ta.selectionStart ?? input.length;
      const end = ta.selectionEnd ?? start;
      const next = input.slice(0, start) + token + input.slice(end);
      setInput(next);
      // Place caret after the inserted token on the next frame.
      requestAnimationFrame(() => {
        const pos = start + token.length;
        ta.setSelectionRange(pos, pos);
      });
    },
    [activeChatKey, input, setInput, addImagesForOwner, setImageError],
  );

  // Remove a paste chip — also strips the token from the textarea.
  const removePaste = useCallback(
    (id: number) => {
      const re = new RegExp(`\\[Pasted #${id} \\+\\d+ lines\\]`, "g");
      setInput(input.replace(re, ""));
      pasteStore.remove(id);
    },
    [input, setInput],
  );

  return { pastedEntries, pasteMissing, onPaste, removePaste };
}
