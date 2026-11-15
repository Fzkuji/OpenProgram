import { DocumentController } from "../../lib/files/document/document-controller";
import { IndexedDbDocumentDraftStore } from "../../lib/files/drafts/file-draft-store";
import * as documentDraftLifecycle from "../../lib/files/drafts/file-drafts";
Object.assign(window, { DocumentController, IndexedDbDocumentDraftStore, documentDraftLifecycle });
