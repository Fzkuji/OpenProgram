"use client";

/**
 * Settings page building blocks, composed only from the official shadcn/ui
 * components (Card, Field, Separator) — the page, a titled section card,
 * and one labelled row. Every Settings tab lays itself out with these, so
 * no tab carries its own row / label / card CSS.
 */
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
  FieldTitle,
} from "@/components/ui/field";

/** A Settings tab: fixed title + description, scrolling body. */
export function SettingsPage({
  title,
  description,
  children,
  bodyClassName,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  bodyClassName?: string;
  /** Controls at the end of the title row. */
  actions?: ReactNode;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex shrink-0 flex-col gap-1 border-b px-7 pt-5 pb-4">
        {actions ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-fs-title font-semibold text-text-primary">{title}</h2>
            {actions}
          </div>
        ) : (
          <h2 className="text-fs-title font-semibold text-text-primary">{title}</h2>
        )}
        {description ? (
          <p className="max-w-3xl text-fs-base text-text-muted">{description}</p>
        ) : null}
      </header>
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col gap-8 overflow-y-auto px-7 pt-6 pb-8",
          bodyClassName
        )}
      >
        {children}
      </div>
    </div>
  );
}

/** The title, description and action of a group of settings. */
export function SettingsHeading({
  title,
  description,
  action,
}: {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-end justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        {title ? <h3 className="text-fs-md font-medium text-text-primary">{title}</h3> : null}
        {description ? <p className="text-fs-base text-text-muted">{description}</p> : null}
      </div>
      {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
    </div>
  );
}

/** A titled group of rows on one Card; rows are divided by hairlines. */
export function SettingsSection({
  title,
  description,
  action,
  children,
  className,
}: {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("flex flex-col gap-3", className)}>
      {title || description || action ? (
        <SettingsHeading title={title} description={description} action={action} />
      ) : null}
      <Card className="gap-0 py-0">
        <CardContent className="divide-y divide-border">{children}</CardContent>
      </Card>
    </section>
  );
}

/**
 * One setting: label (+ optional description) on the left, its control on
 * the right, centred on the row's height. Every select and text field is the same 10rem wide and every
 * button at least 8rem, so the controls of a page line up. `htmlFor` makes the label a real <label> for that control;
 * `stacked` puts a wide control (editor, grid, list) under the label.
 */
export function SettingsRow({
  label,
  description,
  htmlFor,
  stacked = false,
  children,
  className,
}: {
  label: ReactNode;
  description?: ReactNode;
  htmlFor?: string;
  stacked?: boolean;
  children?: ReactNode;
  className?: string;
}) {
  const heading = htmlFor ? (
    <FieldLabel htmlFor={htmlFor}>{label}</FieldLabel>
  ) : (
    <FieldTitle>{label}</FieldTitle>
  );
  if (stacked) {
    return (
      <Field className={cn("py-4", className)}>
        <FieldContent>
          {heading}
          {description ? <FieldDescription>{description}</FieldDescription> : null}
        </FieldContent>
        {children}
      </Field>
    );
  }
  return (
    <Field orientation="horizontal" className={cn("py-4 has-[>[data-slot=field-content]]:items-center", className)}>
      <FieldContent>
        {heading}
        {description ? <FieldDescription>{description}</FieldDescription> : null}
      </FieldContent>
      {children != null ? (
        <div className="flex min-w-0 shrink-0 items-center justify-end gap-2 [&>[data-slot=button]]:min-w-32 [&>[data-slot=input]]:w-40 [&>[data-slot=select-trigger]]:w-40">
          {children}
        </div>
      ) : null}
    </Field>
  );
}
