import assert from "node:assert/strict";
import test from "node:test";

import { isManageActionIcon } from "../components/ui/manage-action-icon.ts";

test("header actions treat forwardRef icons as components, not children", () => {
  const forwardRefIcon = {
    $$typeof: Symbol.for("react.forward_ref"),
    render() {},
    displayName: "RefreshCwIcon",
  };
  assert.equal(isManageActionIcon(forwardRefIcon), true);
  assert.equal(isManageActionIcon(() => null), true);
  assert.equal(
    isManageActionIcon({
      $$typeof: Symbol.for("react.element"),
      props: {},
      type: "svg",
    }),
    false,
  );
});
