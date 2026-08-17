"use client";

import dynamic from "next/dynamic";

// Migrated from PageShell injection of /html/chats.html. Native React
// component with co-located CSS module — no longer references the
// global `.chats-*` class names from app/styles/07-chats.css.
const ChatsPage = dynamic(
  () => import("@/components/chats/chats-page").then((m) => m.ChatsPage),
  { ssr: false },
);

export default function Page() {
  return <ChatsPage />;
}
