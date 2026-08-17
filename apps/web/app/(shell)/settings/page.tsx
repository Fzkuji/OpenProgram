import { redirect } from "next/navigation";

// /settings → default to the LLM Providers tab. Each tab is now a
// distinct URL (/settings/providers | /settings/search | /settings/general)
// so refresh and back-button preserve the active section.
export default function Page() {
  redirect("/settings/providers");
}
