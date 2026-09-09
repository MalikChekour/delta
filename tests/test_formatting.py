"""Chaque morceau envoye a Telegram doit etre du HTML complet : c'est la seule
facon d'eviter les HTTP 400 sur du Markdown malforme par le modele."""

from __future__ import annotations

import re

from hermes.formatting import CHUNK_LIMIT, bien_formee, chunks, strip_tags

BALISE = re.compile(r"</?(b|i|s|u|code|pre|a)\b[^>]*>")


def balises_equilibrees(html: str) -> bool:
    """Meme regle que Telegram : ni croisement, ni imbrication d'une balise
    dans elle-meme."""
    return bien_formee(html)


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


def test_repli_sans_balise():
    rendu = chunks("**gras** et `code`")[0]
    assert strip_tags(rendu) == "gras et code"


def test_repli_sans_balise_desechappe():
    rendu = chunks("a < b & c")[0]
    assert strip_tags(rendu) == "a < b & c"


def test_balisage_entremele_ne_croise_pas_les_balises():
    """Telegram refuse <s>a<i>b</s>c</i>. Les conversions gras/italique/barre
    etant independantes, un balisage entremele en produisait."""
    for texte in ["~~a *b~~ c*", "**a _b** c_", "# titre **gras**", "*a ~~b* c~~"]:
        for part in chunks(texte):
            assert bien_formee(part), texte


def test_ligne_tordue_repliee_en_texte_nu():
    rendu = chunks("~~a *b~~ c*")[0]
    assert "<s>" not in rendu and "a" in rendu


def test_detection_de_mauvaise_imbrication():
    assert bien_formee("<b>a</b><i>b</i>")
    assert not bien_formee("<s>a<i>b</s>c</i>")
    assert not bien_formee("<b>a<b>b</b></b>")
    assert not bien_formee("<b>a")
