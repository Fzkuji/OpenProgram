"use client";

import dynamic from "next/dynamic";

const McpPage = dynamic(
  () => import("@/components/mcp/mcp-page").then((m) => m.McpPage),
  { ssr: false },
);

export default function Page() {
  return <McpPage />;
}
