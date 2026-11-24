# Lead Scout

Python automation that scores sales leads and turns them into a weekly digest with a score-weighted revenue forecast.

**Flow:** CSV of leads -> LLM scores each lead (0-100, reason, next action) -> weighted pipeline value (deal value x score) -> `scored_leads.csv` + `weekly_digest.md` -> optional Slack post.

- **LLM scoring:** Gemini API via REST (set `GEMINI_API_KEY`; model via `GEMINI_MODEL`). Replies are forced to JSON, parsed defensively and clamped to 0-100.
- **Fallback:** if there is no key or a call fails, a transparent rule-based scorer runs, so the job never breaks.
- **Finance view:** raw vs weighted pipeline by stage, plus leads gone quiet for more than 14 days.
- **Standard library only.** 7 pytest tests.

```
python lead_scout.py leads_sample.csv --today 2026-11-24
GEMINI_API_KEY=... python lead_scout.py leads.csv --slack-webhook https://hooks.slack.com/...
pytest
```

Sample data is fictional. Schedule it weekly with cron or GitHub Actions.
