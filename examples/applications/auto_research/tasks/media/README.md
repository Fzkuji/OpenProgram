# paper_media task

The `paper_media` task of auto_research (`tasks/media/`). It turns a paper into a two-host audio overview, as NotebookLM's audio overviews do, or into a narrated slide video, as NotebookLM's video overviews and Paper2Video do.

| Item | Contract |
| --- | --- |
| Purpose | A listenable or watchable overview of the paper whose every number comes from the paper. |
| Input | `paper_dir`: the paper's Git repository; `workflow/rules.json` names the main file. `kind`: `podcast` (default) or `video`. `minutes`: the target length, 0.5 to 30 (default 5; about 150 words a minute). `voice`: `"A,B"` for the two podcast hosts, one name for the video narrator; empty uses Samantha and Daniel when installed, else the first installed voices. `out_dir`: the output folder (default `<paper_dir>/drafts/paper_media`). A missing repository or main file, another kind, minutes out of range or a voice that is not installed raises `ValueError`; so does a video without a PDF. |
| Slides | `video` narrates the deliverable of `rules.json` whose name mentions slides when that PDF exists, else the compiled paper (`<main>.pdf`), at most two pages a minute. Each page's text is read with pypdf; pages are rendered with `pdftoppm -r 100`. |
| Grounding | Every number in the script with two or more digits or a decimal point must occur in the paper text (and, for a video, the page text); `1{,}000` and `1,000` count as `1000`. A podcast line that breaks this is dropped; for a video the sentence is dropped. Every drop is listed with its numbers. A page left without narration stays on screen for 3 seconds in silence. |
| Output | JSON with `status`, `report` (script.md), `media` (the file, or `""`), `run`, `kind`, `seconds` (by ffprobe, 0 without it) and `dropped` (how many lines or sentences). `FAIL`: nothing left to say. `PASS_WITH_NOTES`: something was dropped, a tool is missing or synthesis failed. `PASS`: the media file was made from the whole script. Files in `<out_dir>/<timestamp>/`: `script.md` (Chinese headings, English script), `media.json` (lines or pages with voice and seconds, drops, notes), `podcast.m4a` (AAC) or `video.mp4` (H.264 yuv420p, AAC), `run.json`, `run.md`. |
| Effects | Reads the LaTeX source and the PDF. Runs `say` once per line or page, one after another, then ffmpeg; every subprocess has a timeout and its exit code is checked. Clips and segments go to a temporary folder inside the output folder that is removed after joining. Without `say` or ffmpeg (or `pdftoppm` for a video) only the script is written, with a note. The paper sources are never modified. |
| Model calls | `scriptwriter`: one call. Podcast: a conversation in which host A explains and host B asks what a smart outsider would, covering problem, idea, method, evidence with the paper's numbers, and limits (schema: lines with speaker `A`/`B`, 4 or more lines scaled to the length). Video: one narration paragraph per page (schema: exactly one entry per page). |
| Failure | A malformed reply, a line without host A or B, one host only, the wrong number of lines, or a page narrated twice or not at all raises. A failing `say` or ffmpeg is a note, not a crash. Rerunning is safe. |
| Tests | `tests/media/test_workflow.py`: both kinds with a scripted role and the synthesis replaced, the number rule, the missing-tool path, the schema checks, and one real `say` + ffmpeg run when the machine has them. |
| Usage | `openprogram programs run auto_research -a paper_dir=<repo> -a task=paper_media -a kind=podcast -a minutes=5`, or `from auto_research.tasks.media import paper_media` |

## Process

1. **Read** (code). The flattened LaTeX, split into sections; for a video the PDF and each page's text.
2. **Script.** The `scriptwriter` writes the conversation or the per-page narration in English.
3. **Ground** (code). Numbers not in the paper drop their line or sentence.
4. **Synthesize** (code). `say` per line with the host's voice, joined into `podcast.m4a`; or `say` per page and one still-image segment per page, concatenated into `video.mp4`.
5. **Report** (code). `script.md`, `media.json` and the run trail.

## Not covered

- Number words ("twelve") and single digits are not checked; claims without numbers are trusted to the instructions.
- Making slides: a video narrates existing slides or the paper's own pages.
- Voices other than macOS `say`.
