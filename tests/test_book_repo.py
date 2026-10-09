from repositories.book_repo import _or_query, concept_weight


def test_concept_weight_rare_beats_common_and_never_negative():
    total = 750
    assert concept_weight(total, 5) > concept_weight(total, 50) > concept_weight(total, 400)
    assert concept_weight(total, total) > 0


def test_or_query_quotes_phrases_and_normalizes_yo():
    assert _or_query(["лёгкий бег", "Л-темп", 'зона "1"', " "]) == '"легкий бег" or Л-темп or "зона  1"'
