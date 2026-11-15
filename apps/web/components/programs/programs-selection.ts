/**
 * A selection another page asks the Programs explorer to open with, such
 * as "Show in Abilities" on the Applications page. The explorer takes it
 * once, the next time it loads its listing.
 */

export type ProgramSelectionRequest = {
  /** An application.json application's id. */
  applicationId?: string;
  /** A directory name under applications/ (built-in applications). */
  name?: string;
};

let pending: ProgramSelectionRequest | null = null;

export function requestProgramSelection(request: ProgramSelectionRequest): void {
  pending = request;
}

export function takeProgramSelection(): ProgramSelectionRequest | null {
  const request = pending;
  pending = null;
  return request;
}

/** The explorer path of the application row a request names, or null. */
export function resolveProgramSelection(
  entries: { path: string; name: string; application_id?: string }[],
  request: ProgramSelectionRequest,
): string | null {
  const found = (request.applicationId && entries.find(entry => entry.application_id === request.applicationId))
    || (request.name && entries.find(entry => entry.name === request.name));
  return found ? found.path : null;
}
