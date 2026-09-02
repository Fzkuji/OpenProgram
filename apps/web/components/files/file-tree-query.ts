/** Pure project-relative path helpers used by FileTree queries and rows. */
export function joinPath(dir: string, name: string): string {
  return dir ? `${dir}/${name}` : name;
}
export function parentOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i > 0 ? path.slice(0, i) : "";
}

export function baseOf(path: string): string {
  return path.split("/").pop() || path;
}
