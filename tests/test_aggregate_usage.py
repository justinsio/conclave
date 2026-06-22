from scripts.aggregate_usage import aggregate, parse_lines


def test_aggregate_means_and_cost_by_purpose():
    records = [
        {"event": "llm_usage", "purpose": "answer", "prompt_tokens": 800, "completion_tokens": 100},
        {"event": "llm_usage", "purpose": "answer", "prompt_tokens": 1000, "completion_tokens": 200},
        {"event": "llm_usage", "purpose": "discussion_draft", "prompt_tokens": 2000, "completion_tokens": 500},
    ]
    out = aggregate(records, input_rate=0.000001, output_rate=0.000002)
    ans = out["by_purpose"]["answer"]
    assert ans["count"] == 2
    assert ans["mean_prompt_tokens"] == 900
    assert ans["mean_completion_tokens"] == 150
    assert ans["mean_total_tokens"] == 1050
    assert abs(ans["cost_usd"] - (1800 * 0.000001 + 300 * 0.000002)) < 1e-12
    dd = out["by_purpose"]["discussion_draft"]
    assert dd["count"] == 1
    expected_total = (1800 * 0.000001 + 300 * 0.000002) + (2000 * 0.000001 + 500 * 0.000002)
    assert abs(out["total_cost_usd"] - expected_total) < 1e-12


def test_aggregate_ignores_non_usage_events():
    records = [{"event": "other"},
               {"event": "llm_usage", "purpose": "answer", "prompt_tokens": 10, "completion_tokens": 5}]
    out = aggregate(records, 0.0, 0.0)
    assert set(out["by_purpose"]) == {"answer"}


def test_parse_lines_tolerates_log_prefix_and_junk():
    lines = [
        '2026-06-22 [coding] INFO seed.usage: {"event":"llm_usage","purpose":"answer","prompt_tokens":5,"completion_tokens":3}',
        'not json at all',
        '',
    ]
    recs = parse_lines(lines)
    assert len(recs) == 1
    assert recs[0]["completion_tokens"] == 3
