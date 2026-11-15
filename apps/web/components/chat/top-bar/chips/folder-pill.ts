/**
 * The shell of a folder pill in the composer's environment row: one
 * raised pill holding a folder segment (opens the folder's menu) and,
 * when the folder is in a Git checkout, the git segment (GitChip). Each
 * segment is its own hover / click target (`.folder-pill-seg`); the shell
 * only carries the surface, shadow and clipping. Same surface as the
 * `elevated` Button variant, so it sits flush with the other chips.
 */
export const FOLDER_PILL =
  "folder-pill inline-flex h-(--ui-button-h-sm) shrink-0 items-stretch overflow-hidden rounded-md " +
  "bg-bg-input text-fs-sm font-medium whitespace-nowrap text-text-primary shadow-raised " +
  "transition-shadow hover:shadow-raised-hover has-[[aria-expanded=true]]:shadow-raised-hover";
