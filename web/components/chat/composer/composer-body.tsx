"use client";

/**
 * Input-area body per composer mode.
 *
 * 按当前 mode 渲染输入区主体。所有「问用户」的形态（ask/confirm/approval/
 * form/ask_many）都由唯一的 QuestionMode 承接，不再分组件；fn-form 走
 * FunctionForm；其余走普通聊天的 ChatInputRow。
 *
 * The outgoing fn-form crossfade layer rides along here because it must be
 * a sibling rendered AFTER the live form (see the comment at its JSX).
 */
import React from "react";

import { FunctionForm } from "./modes/fn-form/fn-form";
import { QuestionMode, type DecisionAction } from "./modes/question/question-mode";
import { ChatInputRow } from "./chat-input-row";
import type { useFnFormState } from "./modes/fn-form/use-fn-form-state";
import type { useFileMention } from "./attach/use-file-mention";
import type { useSlashMenu } from "./slash/use-slash-menu";
import type { usePasteTokens } from "./paste/use-paste-tokens";
import styles from "./composer.module.css";

const noop = () => {};

type FnFormFn = React.ComponentProps<typeof FunctionForm>["fn"];
type Decision = React.ComponentProps<typeof QuestionMode>["decision"];

export interface ComposerBodyProps {
  bound: string | null;
  composerMode: string;
  activeDecision: Decision | null;
  dequeueDecision(id: string): void;
  setDecisionAction(action: DecisionAction | null): void;
  fnFormFunction: FnFormFn | null;
  fnForm: ReturnType<typeof useFnFormState>;
  handleFnFormClose(): void;
  submitFnForm(): void;
  outgoingFn: FnFormFn | null;
  textareaRef: React.RefObject<HTMLTextAreaElement>;
  input: string;
  setInput(next: string): void;
  isRunning: boolean;
  runningPlaceholder: string;
  onKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement>;
  onPaste: ReturnType<typeof usePasteTokens>["onPaste"];
  slash: ReturnType<typeof useSlashMenu>;
  fileMention: ReturnType<typeof useFileMention>;
  pastedEntries: ReturnType<typeof usePasteTokens>["pastedEntries"];
  pasteMissing: ReturnType<typeof usePasteTokens>["pasteMissing"];
  removePaste: ReturnType<typeof usePasteTokens>["removePaste"];
}

export function ComposerBody({
  bound,
  composerMode,
  activeDecision,
  dequeueDecision,
  setDecisionAction,
  fnFormFunction,
  fnForm,
  handleFnFormClose,
  submitFnForm,
  outgoingFn,
  textareaRef,
  input,
  setInput,
  isRunning,
  runningPlaceholder,
  onKeyDown,
  onPaste,
  slash,
  fileMention,
  pastedEntries,
  pasteMissing,
  removePaste,
}: ComposerBodyProps) {
  return (
    <>
        {/* 按当前 mode 渲染输入区主体。所有「问用户」的形态（ask/confirm/
            approval/form/ask_many）都由唯一的 QuestionMode 承接，不再分组件。 */}
        {activeDecision ? (
          <QuestionMode
            key={activeDecision.id}
            decision={activeDecision}
            onResolve={dequeueDecision}
            onAction={setDecisionAction}
          />
        ) : composerMode === "fn-form" && fnFormFunction ? (
          <FunctionForm
            // `key` ties to fn name so React re-mounts on every
            // switch — the freshly mounted header/body run their own
            // fadeIn animation, completing the crossfade with the
            // outgoing overlay below.
            key={fnFormFunction.name}
            fn={fnFormFunction}
            values={fnForm.values}
            setValue={fnForm.setValue}
            workdir={fnForm.workdir}
            setWorkdir={fnForm.setWorkdir}
            errorParam={fnForm.error}
            closing={fnForm.closing}
            onClose={handleFnFormClose}
            onSubmit={submitFnForm}
          />
        ) : (
          <ChatInputRow
            textareaRef={textareaRef}
            input={input}
            setInput={setInput}
            inputId={bound ? `composer-chat-input-${bound}` : undefined}
            placeholder={isRunning ? runningPlaceholder : undefined}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            onFocus={() => slash.setFocused(true)}
            onBlur={() => slash.setFocused(false)}
            setCaretPos={fileMention.setCaretPos}
            pastedEntries={pastedEntries}
            pasteMissing={pasteMissing}
            removePaste={removePaste}
            atToken={fileMention.atToken}
            fileMatches={fileMention.fileMatches}
            fileMenuIndex={fileMention.fileMenuIndex}
            setFileMenuIndex={fileMention.setFileMenuIndex}
            fileMenuLoading={fileMention.fileMenuLoading}
            fileMenuPos={fileMention.fileMenuPos}
            pickFile={fileMention.pickFile}
          />
        )}
        {/* Outgoing fn-form overlay — only present during a fn → fn
            switch. Rendered AFTER the main form so that
            `querySelector('[data-fn-form-header]')` in the wrapper
            height measurement matches the main form first (the
            outgoing layer's cloned header/body would otherwise lock
            wrapper height to the previous form's size). Absolute +
            z-index 1 still puts it visually on top during the fade. */}
        {outgoingFn && (
          <div
            key={`${outgoingFn.name}-outgoing`}
            className={styles.outgoingLayer}
            aria-hidden="true"
          >
            <FunctionForm
              fn={outgoingFn}
              values={{}}
              setValue={noop}
              workdir=""
              setWorkdir={noop}
              errorParam={null}
              onClose={noop}
              onSubmit={noop}
              // Outgoing crossfade copy — strip input `id`s so the
              // browser doesn't complain about duplicate-id form
              // fields while both the live and ghost forms are
              // mounted simultaneously during the fade.
              ghost
            />
          </div>
        )}
    </>
  );
}
