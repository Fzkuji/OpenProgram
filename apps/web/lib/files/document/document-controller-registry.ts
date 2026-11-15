import type { DocumentController } from "./document-controller";

// Shared identity registry without importing editor, React or network code.
export const documentControllers = new Map<string, DocumentController>();
