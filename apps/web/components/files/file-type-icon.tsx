import type { SVGProps } from "react";
import { FileIcon, DefaultFolderIcon, DefaultFolderOpenedIcon } from "@react-symbols/icons/utils";
import { Claude } from "@react-symbols/icons/files";

const fileNames = { "claude.md": Claude };

/** Shared decorative file identity; callers retain the visible filename. */
export function FileTypeIcon({ name, size = 16, className }: {
  name: string;
  size?: number;
  className?: string;
}) {
  const basename = name.split(/[\\/]/).pop()?.toLowerCase() ?? "";
  const extension = basename.split(".").pop() ?? "";
  // The library indexes plain objects; inherited properties are not file icons.
  const safeName = Object.hasOwn(Object.prototype, basename) || Object.hasOwn(Object.prototype, extension)
    ? ""
    : basename;
  return <FileIcon
    fileName={safeName}
    autoAssign
    editFileNameData={fileNames}
    width={size}
    height={size}
    className={className}
    aria-hidden="true"
    focusable="false"
    style={{ flexShrink: 0 }}
  />;
}


/** Shared folder identity; actions and caller-owned animation stay outside. */
export function FolderTypeIcon({ open = false, size = 16, style, ...props }: SVGProps<SVGSVGElement> & { open?: boolean; size?: number }) {
  const Icon = open ? DefaultFolderOpenedIcon : DefaultFolderIcon;
  return <Icon width={size} height={size} {...props} aria-hidden="true" focusable="false" style={{ flexShrink: 0, ...style }} />;
}
