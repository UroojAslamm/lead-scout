from datetime import date
import lead_scout as ls

TODAY = date(2026, 11, 24)

def lead(stage="qualified", notes="", last="2026-11-20", value="1000"):
    return {"id": "1", "company": "X", "stage": stage, "deal_value_aed": value, "last_contact": last, "notes": notes}

def test_days_since():
    assert ls.days_since("2026-11-14", TODAY) == 10

def test_hot_beats_cold():
    hot, *_ = ls.heuristic_score(lead(notes="Budget approved. Urgent."), 4)
    cold, *_ = ls.heuristic_score(lead(notes="Just browsing, no timeline."), 4)
    assert hot > cold

def test_stale_penalised():
    fresh, *_ = ls.heuristic_score(lead(), 2)
    stale, _, action = ls.heuristic_score(lead(), 30)
    assert stale < fresh and "Re-engage" in action

def test_score_bounded():
    s, *_ = ls.heuristic_score(lead(stage="negotiation", notes="urgent ready to sign budget approved budget confirmed samples"), 1)
    assert 0 <= s <= 100

def test_parse_llm_json_handles_fences_and_clamps():
    out = ls.parse_llm_json('```json\n{"score": 140, "reason": "r", "next_action": "a"}\n```')
    assert out["score"] == 100

def test_weighted_value_and_sort():
    rows = [lead(stage="lead", value="1000"), lead(stage="negotiation", notes="ready to sign", value="1000")]
    scored = ls.score_leads(rows, TODAY, None, "m")
    assert scored[0].stage == "negotiation"
    assert scored[0].weighted_value_aed == round(1000 * scored[0].score / 100, 2)

def test_missing_columns_rejected(tmp_path):
    p = tmp_path / "bad.csv"; p.write_text("id,company\n1,A\n")
    try:
        ls.load_leads(p); assert False
    except ValueError:
        pass
