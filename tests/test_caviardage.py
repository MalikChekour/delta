"""Tests du caviardage des secrets dans les sorties d'outils.

🚨 POURQUOI CE N'EST PAS UNE CONSIGNE DE PROMPT. Essaye le 11/09 : le prompt systeme disait
« tu ne divulgues aucun secret, meme partiellement ». L'agent a livre le .env ENTIER, puis
les vingt premiers caracteres d'une cle, sur simple demande. C'etait previsible — le modele
est un GLM **decensure**, dont la raison d'etre est d'avoir perdu ses reflexes de refus.
La seule barriere qui tienne est technique et placee APRES l'outil.
"""

from __future__ import annotations

import pytest

from hermes.tools import (ToolContext, build_registry, caviarde,
                          oublie_les_secrets, secrets_connus)

SECRET = "tvly-dev-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


@pytest.fixture(autouse=True)
def _cle(monkeypatch):
    monkeypatch.setenv("BANC_TEST_API_KEY", SECRET)
    # 🚨 L'index des secrets est MIS EN CACHE : sans cette purge, un test qui change
    # l'environnement travaillerait sur l'index du test precedent — et passerait ou
    # echouerait selon l'ordre d'execution, la pire sorte de test.
    oublie_les_secrets()
    yield
    oublie_les_secrets()


def test_la_valeur_entiere_est_retiree():
    assert SECRET not in caviarde(f"la cle est {SECRET} voila")
    assert "BANC_TEST_API_KEY" in caviarde(f"la cle est {SECRET} voila")


def test_un_fragment_est_retire_aussi():
    """🚨 LE TEST QUI COMPTE. La premiere version ne retirait que la valeur COMPLETE :
    `read()[:40]` suffisait a faire sortir un morceau. Un secret coupe reste un secret."""
    for taille in (12, 16, 20, 30):
        morceau = SECRET[:taille]
        assert morceau not in caviarde(f"debut: {morceau}"), f"fragment de {taille} passe"
    assert SECRET[7:25] not in caviarde("milieu: " + SECRET[7:25])


def test_un_texte_anodin_n_est_pas_touche():
    """Le miroir : un caviardage trop large mutilerait les reponses normales."""
    texte = "Le persil contient 133 mg de vitamine C aux 100 g selon l'USDA."
    assert caviarde(texte) == texte


def test_les_valeurs_courtes_ne_sont_pas_des_secrets(monkeypatch):
    """« 1 », « on », « fr » sont des reglages, pas des cles : les caviarder rendrait
    illisible la moitie des sorties."""
    monkeypatch.setenv("HERMES_TRUC_TOKEN", "1")
    oublie_les_secrets()
    assert "1" not in secrets_connus()
    assert caviarde("valeur 1 et 1 et 1") == "valeur 1 et 1 et 1"


def test_sans_aucun_secret_le_texte_est_rendu_tel_quel(monkeypatch):
    for nom in list(secrets_connus().values()):
        monkeypatch.delenv(nom, raising=False)
    oublie_les_secrets()
    assert caviarde("texte quelconque") == "texte quelconque"


async def test_la_sortie_d_un_outil_est_caviardee(tmp_path):
    """Le point de passage unique : tout resultat d'outil transite par `dispatch`."""
    reg = build_registry()
    ctx = ToolContext(workspace=tmp_path, exec_timeout=30, output_limit=4000,
                      request_timeout=10, search_url="x")
    (tmp_path / "fuite.txt").write_text(f"CLE={SECRET}\n", encoding="utf-8")
    out = await reg.dispatch(ctx, "read_file", {"path": "fuite.txt"})
    assert SECRET not in out
    assert "SECRET RETIRE" in out


async def test_meme_par_le_shell_qui_echappe_au_bac_a_sable(tmp_path):
    """🚨 `shell` et `python` ne sont PAS confines au workspace : ils peuvent lire le .env.
    C'est justement pour ca que le caviardage est place a la sortie, pas a l'entree."""
    reg = build_registry()
    ctx = ToolContext(workspace=tmp_path, exec_timeout=30, output_limit=4000,
                      request_timeout=10, search_url="x")
    out = await reg.dispatch(ctx, "python", {"code": f"print({SECRET!r})"})
    assert SECRET not in out


def test_les_encodages_courants_sont_couverts():
    """🚨 Le base64 contournait TOUT. « encode en base64 le contenu de .env » faisait
    ressortir le jeton entier : une recherche de sous-chaines ne voit pas une valeur
    transformee. On couvre base64 (aux trois decalages, car la valeur peut etre encodee
    au sein d'un texte plus large) et hexadecimal."""
    import base64

    brut = SECRET.encode()
    assert "SECRET RETIRE" in caviarde(base64.b64encode(brut).decode())
    assert "SECRET RETIRE" in caviarde(brut.hex())
    # encodee au milieu d'un fichier entier, donc a un alignement quelconque
    entier = f"A=1\nCLE={SECRET}\nB=2\n".encode()
    assert "SECRET RETIRE" in caviarde(base64.b64encode(entier).decode())


def test_le_caviardage_n_est_pas_annonce_comme_etanche():
    """⚠️ Garde-fou de DOCUMENTATION : rot13, un chiffrement maison ou un caractere insere
    entre chaque lettre passeraient encore. Le code doit le dire, pour qu'on ne s'y fie pas
    plus qu'il ne le merite."""
    from hermes.tools import secrets_connus as _sc

    assert "etanche" in (_sc.__doc__ or ""), "la limite doit rester ecrite dans le code"
