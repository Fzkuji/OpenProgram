import { DocumentController } from "../lib/files/document-controller";
import { IndexedDbDocumentDraftStore } from "../lib/files/file-draft-store";
import * as documentDraftLifecycle from "../lib/files/file-drafts";
Object.assign(window, { DocumentController, IndexedDbDocumentDraftStore, documentDraftLifecycle });
