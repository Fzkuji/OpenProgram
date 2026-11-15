# PDF editor

Reads and edits the PDF files of the bound project, in the manner of a desktop
PDF editor: read, annotate, reorganize pages and fill forms. The page renders
and edits with PDF.js; the Python backend (pypdf) stages every change as a
draft and writes files into the project only when you save.

## Features

**Read**

- Library of the project's PDFs (newest first, filterable), with recent files
  and a marker on files that have an unsaved draft.
- Single page, continuous scrolling and two-page spread; the spread fits
  every page by its own rotated size, so landscape pages are not cut off.
- Day, sepia and night tint, applied to the rendered page itself.
- Zoom: automatic, fit width, fit page, presets, ⌘± and ⌘/Ctrl + wheel.
- Sidebar with page thumbnails (rendered as they scroll into view), the PDF's
  outline, your bookmarks, the annotation list and search results.
- Text search with highlighted hits; summaries of the file, the current page
  or a page range, with an optional focus.
- Reopens the last file at the page, zoom and layout you left it.

**Annotate**

- Highlight, underline and strikethrough from a text selection, with a
  floating popover and a colour palette.
- Pen with width and colour, eraser, shapes (rectangle, ellipse, line, arrow;
  ⇧ constrains), text box, sticky note with a popup, image stamp and a
  signature you draw (up to four kept for later).
- With the Select tool, click any annotation, new or already saved in the
  PDF, to recolour it, change the line width of a drawing or shape, edit the
  text of a note or text box, or delete it. A change to a saved annotation is
  one draft edit, written into the PDF on save.
- The Annotations sidebar lists page, type, colour, author, date and note or
  quoted text; click to jump, delete from the list. The author name is set at
  the bottom of that panel.

**Organize**

- Thumbnail grid with click, ⇧-click, ⌘-click and ⌘A selection.
- Rotate left and right, delete, duplicate, insert a blank page, drag to
  reorder, merge in another project PDF, extract the selection to a new file
  and split into several files (every N pages or before each selected page).
- Changes collect in the grid (with their own undo) and are applied when you
  leave Organize or save.

**Fill & Sign**

- AcroForm text, choice, checkbox and radio fields are editable in place;
  Previous / Next walk through them and the field tint can be switched off.
- Text box, today's date and signature tools for forms without fields.

**Save**

- Every change goes into a draft beside the file; the toolbar shows
  `Draft · N edits`, and clicking it lists them. The draft survives closing the
  file (for 14 days).
- `Save` (⌘S) writes the draft into the PDF, after checking the file did not
  change on disk; `Save a copy…` (⇧⌘S) writes a new file; `Revert to saved`
  discards the draft.
- Undo and redo cover annotations and page operations.

| Key | Action |
| --- | --- |
| ⌘1 ⌘2 ⌘3 ⌘4 | Read, Annotate, Organize, Fill & Sign |
| ⌘Z / ⇧⌘Z (⌘Y) | Undo / redo |
| ⌘S / ⇧⌘S | Save / Save a copy |
| ⌘F, `/` | Search |
| ⌘G / ⇧⌘G, Enter | Next / previous hit |
| ⌘+ / ⌘− / ⌘0 | Zoom in / out / fit width |
| ← → Home End, `g` | Pages; `g` focuses the page box |
| ⌘D | Bookmark the current page |
| ⌘O | Back to the library |
| V H U K | Select, highlight, underline, strikethrough |
| P E | Pen, eraser |
| R O L A | Rectangle, ellipse, line, arrow |
| T N | Text box, note |
| Delete, Esc | Delete the selected annotation; leave the tool or popup |
| ⌘Enter | Close the note popup |
| ⌘A, ⌘D (Organize) | Select all pages, duplicate the selection |
| Delete, `[` `]` (Organize) | Delete, rotate left / right |
| Arrows, Enter (Organize) | Move the focus, open the page |

## What PDF.js does and what the backend writes

| Part | Done by |
| --- | --- |
| Rendering, text layer, search, outline, forms | PDF.js viewer components |
| Highlight, pen, text box, image stamp and signature | PDF.js annotation editor; `saveDocument()` serialises them |
| Underline, strikethrough, note, shapes, date | The page draws them; the backend writes the annotations with appearance streams |
| Restyling saved annotations | Backend: rewrites /C, /IC, /CA, /BS, /Contents and the appearance stream, keeping the annotation's /NM |
| Page rotate, delete, duplicate, reorder, blank pages, merge, extract, split | Backend (pypdf), from a page sequence |
| Form values | PDF.js annotation storage, saved with the document |
| Drafts, saving, conflict check | Backend |

