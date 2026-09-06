import { FileIcon } from "@react-symbols/icons/utils";
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
