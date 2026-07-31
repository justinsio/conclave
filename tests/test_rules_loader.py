"""Rules file loading. Pure logic, no DB."""
from app.services.rules_loader import load_rules

DEFAULTS = ["built in one", "built in two"]


def test_empty_path_returns_defaults():
    assert load_rules("", DEFAULTS) == DEFAULTS


def test_missing_file_falls_back_to_defaults():
    assert load_rules("no/such/file.txt", DEFAULTS) == DEFAULTS


def test_reads_one_rule_per_line(tmp_path):
    f = tmp_path / "rules.txt"
    f.write_text("first rule\nsecond rule\n", encoding="utf-8")
    assert load_rules(str(f), DEFAULTS) == ["first rule", "second rule"]


def test_skips_comments_and_blank_lines(tmp_path):
    f = tmp_path / "rules.txt"
    f.write_text("# a comment\n\nreal rule\n   \n# another\n", encoding="utf-8")
    assert load_rules(str(f), DEFAULTS) == ["real rule"]


def test_strips_surrounding_whitespace(tmp_path):
    f = tmp_path / "rules.txt"
    f.write_text("   padded rule   \n", encoding="utf-8")
    assert load_rules(str(f), DEFAULTS) == ["padded rule"]


def test_file_with_only_comments_falls_back_to_defaults(tmp_path):
    f = tmp_path / "rules.txt"
    f.write_text("# nothing but comments\n", encoding="utf-8")
    assert load_rules(str(f), DEFAULTS) == DEFAULTS


def test_returned_list_is_a_copy_not_the_defaults_object():
    result = load_rules("", DEFAULTS)
    result.append("mutated")
    assert DEFAULTS == ["built in one", "built in two"]
