#!/usr/bin/env python3
"""Lead Scout: score sales leads with an LLM (or an offline heuristic), then
build a weekly digest with a weighted pipeline forecast.

Usage:
    python lead_scout.py leads_sample.csv --today 2026-11-24
    GEMINI_API_KEY=... python lead_scout.py leads.csv          # LLM scoring
    python lead_scout.py leads.csv --slack-webhook https://...  # also post digest

Outputs (in --out, default ./output): scored_leads.csv, weekly_digest.md
Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path

STAGE_BASE = {"lead": 25, "qualified": 45, "proposal": 55, "negotiation": 70}
STALE_DAYS = 14
HOT_WORDS = ("urgent", "ready to sign", "budget approved", "budget confirmed", "asked for a call", "samples")
COLD_WORDS = ("no budget", "just browsing", "just exploring", "no timeline", "not replied", "has not replied", "no further", "maybe next month")

SYSTEM_PROMPT = (
    "You are a B2B packaging sales analyst. Score the lead from 0-100 for likelihood "
    "to close in the next 60 days. Reply ONLY with JSON: "
    '{"score": int, "reason": "<one sentence>", "next_action": "<one short action>"}'
)


@dataclass
class ScoredLead:
    id: str
    company: str
    stage: str
    deal_value_aed: float
    days_since_contact: int
    score: int
    reason: str
    next_action: str
    scorer: str
    weighted_value_aed: float


def load_leads(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    required = {"id", "company", "stage", "deal_value_aed", "last_contact", "notes"}
    missing = required - set(rows[0].keys()) if rows else required
    if missing:
        raise ValueError(f"CSV missing columns: {sorted(missing)}")
    return rows


def days_since(last_contact: str, today: date) -> int:
    return (today - datetime.strptime(last_contact, "%Y-%m-%d").date()).days


def heuristic_score(lead: dict, age_days: int) -> tuple[int, str, str]:
    """Offline fallback. Transparent rules so the score can be explained."""
    notes = lead["notes"].lower()
    score = STAGE_BASE.get(lead["stage"], 40)
    reasons = []
    hot = [w for w in HOT_WORDS if w in notes]
    cold = [w for w in COLD_WORDS if w in notes]
    score += 12 * len(hot) - 15 * len(cold)
    if hot:
        reasons.append(f"buying signals: {', '.join(hot)}")
    if cold:
        reasons.append(f"weak signals: {', '.join(cold)}")
    if age_days <= 3:
        score += 10
        reasons.append("contacted in last 3 days")
    elif age_days > STALE_DAYS:
        score -= 15
        reasons.append(f"no contact for {age_days} days")
    score = max(0, min(100, score))
    if age_days > STALE_DAYS:
        action = "Re-engage today with a short follow-up"
    elif lead["stage"] == "negotiation":
        action = "Close: confirm final price and send contract"
    elif lead["stage"] == "proposal":
        action = "Follow up on proposal and ask for a decision date"
    else:
        action = "Book a discovery call"
    return score, "; ".join(reasons) or "no strong signals", action


def parse_llm_json(text: str) -> dict:
    """Extract the JSON object even if the model wraps it in markdown fences."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON in LLM reply")
    data = json.loads(match.group(0))
    data["score"] = int(max(0, min(100, int(data["score"]))))
    return {"score": data["score"], "reason": str(data["reason"]), "next_action": str(data["next_action"])}


def llm_score(lead: dict, age_days: int, api_key: str, model: str) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    prompt = (
        f"Company: {lead['company']}\nIndustry: {lead.get('industry', '')}\nStage: {lead['stage']}\n"
        f"Deal value (AED): {lead['deal_value_aed']}\nDays since last contact: {age_days}\nNotes: {lead['notes']}"
    )
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    return parse_llm_json(payload["candidates"][0]["content"]["parts"][0]["text"])


def score_leads(rows: list[dict], today: date, api_key: str | None, model: str) -> list[ScoredLead]:
    out = []
    for lead in rows:
        age = days_since(lead["last_contact"], today)
        scorer = "heuristic"
        result = None
        if api_key:
            try:
                result = llm_score(lead, age, api_key, model)
                scorer = f"llm:{model}"
            except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError) as e:
                print(f"[warn] LLM failed for {lead['company']} ({e}); using heuristic", file=sys.stderr)
        if result is None:
            s, r, a = heuristic_score(lead, age)
            result = {"score": s, "reason": r, "next_action": a}
        value = float(lead["deal_value_aed"])
        out.append(ScoredLead(
            id=lead["id"], company=lead["company"], stage=lead["stage"], deal_value_aed=value,
            days_since_contact=age, score=result["score"], reason=result["reason"],
            next_action=result["next_action"], scorer=scorer,
            weighted_value_aed=round(value * result["score"] / 100, 2),
        ))
    return sorted(out, key=lambda x: x.weighted_value_aed, reverse=True)


def build_digest(leads: list[ScoredLead], today: date) -> str:
    total = sum(l.deal_value_aed for l in leads)
    weighted = sum(l.weighted_value_aed for l in leads)
    stale = [l for l in leads if l.days_since_contact > STALE_DAYS]
    lines = [
        f"# Weekly Sales Digest: {today.isoformat()}", "",
        f"- Leads in pipeline: **{len(leads)}**",
        f"- Raw pipeline value: **AED {total:,.0f}**",
        f"- Score-weighted forecast: **AED {weighted:,.0f}**", "",
        "## Pipeline by stage", "", "| Stage | Leads | Raw value (AED) | Weighted (AED) |", "|---|---|---|---|",
    ]
    for stage in STAGE_BASE:
        grp = [l for l in leads if l.stage == stage]
        if grp:
            lines.append(f"| {stage} | {len(grp)} | {sum(l.deal_value_aed for l in grp):,.0f} | {sum(l.weighted_value_aed for l in grp):,.0f} |")
    lines += ["", "## Top 5 to work this week", ""]
    for i, l in enumerate(leads[:5], 1):
        lines.append(f"{i}. **{l.company}** ({l.stage}, score {l.score}, weighted AED {l.weighted_value_aed:,.0f}): {l.next_action}. _{l.reason}_")
    lines += ["", f"## Gone quiet (>{STALE_DAYS} days)", ""]
    lines += [f"- {l.company}: {l.days_since_contact} days, AED {l.deal_value_aed:,.0f}" for l in stale] or ["- none"]
    return "\n".join(lines) + "\n"


def post_slack(webhook: str, text: str) -> None:
    req = urllib.request.Request(webhook, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv", type=Path)
    p.add_argument("--out", type=Path, default=Path("output"))
    p.add_argument("--today", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(), default=date.today())
    p.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"))
    p.add_argument("--slack-webhook", default=os.environ.get("SLACK_WEBHOOK_URL"))
    args = p.parse_args(argv)

    leads = score_leads(load_leads(args.csv), args.today, os.environ.get("GEMINI_API_KEY"), args.model)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "scored_leads.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(leads[0]).keys()))
        w.writeheader()
        w.writerows(asdict(l) for l in leads)
    digest = build_digest(leads, args.today)
    (args.out / "weekly_digest.md").write_text(digest, encoding="utf-8")
    if args.slack_webhook:
        post_slack(args.slack_webhook, digest)
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
