# Task B implementation evidence

The shared `DocumentController` owns a stable project or captured attachment identity, keeps Blob drafts outside React panes, publishes authenticated document bytes with baseline revision and idempotency headers, retries one uncertain response with the same request, and exposes paginated history, immutable history content, and restore operations. `DocumentWindow` is the common Preview/Edit/History surface used by project file tabs and read-only attachment previews.

RED: before implementation, importing `apps/web/tests/document-controller.test.mjs` failed because `lib/state/document-controller.ts` did not exist.

GREEN: `npx tsc --noEmit --pretty false -p apps/web/tsconfig.json`; `npm run check:file-cache-drafts --workspace apps/web`; `node --no-warnings --experimental-strip-types --test apps/web/tests/document-controller.test.mjs` (3 pass); `npm run test:unit --workspace apps/web` (648 pass); `git diff --check`.

The controller now persists Blob records with the original baseline revision and generation before publication, restores them on a new controller, serializes newer generations, and refuses close when flush fails. History content is rendered through `FileViewer` with an immutable snapshot. A real browser rendered interaction suite and installed App acceptance remain outstanding; format engine adapters are excluded from this task.
