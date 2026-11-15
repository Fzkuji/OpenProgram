"use client";

import { forwardRef, type ComponentProps } from "react";
import { type ManageTabIcon } from "@/components/ui/manage-page";
import { Button } from "@/components/ui/button";
import { useActionIconAnimation } from "@/components/chat/messages/bubbles/use-action-icon-animation";

/** Use the same whole-button hover/focus contract as chat actions. */
// 28px (`sm`) like the composer's controls unless a call site asks otherwise.
export const AgentButton = forwardRef<HTMLButtonElement, ComponentProps<typeof Button> & { icon?: ManageTabIcon }>(function AgentButton({ icon: Icon, children, size = "default", ...props }, buttonRef) {
  const animation = useActionIconAnimation(Boolean(props.disabled));
  return <Button ref={buttonRef} size={size} {...props} {...animation.handlers}>
    {Icon ? <Icon ref={animation.ref} size={16} aria-hidden /> : null}{children}
  </Button>;
});
