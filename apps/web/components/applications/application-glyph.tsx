"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AppWindow, BookOpen, Bot, Briefcase, Calculator, Calendar, ChartColumn, CircleDot, Code, Database,
  FilePen, FileSearch, FileText, FlaskConical, Globe, Image, Languages, ListChecks, Mail, Map,
  MessageSquare, Mic, Music, NotebookPen, Palette, PenTool, Sigma, Sparkles, Table, Telescope, Terminal, Video,
} from "lucide-react";
import { applicationRequest, type ApplicationDefinition } from "@/lib/net/applications";

/** The glyphs an application may name with `icon` in application.json.
 *  Packages pick from this fixed set rather than shipping images, so every
 *  application draws in the same line style as the rest of the app. */
const glyphs = {
  calculator: Calculator, "book-open": BookOpen, "file-search": FileSearch, "file-text": FileText, "file-pdf": FileText,
  telescope: Telescope, "notebook-pen": NotebookPen, table: Table, "chart-column": ChartColumn, database: Database,
  image: Image, music: Music, video: Video, map: Map, calendar: Calendar, globe: Globe,
  code: Code, terminal: Terminal, mail: Mail, "message-square": MessageSquare,
  "list-checks": ListChecks, languages: Languages, "pen-tool": PenTool, palette: Palette,
  sigma: Sigma, "flask-conical": FlaskConical, briefcase: Briefcase, bot: Bot, sparkles: Sparkles,
  browser: Globe, office: FilePen, recording: CircleDot, meeting: Mic,
} as const;

export const APPLICATION_GLYPHS = Object.keys(glyphs);

export function ApplicationGlyph({ icon, size }: { icon?: string; size: number }) {
  const Icon = (icon && glyphs[icon as keyof typeof glyphs]) || AppWindow;
  return <Icon size={size} aria-hidden="true" />;
}

/** An application tab names only its application; the glyph comes from the
 *  installed definition, so it follows an upgrade without touching the tab. */
export function ApplicationTabGlyph({ appId, size }: { appId?: string; size: number }) {
  const { data } = useQuery({
    queryKey: ["applications"],
    queryFn: () => applicationRequest<{ applications: ApplicationDefinition[] }>("/api/applications"),
    staleTime: 60_000,
  });
  return <ApplicationGlyph icon={data?.applications.find(app => app.id === appId)?.icon} size={size} />;
}
