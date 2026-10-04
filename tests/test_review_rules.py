"""Rules: the built-in pack, .cerno.toml loading, and path scoping."""

import pytest

from cerno.review.models import ReviewRule
from cerno.review.rules import BUILTIN, RulesError, load_rules, matches

BUILTIN_IDS = {"CERNO-CONTRACT", "CERNO-AUTHZ", "CERNO-DATA-EXPOSURE", "CERNO-DESTRUCTIVE", "CERNO-BEHAVIOR"}


def test_builtin_pack_covers_the_contract_ids_and_rules_out_style(repo):
    assert {r.id for r in BUILTIN} == BUILTIN_IDS
    for rule in BUILTIN:
        assert "style" in rule.text and "refactor" in rule.text
        assert "never report" in rule.text.lower()
    assert load_rules(repo).sources == ["builtin"]
    assert load_rules(repo).min_severity == "high"


def write_rules(repo, text: str):
    (repo / ".cerno.toml").write_text(text)
    return repo


def test_file_rules_extend_the_builtin_pack(repo):
    write_rules(repo, """
[review]
min_severity = "medium"
disable = ["CERNO-BEHAVIOR"]

[[rule]]
id = "PAY-001"
text = "Refunds must never exceed the captured amount."
severity = "critical"
paths = ["src/payments/**"]
""")
    rules = load_rules(repo)
    assert rules.min_severity == "medium"
    assert rules.sources == ["builtin", ".cerno.toml"]
    ids = [r.id for r in rules.rules]
    assert "CERNO-BEHAVIOR" not in ids and ids[-1] == "PAY-001"
    pay = rules.rules[-1]
    assert (pay.severity, pay.source, pay.paths) == ("critical", "file", ["src/payments/**"])


def test_builtin_false_keeps_only_file_rules(repo):
    write_rules(repo, """
[review]
builtin = false

[[rule]]
id = "X-1"
text = "Something project specific."
""")
    rules = load_rules(repo)
    assert [r.id for r in rules.rules] == ["X-1"]
    assert rules.rules[0].severity == "high" and rules.sources == [".cerno.toml"]


def test_paths_scope_a_rule_to_matching_changed_files():
    rule = ReviewRule(id="PAY-001", text="t", paths=["src/payments/**"])
    assert matches(rule, ["src/payments/refund.py"])
    assert matches(rule, ["src/payments/api/v2/refund.py"])
    assert not matches(rule, ["src/auth/session.py"])
    assert matches(ReviewRule(id="A", text="t"), ["anything.py"])


@pytest.mark.parametrize("body, needle", [
    ('[review]\nmin_severity = "urgent"\n', "'severity' must be one of"),
    ('[review]\nbuiltin = "yes"\n', "'builtin' must be true or false"),
    ('[review]\ndisable = "CERNO-AUTHZ"\n', "'disable' must be a list of strings"),
    ('[[rule]]\ntext = "no id here"\n', "missing required key 'id'"),
    ('[[rule]]\nid = "A"\n', "missing required key 'text'"),
    ('[[rule]]\nid = "A"\ntext = "t"\nseverity = "blocker"\n', "'severity' must be one of"),
    ('[[rule]]\nid = "A"\ntext = "t"\npaths = "src/**"\n', "'paths' must be a list of strings"),
    ("[review\n", ".cerno.toml"),
])
def test_malformed_rules_name_the_offending_key(repo, body, needle):
    write_rules(repo, body)
    with pytest.raises(RulesError) as exc:
        load_rules(repo)
    assert needle in str(exc.value)


def test_an_explicitly_named_missing_rules_file_is_an_error(repo):
    with pytest.raises(RulesError, match="rules file not found"):
        load_rules(repo, repo / "nope.toml")
