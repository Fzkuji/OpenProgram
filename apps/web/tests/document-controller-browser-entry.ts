import { DocumentController } from "../lib/state/document-controller";
import { IndexedDbDocumentDraftStore } from "../lib/state/file-draft-store";
import * as documentDraftLifecycle from "../lib/state/file-drafts";
Object.assign(window, { DocumentController, IndexedDbDocumentDraftStore, documentDraftLifecycle });
