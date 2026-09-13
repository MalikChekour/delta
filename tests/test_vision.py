# -*- coding: utf-8 -*-
"""Les YEUX : faire decrire une image par un modele qui voit.

🚨 POURQUOI CE MODULE EXISTE. Mesure du 13/09 sur le catalogue Venice : 79 modeles sur 119
acceptent les images, mais `olafangensan-glm-4.7-flash-heretic` — celui qui fait tourner
l'agent — n'en fait PAS partie. Lui passer une capture ne servirait a rien : il repondrait en
devinant, ce qui est pire qu'un aveu d'aveuglement. Un second modele regarde a sa place.

Les tests montent un faux service de modeles sur un port libre : pas de reseau, et le chemin
de repli d'un modele a l'autre est reellement exerce.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hermes.errors import ToolError
from hermes.tools import ToolContext, build_registry
from hermes.tools import vision


def port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


#: Etat du faux service : quels modeles echouent, et ce qu'on a reellement recu.
SERVICE: dict = {"echouent": set(), "vus": [], "reponse": "Un rond rouge et un bouton vert."}


class FauxModele(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a) -> None:
        pass

    def do_POST(self) -> None:                       # noqa: N802
        taille = int(self.headers.get("Content-Length") or 0)
        recu = json.loads(self.rfile.read(taille) or b"{}")
        modele = recu.get("model", "")
        SERVICE["vus"].append((modele, recu))
        if modele in SERVICE["echouent"]:
            corps = json.dumps({"error": "indisponible"}).encode()
            self.send_response(503)
        else:
            corps = json.dumps({"choices": [{"message": {
                "content": SERVICE["reponse"]}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)


@pytest.fixture()
def service(monkeypatch):
    SERVICE["echouent"] = set()
    SERVICE["vus"] = []
    SERVICE["reponse"] = "Un rond rouge et un bouton vert."
    port = port_libre()
    serveur = ThreadingHTTPServer(("127.0.0.1", port), FauxModele)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    monkeypatch.setattr(vision, "ENDPOINT", f"http://127.0.0.1:{port}/v1/chat/completions")
    monkeypatch.setattr(vision, "_cle", lambda: "cle-de-test")
    try:
        yield SERVICE
    finally:
        serveur.shutdown()
        serveur.server_close()


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 200)
    return ToolContext(workspace=tmp_path, exec_timeout=20, output_limit=4000,
                       request_timeout=10, search_url="x")


def outil(ctx, args):
    return asyncio.run(build_registry().dispatch(ctx, "regarde", args))


def test_l_outil_est_declare() -> None:
    assert "regarde" in build_registry().tools


def test_decrit_une_image_du_workspace(ctx, service) -> None:
    sortie = outil(ctx, {"fichier": "photo.png"})
    assert "rond rouge" in sortie
    assert "photo.png" in sortie


def test_l_image_est_bien_envoyee(ctx, service) -> None:
    """Sans l'image dans la charge, le modele decrirait du vide avec assurance."""
    outil(ctx, {"fichier": "photo.png", "question": "Combien de boutons ?"})
    _modele, recu = service["vus"][-1]
    contenu = recu["messages"][-1]["content"]
    genres = [m.get("type") for m in contenu]
    assert "image_url" in genres
    assert any("base64," in (m.get("image_url") or {}).get("url", "") for m in contenu)
    assert any("Combien de boutons" in m.get("text", "") for m in contenu)


def test_repli_sur_le_second_modele(ctx, service) -> None:
    """Un modele indisponible ne doit pas rendre l'agent aveugle."""
    service["echouent"] = {vision.MODELES[0]}
    sortie = outil(ctx, {"fichier": "photo.png"})
    assert "rond rouge" in sortie
    assert [m for m, _ in service["vus"]] == list(vision.MODELES)


def test_tous_indisponibles_le_dit(ctx, service) -> None:
    service["echouent"] = set(vision.MODELES)
    sortie = outil(ctx, {"fichier": "photo.png"})
    assert "aucun modele n'a pu regarder" in sortie


def test_fichier_absent(ctx, service) -> None:
    assert "n'existe pas" in outil(ctx, {"fichier": "fantome.png"})


def test_pas_de_sortie_du_workspace(ctx, service) -> None:
    """Le nom de fichier ne doit pas servir a faire lire un fichier du disque."""
    sortie = outil(ctx, {"fichier": "../../../Windows/win.ini"})
    assert "hors du workspace" in sortie or "n'existe pas" in sortie


def test_image_trop_lourde(ctx, service, monkeypatch) -> None:
    monkeypatch.setattr(vision, "POIDS_MAXI", 100)
    assert "trop lourde" in outil(ctx, {"fichier": "photo.png"})


def test_sans_cle_le_message_est_clair(monkeypatch, tmp_path: Path) -> None:
    import os
    monkeypatch.setattr(os, "environ", {**os.environ, "VENICE_API_KEY": ""})
    with pytest.raises(ToolError) as erreur:
        vision._cle()
    assert "cle Venice" in str(erreur.value)


def test_les_yeux_restent_sur_des_modeles_decensures() -> None:
    """🚨 Consigne du patron : l'agent reste sur GLM-4.7-heretic et ses semblables.

    Le modele principal n'a jamais bouge, mais ces yeux-la auraient pu introduire un modele
    bride par la bande — et refuser de decrire une capture au mauvais moment. Les deux
    retenus sont decensures, et mesures meilleurs que les brides essayes d'abord : 6 reperes
    sur 6 en 1,7 s, contre 3,6 a 6,3 s.
    """
    assert vision.MODELES, "il faut au moins un modele qui voit"
    for modele in vision.MODELES:
        assert "uncensored" in modele or "heretic" in modele, modele
