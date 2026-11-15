/**
 * One GET /api/agent_settings in flight per question.
 *
 * Opening the app or a conversation asks for the same settings from several
 * places within a moment (the socket connecting, the conversation loading,
 * the composer mounting). Every caller that asks the same question (the
 * same conversation and branch) while a request is out shares that
 * request's answer; nothing is kept once it has answered.
 *
 * A request that left before the settings changed must not answer a caller
 * that asks after the change: ``agentSettingsMoved`` (the server's
 * ``agent_settings_changed``, or this page's own write) makes every later
 * caller start a new request.
 */

/** The reply as ``Response.json()`` hands it over; each caller reads the
 *  fields it knows. */
type SettingsReply = ReturnType<Response["json"]>;
type SettingsReader = (url: string) => SettingsReply;

const SETTINGS_URL = "/api/agent_settings";
const flights = new Map<string, SettingsReply>();

const readJson: SettingsReader = async (url) => (await fetch(url)).json();

/** The settings were written: requests already out answer only the callers
 *  they have. */
export function agentSettingsMoved(): void {
  flights.clear();
}

/** The settings answer for ``query`` ("" or "?session_id=…&branch_id=…"),
 *  shared with every caller that asks the same while it is in flight. */
export function requestAgentSettings(query = "", read: SettingsReader = readJson): SettingsReply {
  const shared = flights.get(query);
  if (shared) return shared;
  const flight = read(SETTINGS_URL + query);
  flights.set(query, flight);
  const done = () => {
    if (flights.get(query) === flight) flights.delete(query);
  };
  flight.then(done, done);
  return flight;
}
