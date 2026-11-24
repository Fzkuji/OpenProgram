import { redirect } from "next/navigation";

// The Scheduler is the Tasks page's Scheduled tab now.
export default function Page() {
  redirect("/tasks?tab=scheduled");
}
