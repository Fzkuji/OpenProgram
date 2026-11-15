import { Blocks, Bot, CalendarDays, Cpu, History, Settings } from "lucide-react";
import type { ReactNode } from "react";

/** The glyph the left rail shows for a management page, so a tab that
 *  navigated to the page wears the page's icon along with its name. */
const pageIcons: Record<string, typeof Bot> = {
  agents: Bot,
  programs: Cpu, functions: Cpu, skills: Cpu, mcp: Cpu, plugins: Cpu, plugin: Cpu,
  applications: Blocks,
  chats: History, projects: History, memory: History, history: History,
  scheduler: CalendarDays,
  settings: Settings,
};

export function routePageIcon(route: string | undefined, size: number): ReactNode | null {
  if (!route) return null;
  const root = route.split(/[?#]/, 1)[0].split("/").filter(Boolean)[0];
  const Icon = root ? pageIcons[root] : undefined;
  return Icon ? <Icon size={size} /> : null;
}
