import { redirect } from "next/navigation";

// /settings → default to the General tab. Each tab is now a
// distinct URL (/settings/providers | /settings/search | /settings/general)
// so refresh and back-button preserve the active section.
export default function Page() {
  redirect("/settings/general");
}
