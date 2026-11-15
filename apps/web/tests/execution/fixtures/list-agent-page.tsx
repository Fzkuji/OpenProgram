/** An agent page that is only the run list. Scheduled tasks still use the
 *  list, and this reaches it through the same panel the rows live in. */
import { useState } from "react";
import { ActivityPageFrame } from "../../../components/right-sidebar/running/activity-page.tsx";
import { axisFor } from "../../../components/right-sidebar/running/activity-page-geometry.ts";
import { RunList } from "../../../components/right-sidebar/running/run-list.tsx";

export function AgentPage({ target, context }: any) {
  const scope = context.scopes.find((item: any) => item.key === target.scopeKey) ?? context.scopeOf(target.scopeKey);
  const head = context.scopeHeadText(scope);
  const [axis, setAxis] = useState(() => axisFor([], []));
  return <ActivityPageFrame kind={null} title={head.title} status={head.status} tone={head.tone} axis={axis}>
    <RunList source={{ kind: "branch", head: scope.head }} context={context} onAxis={setAxis} renderInside={() => null} reportName={() => null} />
  </ActivityPageFrame>;
}
