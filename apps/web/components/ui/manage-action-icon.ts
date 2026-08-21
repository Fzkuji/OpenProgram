/** True for function and forwardRef icon components, false for element nodes. */
export function isManageActionIcon(icon: unknown): boolean {
  if (typeof icon === "function") return true;
  return Boolean(
    icon
    && typeof icon === "object"
    && "render" in icon
    && typeof (icon as { render?: unknown }).render === "function",
  );
}
