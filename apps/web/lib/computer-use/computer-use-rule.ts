/** Project permission rules that allow computer use of one app; rules saved
 *  under the tool's earlier name ``computer_use`` grant the same app. A VM
 *  endpoint rule (``vm:<url>``) and the user's own browser (``browser:chrome``) are not apps. */
const APP_RULE = /^computer_use(?:_agent)?\((?!vm:|browser:)(.+)\)$/i;

/** The app a ``computer_use_agent(<app>)`` rule names, or null for any other rule. */
export function computerUseRuleApp(rule: string): string | null {
  const match = APP_RULE.exec(rule.trim());
  return match ? match[1].trim() : null;
}
