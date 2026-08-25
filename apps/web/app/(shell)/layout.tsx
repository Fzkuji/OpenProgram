"use client";

import dynamic from "next/dynamic";
import { Sidebar } from "@/components/sidebar/sidebar";

const AppShell = dynamic(
  () => import("@/components/app-shell").then((m) => m.AppShell),
  {
    ssr: false,
    loading: () => (
      <div className="app">
        <Sidebar />
      </div>
    ),
  },
);

export default function ShellLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShell>{children}</AppShell>;
}
