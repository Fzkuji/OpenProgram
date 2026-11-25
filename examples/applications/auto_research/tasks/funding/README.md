# funding tools

The `funding_calls` and `call_requirements` tasks of auto_research (`tasks/funding/`). `funding_calls` finds open funding calls that fit the project, with projects already funded under them as evidence. `call_requirements` turns one call into a checklist whose every item quotes the call, and checks the grant_proposal draft against it.

Learned from Proposia (funding finder and call analysis), with Grants.gov, the EU Funding & Tenders portal, the NSF Award Search and NIH RePORTER as the free sources.

Sources (all free and keyless): Grants.gov `search2` and `fetchOpportunity` (JSON POST through `scouting.common.post`), the EU Funding & Tenders search API (`apiKey=SEDIA`, multipart POST) and topic details (JSON GET), the NSF Awards API (GET through `citations.sources.fetch`) and NIH RePORTER (JSON POST). The EU hosts and call pages are read with a TLS context that adds certifi's roots to the system's, because the EU hosts chain to GlobalSign Root R46, which macOS's `/etc/ssl/cert.pem` lacks. A source that fails is named in the report and makes the result `PASS_WITH_NOTES`.

## funding_calls

| Item | Contract |
| --- | --- |
| Signature | `funding_calls(paper_dir: str, funder: str = "", count: str = "10", out_dir: str = "") -> str` |
| Purpose | The author sees which open calls the project could apply to, why each fits, and what the same programmes already funded. |
| Input | `paper_dir`: the project folder (not a folder raises `ValueError`). `funder`: empty searches every source; `us` (`usa`, `grants`, `grants.gov`) Grants.gov, NSF and NIH; `eu` (`europe`, `ec`) the EU portal; any other text keeps calls whose funder, agency or identifier contains it (`NSF`, `NIH`, `ERC`, `HORIZON`); `nsf` and `nih` search Grants.gov and their own awards only, `erc`, `horizon`, `msca`, `eic`, `digital` and `life` the EU portal only. `count`: calls to keep, 1 to 50 (default 10; outside raises `ValueError`). `out_dir`: default `<paper_dir>/drafts/funding_calls`. No title, abstract or claim raises `ValueError`. |
| Process | Keywords: the `keyworder` gives 1-2 topic phrases (kept only when one word shares its first five letters with the title, abstract or claim) and 2-4 research fields named as programmes name them; at most 4 words each. Search: each phrase on Grants.gov (posted and forecasted, quoted when it has several words) and the EU portal (call topics, open and forthcoming, latest deadline first); NSF Awards and NIH RePORTER (last 3 fiscal years) for funded projects. Funded projects scoring 40 or more on the phrases are similar; the NIH opportunity numbers and NSF programmes of the similar ones are looked up on Grants.gov too. The full text of up to 60 Grants.gov calls is fetched. Filter (code): a closed call, a call whose every deadline is before today, an EU topic with no deadline from today on, and (with `funder` text) a call of another funder are dropped; a forecast without a date is kept and marked. Score (code): fit 0-100 is the mean over the phrases of 1 (in the title), 0.7 (in the description) or up to 0.4 (share of its words found); the TF-IDF cosine of project and call breaks ties. A similar funded project under the same NIH opportunity number or NSF programme is the call's evidence. Rank: the `ranker` judges the best 30 (or `count`) by fit as fit, partial or no, with a Chinese reason and a quote of the call. An id it was not given is a note; "no" removes the call; a fit or partial verdict whose quote (at least 4 words) is not in the call's text is void. Kept: fit, then partial, then unjudged calls with fit 50 or more, each with evidence first, then by fit. |
| Output | JSON with `status`, `report`, `json`, `run`, `csv`, `calls`, `candidates`, `top`, `unavailable_sources`. `FAIL`: no source answered. `PASS_WITH_NOTES`: a source failed, the ranker named an unknown id, or fewer than `count` calls fit. Files in `<out_dir>/<timestamp>/`: `calls.csv` (title, funder, id, deadline, url, fit, why), `calls.json` (calls, phrases, dropped calls by reason, similar funded projects), `report.md`, `run.json`, `run.md`. |
| Effects | Network reads only; the project is never modified. |
| Model calls | `keyworder`: one call. `ranker`: one call when any call is left. |
| Tests | `tests/funding/test_calls.py`: every source faked; past-deadline, closed and other-funder calls dropped; the NIH opportunity followed; NSF programme and NIH opportunity evidence; fit, partial, unjudged order; a void quote; `funder` narrowing; a failing source; dates and HTML. |

