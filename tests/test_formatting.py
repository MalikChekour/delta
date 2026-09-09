"""Chaque morceau envoye a Telegram doit etre du HTML complet : c'est la seule
facon d'eviter les HTTP 400 sur du Markdown malforme par le modele."""

from __future__ import annotations

import re

from hermes.formatting import CHUNK_LIMIT, chunks, plain

BALISE = re.compile(r"</?(b|i|s|u|code|pre|a)\b[^>]*>")


def balises_equilibrees(html: str) -> bool:
    pile: list[str] = []
    for match in BALISE.finditer(html):
        nom = match.group(1)
        if match.group(0).startswith("</"):
            if not pile or pile.pop() != nom:
                return False
        else:
            pile.append(nom)
    return not pile


def test_conversion_de_base():
    out = chunks("**gras** et *italique* et `code`")[0]
    assert "<b>gras</b>" in out and "<i>italique</i>" in out and "<code>code</code>" in out


def test_html_du_modele_est_echappe():
    out = chunks("regarde <script>alert(1)</script> & co")[0]
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_bloc_de_code_ferme():
    out = chunks("avant\n```python\nx = 1 < 2\n```\napres")[0]
    assert "<pre><code>" in out and "x = 1 &lt; 2" in out
    assert balises_equilibrees(out)


def test_bloc_de_code_non_ferme_par_le_modele():
    out = chunks("texte\n```\nprint('oups')")[0]
    assert balises_equilibrees(out)


def test_asterisque_isolee_ne_casse_rien():
    out = chunks("5 * 3 = 15 et un _ tout seul")[0]
    assert balises_equilibrees(out)


def test_decoupage_produit_des_morceaux_valides():
    code = "\n".join(f"ligne {i} " + "y" * 90 for i in range(300))
    out = chunks(f"intro\n```\n{code}\n```\nfin")
    assert len(out) > 1
    for part in out:
        assert len(part) <= CHUNK_LIMIT
        assert balises_equilibrees(part)
        assert part.count("<pre>") == part.count("</pre>")


def test_ligne_unique_demesuree():
    out = chunks("x" * 20000)
    assert out and all(len(part) <= CHUNK_LIMIT for part in out)


def test_lien_markdown():
    out = chunks("voir [ici](https://exemple.test/a?b=1&c=2)")[0]
    assert '<a href="https://exemple.test/a?b=1&amp;c=2">ici</a>' in out


def test_repli_texte_brut():
    assert plain("abc", limit=2) == ["ab", "c"]
