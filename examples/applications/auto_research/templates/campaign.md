# Campaign
Approved: no
Goal: What this campaign should achieve for the paper.
Tasks: watch, research_map
Agent work: Continue the experiment loop per workflow/experiment.md.
Every: 0 */6 * * *
Budget: 20
Until:

Goal is what the campaign is for. Tasks lists the Auto Research tasks every step
runs, comma separated, in order. Agent work says what the unattended agent may do
after them; it changes only the files that workflow/experiment.md lets it edit.
Every is a five-field cron expression. Budget is the most the campaign may spend,
in USD, counted from its start. Until is the last day it runs (YYYY-MM-DD); empty
runs until the budget is spent. The author approves this spec in the app before
the campaign starts.
