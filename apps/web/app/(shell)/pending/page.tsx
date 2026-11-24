import { redirect } from "next/navigation";

// Pending is the Tasks page's Inbox tab now.
export default function Page() {
  redirect("/tasks?tab=inbox");
}
