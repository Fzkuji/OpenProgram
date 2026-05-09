"use client";

import dynamic from "next/dynamic";

const AppShell = dynamic(
  () => import("@/components/app-shell").then((m) => m.AppShell),
  { ssr: false }
);

export default function ShellLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShell>{children}</AppShell>;
}
