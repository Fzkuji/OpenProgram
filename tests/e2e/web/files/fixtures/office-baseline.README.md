# Local Office acceptance documents

The DOCX, PPTX and XLSX fixtures derive from `public/fixtures/office/local.*`
in onlyoffice-browser commit `d15d12b6945be4d8b0f3aa1806120e740d2950ee`
(AGPL-3.0). Each contains one Chinese baseline text substitution used to
check actual native rendering and OOXML export. No user documents are included.

Tests copy these immutable inputs into a temporary project before editing.

The `office-legacy.doc`, `.ppt` and `.xls` files are unchanged inputs from
`public/fixtures/legacy/legacy.*` in the same upstream commit and license.

The ODT, ODP and ODS baselines are unchanged `public/fixtures/office/local.*`
inputs from the same upstream revision.
