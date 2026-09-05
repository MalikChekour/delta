from hermes.bot import _chunks


def test_short_text_is_one_chunk():
    assert _chunks("court") == ["court"]


def test_split_respects_line_boundaries():
    text = "\n".join(f"ligne {i}" for i in range(2000))
    parts = _chunks(text, size=200)
    assert all(len(p) <= 200 for p in parts)
    assert "".join(parts) == text


def test_oversized_single_line_is_hard_split():
    parts = _chunks("A" * 5000, size=1000)
    assert len(parts) == 5
    assert "".join(parts) == "A" * 5000
