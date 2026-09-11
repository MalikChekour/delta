"""Memoire de CONNAISSANCES de l'agent : retenir un fait, le retrouver plus tard.

🚨 CE QUI MANQUAIT. L'agent gardait ses CONVERSATIONS (SQLite, `memory.py`) mais aucun FAIT :
tout ce qu'il apprenait mourait avec le fil de discussion. Impossible de lui dire quelque
chose lundi et de s'y referer jeudi.

🚨 POURQUOI FTS5 ET PAS UNE BASE VECTORIELLE. Question posee et tranchee explicitement :
pour quelques milliers de notes, la recherche plein texte fait aussi bien pour un centieme
du cout. Elle n'exige **aucun modele d'embedding**, donc aucun telechargement, aucun GPU,
aucune seconde de calcul — ce qui compte sur une machine sans carte graphique. Et surtout
elle cherche des mots EXACTS : quand une note porte une citation reglementaire qui doit etre
rendue mot pour mot, la recherche approximative est un defaut, pas une qualite.
FTS5 est inclus dans SQLite, donc dans la bibliotheque standard : zero dependance ajoutee.

Un index vectoriel (`sqlite-vec`) reste greffable plus tard si un besoin SEMANTIQUE se
mesure. Pas avant : on ne paie pas d'avance une complexite qu'on n'a pas constatee.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..sandbox import clip
from . import ToolContext, tool

_BASES: dict[str, Path] = {}


def _base(ctx: ToolContext) -> sqlite3.Connection:
    chemin = ctx.workspace / ".hermes_savoir" / "savoir.db"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    co = sqlite3.connect(chemin, timeout=10)
    co.execute("PRAGMA journal_mode=WAL")
    # La table porte les donnees ; la table virtuelle FTS5 ne porte que l'index.
    co.execute("CREATE TABLE IF NOT EXISTS notes ("
               " id INTEGER PRIMARY KEY, titre TEXT, contenu TEXT, source TEXT,"
               " etiquettes TEXT, pose REAL)")
    co.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
               " titre, contenu, etiquettes, content='notes', content_rowid='id',"
               " tokenize=\"unicode61 remove_diacritics 2\")")
    # 🚨 `remove_diacritics 2` n'est pas un detail : sans lui, « allegation » ne trouve pas
    # « allégation ». Sur un corpus francais, c'est la moitie des recherches qui echouent.
    for nom, corps in (
        ("notes_ai", "AFTER INSERT ON notes BEGIN INSERT INTO notes_fts(rowid,titre,contenu,"
                     "etiquettes) VALUES (new.id,new.titre,new.contenu,new.etiquettes); END"),
        ("notes_ad", "AFTER DELETE ON notes BEGIN INSERT INTO notes_fts(notes_fts,rowid,titre,"
                     "contenu,etiquettes) VALUES('delete',old.id,old.titre,old.contenu,"
                     "old.etiquettes); END"),
        ("notes_au", "AFTER UPDATE ON notes BEGIN INSERT INTO notes_fts(notes_fts,rowid,titre,"
                     "contenu,etiquettes) VALUES('delete',old.id,old.titre,old.contenu,"
                     "old.etiquettes); INSERT INTO notes_fts(rowid,titre,contenu,etiquettes) "
                     "VALUES (new.id,new.titre,new.contenu,new.etiquettes); END"),
    ):
        co.execute(f"CREATE TRIGGER IF NOT EXISTS {nom} {corps}")
    co.commit()
    return co


@tool(
    "retiens",
    "Enregistre un fait dans la memoire durable de l'agent, pour le retrouver dans une "
    "AUTRE conversation. A utiliser pour ce qui reste vrai au-dela du fil en cours : une "
    "preference, une decision, une source, un chiffre verifie.",
    {
        "titre": {"type": "string", "description": "Resume en quelques mots."},
        "contenu": {"type": "string", "description": "Le fait, redige pour etre relu seul."},
        "source": {"type": "string", "description": "URL ou origine, si elle existe."},
        "etiquettes": {"type": "string", "description": "Mots-cles separes par des virgules."},
    },
    ["titre", "contenu"],
)
def retiens(ctx: ToolContext, args: dict[str, Any]) -> str:
    titre = str(args["titre"]).strip()
    contenu = str(args["contenu"]).strip()
    if not contenu:
        raise ToolError("contenu vide : il n'y a rien a retenir.")
    co = _base(ctx)
    try:
        # 🚨 Un meme titre remplace la note existante plutot que d'en empiler une seconde :
        # sinon la memoire accumule des versions contradictoires du meme fait, et la
        # recherche rend les deux sans dire laquelle vaut.
        ancienne = co.execute("SELECT id, contenu FROM notes WHERE titre = ?",
                              (titre,)).fetchone()
        # 🚨 DIRE QUE RIEN N'A CHANGE. Mesure du 11/09 : l'outil repondait « Note mise a
        # jour » meme quand le contenu etait identique au caractere pres, et le modele,
        # croyant avoir modifie quelque chose, rappelait `retiens` — SIX fois de suite.
        # Un outil doit distinguer « j'ai agi » de « il n'y avait rien a faire ».
        if ancienne and ancienne[1] == contenu:
            return (f"Note deja enregistree a l'identique : « {titre} ». Rien a faire, "
                    f"n'appelle plus cet outil pour ce fait.")
        champs = (titre, contenu, str(args.get("source") or ""),
                  str(args.get("etiquettes") or ""), time.time())
        if ancienne:
            co.execute("UPDATE notes SET titre=?,contenu=?,source=?,etiquettes=?,pose=? "
                       "WHERE id=?", champs + (ancienne[0],))
            verbe = "mise a jour"
        else:
            co.execute("INSERT INTO notes(titre,contenu,source,etiquettes,pose) "
                       "VALUES (?,?,?,?,?)", champs)
            verbe = "enregistree"
        co.commit()
        total = co.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        return f"Note {verbe} : « {titre} ». La memoire compte {total} note(s)."
    finally:
        co.close()


@tool(
    "rappelle",
    "Cherche dans la memoire durable de l'agent. A faire AVANT de chercher sur le web : "
    "ce qu'on sait deja n'a pas besoin d'etre recherche.",
    {
        "requete": {"type": "string", "description": "Mots-cles. Vide = les notes recentes."},
        "limite": {"type": "integer", "description": "Nombre de notes (5 par defaut)."},
    },
)
def rappelle(ctx: ToolContext, args: dict[str, Any]) -> str:
    requete = str(args.get("requete") or "").strip()
    limite = max(1, min(int(args.get("limite") or 5), 25))
    co = _base(ctx)
    try:
        if not requete:
            lignes = co.execute(
                "SELECT titre,contenu,source,pose FROM notes ORDER BY pose DESC LIMIT ?",
                (limite,)).fetchall()
        else:
            # 🚨 On echappe la requete en la citant terme a terme : une apostrophe ou un
            # operateur FTS5 mal place ( « allegation OR » ) leve une erreur de syntaxe et
            # l'agent croirait sa memoire vide alors qu'elle est pleine.
            termes = [t for t in re.findall(r"\w+", requete, re.UNICODE) if len(t) > 1]
            if not termes:
                raise ToolError("requete trop courte.")
            # 🚨 RECHERCHE PAR PREFIXE au-dela de 3 lettres. FTS5 compare des mots EXACTS,
            # sans radical : « allegation » ne trouvait pas « allégations » — le pluriel
            # suffisait a rendre la memoire muette. Le suffixe `*` regle singulier/pluriel
            # et les formes derivees (« reglement » trouve « reglementation »). En dessous
            # de 4 lettres on reste exact, sinon « ete* » ramenerait la moitie du corpus.
            fts = " OR ".join(('"%s"*' % t) if len(t) >= 4 else ('"%s"' % t) for t in termes)
            lignes = co.execute(
                "SELECT n.titre,n.contenu,n.source,n.pose FROM notes_fts f "
                "JOIN notes n ON n.id = f.rowid WHERE notes_fts MATCH ? "
                "ORDER BY rank LIMIT ?", (fts, limite)).fetchall()
        if not lignes:
            total = co.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            return (f"aucune note ne correspond a {requete!r} "
                    f"(la memoire compte {total} note(s)).")
        out = []
        for titre, contenu, source, pose in lignes:
            age = time.strftime("%Y-%m-%d", time.localtime(pose))
            out.append(f"— {titre}  [{age}]"
                       + (f"\n  source : {source}" if source else "")
                       + f"\n  {contenu}")
        return clip("\n".join(out), ctx.output_limit)
    finally:
        co.close()


@tool(
    "oublie",
    "Supprime une note de la memoire durable, par son titre exact.",
    {"titre": {"type": "string", "description": "Titre exact de la note a supprimer."}},
    ["titre"],
)
def oublie(ctx: ToolContext, args: dict[str, Any]) -> str:
    co = _base(ctx)
    try:
        cur = co.execute("DELETE FROM notes WHERE titre = ?", (str(args["titre"]).strip(),))
        co.commit()
        if not cur.rowcount:
            return "aucune note ne porte ce titre : rien n'a ete supprime."
        return f"Note supprimee : « {args['titre']} »."
    finally:
        co.close()


TOOLS = (retiens, rappelle, oublie)
