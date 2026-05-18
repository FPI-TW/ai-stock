from app.repositories.symbol_repository import escape_like_pattern


def test_escape_like_pattern_backslash() -> None:
    assert escape_like_pattern("a\\b") == "a\\\\b"


def test_escape_like_pattern_percent() -> None:
    assert escape_like_pattern("100%") == "100\\%"


def test_escape_like_pattern_underscore() -> None:
    assert escape_like_pattern("a_b") == "a\\_b"


def test_escape_like_pattern_combined() -> None:
    # 順序敏感：先 escape \，再 escape % 與 _，避免把後者新加的 \ 再次 escape
    assert escape_like_pattern("a\\b_c%d") == "a\\\\b\\_c\\%d"


def test_escape_like_pattern_no_wildcards() -> None:
    assert escape_like_pattern("2330") == "2330"


def test_escape_like_pattern_empty() -> None:
    assert escape_like_pattern("") == ""