## call_requirements

| Item | Contract |
| --- | --- |
| Signature | `call_requirements(paper_dir: str, call: str, out_dir: str = "") -> str` |
| Purpose | Every rule of one call in a checklist the author can trust (each item quotes the call), and the draft measured against the rules code can measure. |
| Input | `paper_dir`: the project folder. `call`: a URL or a local `.pdf`, `.html`, `.htm`, `.txt` or `.md` file. Empty, a missing file or another suffix raises `ValueError`. `out_dir`: default `<paper_dir>/drafts/call_requirements`. |
| Read (code) | A Grants.gov URL (`search-results-detail/<id>`, `oppId=<id>`): the opportunity from `fetchOpportunity` (close date, award ceiling, eligible applicants, description; a close date more than 10 years ahead is Grants.gov's mark for a call open all year), then the programme page it links, the solicitation that page links, and one proposal preparation guide a page points to (Chapter II.D.2, proposal preparation, application guide), three pages at most. An EU topic URL (`topic-details/<ID>`): the topic details (deadlines, description, conditions, support). Any other URL: the page, or the PDF by pypdf. 250k characters go to the model. |
| Extract | The `extractor` lists the requirements: `page_limit`, `word_limit`, `char_limit` (value and the part it applies to), `section` (its name), `eligibility`, `budget`, `deadline` (date), `format`, `other`, each with a verbatim quote. |
| Verify (code) | An item whose quote (3 or more words; case, whitespace, curly quotes and dashes ignored) is not in the text is dropped. A limit takes the unit its quote names. Left to the author: a limit whose value is not a number of the quote (digits or one to twenty in words), a deadline without a date or whose year and day are not in the quote, a section whose name is not in the quote. Items are numbered R1... |
| Check (code) | The newest `drafts/grant_proposal/*/grant.json`, each section as submitted (grant workflow's `plain`). A limit is measured (grant `facts.measure`; pages at about 500 words or 800 字) on the section whose title holds 60% of the named part's words, or on the whole draft when the part is empty or "proposal" / "application"; with no such section it is left to the author. A required section is looked for among the section titles and headings. A deadline is compared with today. Eligibility, budget, format and other go to the author. Without a draft, every item but the deadline goes to the author. |
| Output | JSON with `status`, `report`, `json`, `run`, `checklist`, `items`, `checked`, `failed`, `for_author`. `FAIL`: the call could not be read, the deadline has passed, a limit is exceeded or a required section is missing. `PASS_WITH_NOTES`: items dropped or left to the author, no draft, text cut, or a source failed. Files: `workflow/call_requirements.json` (call, title, URLs read, date, items), and in `<out_dir>/<timestamp>/` `requirements.json`, `report.md`, `run.json`, `run.md`. |
| Effects | Writes `workflow/call_requirements.json` (replaced on each run). The draft is never modified. |
| Model calls | `extractor`: one call. |
| Tests | `tests/funding/test_requirements.py`: a local HTML call; dropped quote; a value not in the quote; page and character limits measured on the matching sections; a heading present and missing; deadline ahead and passed; a Grants.gov URL with its linked programme page and solicitation; an EU topic URL; an unreadable URL; a PDF; units from the quote. |

## Not covered

- Registering the two tasks in the program's task list and app.
- Funders without a free search API (NSFC, DFG, UKRI, private foundations): give their call page to `call_requirements`.
- Checking forms, biosketches, budgets and other documents grant_proposal does not draft; they are listed for the author.
