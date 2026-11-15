/**
 * The time shown under a chat message.
 *
 * A message from today shows its clock time. An older one also shows its
 * date (and its year when that differs), so a conversation that spans days
 * never reads as "09:39 PM" sitting above "04:20 PM".
 */
export function formatMessageTime(value: Date, now: Date = new Date()): string {
  const time = value.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (value.toDateString() === now.toDateString()) return time;
  const date = value.toLocaleDateString([], value.getFullYear() === now.getFullYear()
    ? { month: "short", day: "numeric" }
    : { year: "numeric", month: "short", day: "numeric" });
  return `${date} ${time}`;
}