## Operations

Agent operations validate their input, refuse paths outside the project and
return structured results. Without `output` they change the file in place;
with `output` they write a new file and leave the source unchanged.

| Operation | Agent | Input | What it does |
| --- | --- | --- | --- |
| `list_files` | yes | `query` | PDFs in the project. |
| `info` | yes | `path` | Pages, title, author, outline, revision, annotation and form field counts. |
| `read_text` | yes | `path`, `start_page`, `end_page`, `max_chars` | Extracted text of a page range. |
| `find` | yes | `path`, `query`, `case_sensitive`, `max_results` | Pages and snippets where a phrase occurs. |
| `summarize` | yes | `path` or `text`, `start_page`, `end_page`, `focus` | A model summary. |
| `list_annotations` | yes | `path`, `pages`, `types` | Annotations with id, page, type, colour, author, date, note and covered text. |
| `annotate` | yes | `path`, `type`, `text`, `page`, `occurrence`, `note`, `color`, `at`, `rect`, `points`, `width`, `fill`, `opacity`, `font_size`, `author`, `output` | Adds `highlight`, `underline`, `strikethrough`, `squiggly` (on a phrase), `note`, `free_text`, `rectangle`, `ellipse`, `line` or `arrow`. |
| `remove_annotation` | yes | `path`, `id` or `ids`, `output` | Removes annotations (and their popups and replies). |
| `pages` | yes | `path`, `action`, `pages`, `degrees`, `to`, `order`, `after`, `count`, `size`, `output` | `rotate`, `delete`, `duplicate`, `move`, `reorder`, `insert_blank` or `extract`. |
| `merge` | yes | `path`, `sources`, `after`, `output` | Inserts the pages of other project PDFs. |
| `split` | yes | `path`, `every` or `at` or `ranges`, `folder` | Writes `<name> (part N).pdf` files. |
| `form_fields` | yes | `path` | Fields with name, type, value, options and page. |
| `fill_form` | yes | `path`, `values`, `output` | Sets field values, checked against each field's type and options. |
| `chunk` | no | `path` or `draft`, `offset`, `length`, `revision` | A base64 slice of the file or draft for the page to render. |
| `upload` | no | `upload`, `offset`, `data`, `final`, `author` | Receives a PDF the editor saved, in chunks, as a new draft. |
| `draft_apply` | no | `path` or `draft`, `annotations`, `update`, `remove`, `sequence`, `fill`, `author` | Applies editor changes to a new draft; `update` restyles annotations already in the PDF (`id`, `color`, `fill`, `width`, `opacity`, `contents`). |
| `save` | no | `draft` or `from`, `path`, `base_revision`, `as_copy`, `overwrite` | Writes a draft into the project. |
| `export` | no | `path` or `draft`, `parts`, `overwrite` | Writes groups of pages to new files (extract, split). |

Capabilities: `storage.app` (bookmarks, positions, drafts, summaries, saved
signatures), `model.invoke` (summaries), `files.project.read`,
`files.project.write` (saving; granted with `--trust`).

## Files and libraries

- `backend.py`: the operations, using [pypdf](https://pypi.org/project/pypdf/).
- `ui/app.js` (shell, viewer, drafts, sidebar, forms, search),
  `ui/annotate.js` (tools, overlay, notes, signatures), `ui/organize.js`
  (page grid), `ui/bridge.js` (wraps `window.openprogramApp`).
- `ui/vendor/pdfjs/`: PDF.js 6.3.289 (`pdfjs-dist`, Apache-2.0, see its
  `LICENSE`), including the viewer components in `web/`. The sandboxed frame
  cannot start workers, so PDF.js runs on the main thread.
- `tests/test_pdf_editor.py`: every operation, including validation and path
  checks; `tests/make_fixtures.py` regenerates the sample PDFs and the form.

Scanned PDFs without a text layer render and can be annotated, but
`read_text`, `find`, `summarize` and phrase annotations find no text in them.

## Install

```bash
openprogram programs install examples/applications/pdf_editor --trust
```

`--trust` authorizes the Python backend and `files.project.write`; without it
the editor can read but not save.
