"use client";

import dynamic from "next/dynamic";

const PageShell = dynamic(
  () => import("@/components/page-shell").then((m) => m.PageShell),
  { ssr: false }
);

export default function ChatsPage() {
  return <PageShell page="chats" />;
}
