# Search Console Verification Design

## Goal

Keep OpenProgram's build-generated sitemap as the canonical sitemap and publish Google's ownership proof at the exact domain-root URL required by Search Console.

## Design

- `tools/docs_site/build.py` remains the only sitemap generator. Its current output contains all built English and Chinese documentation pages plus the landing page.
- `docs/_static_root/google01b0015fda12129e.html` stores the ownership proof in source control.
- The publish workflow promotes the proof from the documentation build output to `_publish/`, alongside `CNAME`, `robots.txt`, and `sitemap.xml`, so the deployed URL is `/google01b0015fda12129e.html` rather than `/docs/google01b0015fda12129e.html`.
- The workflow checks the assembled proof contents before publishing.
- The downloaded `sitemap (1).xml` is not copied because it is a 300-URL crawler snapshot; the repository generator currently emits 443 URLs and updates on every documentation build.

## Verification

Run the documentation build with the production origin and base path, reproduce the workflow assembly locally, and verify:

- the ownership proof exists at the assembled root with the exact Google token;
- `sitemap.xml` is at the assembled root and contains every page from the current build;
- `robots.txt` references `https://openprogram.io/sitemap.xml`;
- documentation links still pass the existing checker.
