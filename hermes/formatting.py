"""Rendu Markdown -> HTML Telegram, et decoupage sur.

Telegram rejette (HTTP 400) tout message dont le balisage est incomplet. Envoyer
directement le Markdown d'un modele est donc une source de pannes permanente :
une astérisque isolée, un bloc de code non ferme, ou une coupe a 4096 caracteres
tombant au milieu d'un bloc suffisent.

La strategie retenue supprime le probleme a la racine : on convertit le Markdown
en HTML nous-memes, puis on decoupe *ligne par ligne* en refermant et rouvrant
les balises a chaque frontiere. Chaque morceau envoye est donc du HTML complet,
independamment de ce que le modele a produit.
"""

from __future__ import annotations

import html
import re

#: Telegram accepte 4096 caracteres ; on garde une marge pour les balises.
CHUNK_LIMIT = 3500

_FENCE = re.compile(r"^```([\w+.-]*)\s*$")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_UNDERSCORE_ITALIC = re.compile(r"(?<![\w_])_([^_\n]+)_(?![\w_])")
_STRIKE = re.compile(r"~~([^~\n]+)~~")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+")

_PLACEHOLDER = "\x00{}\x00"
_BALISE = re.compile(r"</?(b|i|s|u|code|pre|a)\b[^>]*>")


def bien_formee(fragment: str) -> bool:
    """Vrai si les balises sont correctement imbriquees.

    Telegram refuse le croisement (``<s>a<i>b</s>c</i>``) comme l'imbrication
    d'une balise dans elle-meme. Or les conversions gras/italique/barre sont
    appliquees independamment : un balisage entremele dans le texte du modele
    peut donc produire l'un ou l'autre. On le detecte ici plutot que de l'envoyer.
    """
    pile: list[str] = []
    for marque in _BALISE.finditer(fragment):
        nom = marque.group(1)
        if marque.group(0).startswith("</"):
            if not pile or pile.pop() != nom:
                return False
        elif nom in pile:
            return False
        else:
            pile.append(nom)
    return not pile


def _inline(text: str) -> str:
    """Convertit une ligne de Markdown en HTML Telegram valide.

    Les portions ``code`` sont mises de cote avant echappement pour qu'un
    ``*`` a l'interieur d'un extrait de code ne soit pas pris pour du gras.
    """
    kept: list[str] = []

    def stash(match: re.Match[str]) -> str:
        kept.append(html.escape(match.group(1)))
        return _PLACEHOLDER.format(len(kept) - 1)

    text = _INLINE_CODE.sub(stash, text)

    links: list[tuple[str, str]] = []

    def stash_link(match: re.Match[str]) -> str:
        links.append((html.escape(match.group(1)), html.escape(match.group(2), quote=True)))
        return f"\x01{len(links) - 1}\x01"

    text = _LINK.sub(stash_link, text)
    text = html.escape(text)

    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    text = _UNDERSCORE_ITALIC.sub(r"<i>\1</i>", text)

    for index, (label, href) in enumerate(links):
        text = text.replace(f"\x01{index}\x01", f'<a href="{href}">{label}</a>')
    for index, code in enumerate(kept):
        text = text.replace(_PLACEHOLDER.format(index), f"<code>{code}</code>")
    return text


def _render_text_line(line: str) -> str:
    """Rend une ligne, en repliant sur du texte nu si le balisage sort faux.

    Mieux vaut une ligne sans mise en forme qu'un message entier refuse par
    Telegram : ce repli garantit que toute entree, si tordue soit-elle, produit
    un fragment valide."""
    heading = _HEADING.match(line)
    bullet = _BULLET.match(line)
    if heading:
        rendu = f"<b>{_inline(heading.group(2).strip())}</b>"
    elif bullet:
        rendu = f"{bullet.group(1)}• {_inline(line[bullet.end() :])}"
    else:
        rendu = _inline(line)
    return rendu if bien_formee(rendu) else html.escape(line)


def to_html_lines(markdown: str) -> list[tuple[bool, str]]:
    """Rend le Markdown en lignes ``(est_du_code, html)``.

    Garder la granularite de la ligne est ce qui rend le decoupage sur : on peut
    couper n'importe ou sans jamais casser une balise.
    """
    out: list[tuple[bool, str]] = []
    in_code = False
    for raw in markdown.splitlines():
        fence = _FENCE.match(raw.strip())
        if fence:
            in_code = not in_code
            continue
        if in_code:
            out.append((True, html.escape(raw)))
        else:
            out.append((False, _render_text_line(raw)))
    return out


def _wrap(buffer: list[tuple[bool, str]]) -> str:
    """Assemble des lignes en un fragment HTML complet."""
    parts: list[str] = []
    run: list[str] = []
    run_is_code = False

    def flush() -> None:
        if not run:
            return
        body = "\n".join(run)
        parts.append(f"<pre><code>{body}</code></pre>" if run_is_code else body)
        run.clear()

    for is_code, line in buffer:
        if is_code != run_is_code:
            flush()
            run_is_code = is_code
        run.append(line)
    flush()
    return "\n".join(parts)


def _split_long(line: str, limit: int) -> list[str]:
    """Coupe une ligne demesuree. Le HTML inline y est rare : on coupe sur un
    espace quand c'est possible, et on echappe le reste par prudence."""
    pieces: list[str] = []
    while len(line) > limit:
        cut = line.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        pieces.append(line[:cut])
        line = line[cut:].lstrip()
    if line:
        pieces.append(line)
    # Une ligne vide separe deux paragraphes : la laisser tomber collerait tout
    # le texte, et supprimerait les respirations a l'interieur des blocs de code.
    return pieces or [line]


def chunks(markdown: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Rend et decoupe en messages Telegram, chacun en HTML valide."""
    lines = to_html_lines(markdown)
    if not lines:
        return []

    messages: list[str] = []
    buffer: list[tuple[bool, str]] = []
    size = 0

    def flush() -> None:
        nonlocal size
        if buffer:
            rendered = _wrap(buffer).strip()
            if rendered:
                messages.append(rendered)
            buffer.clear()
        size = 0

    for is_code, line in lines:
        for piece in _split_long(line, limit - 40):
            cost = len(piece) + 1
            if size + cost > limit - 40 and buffer:
                flush()
            buffer.append((is_code, piece))
            size += cost
    flush()
    return messages or [""]


def strip_tags(fragment: str) -> str:
    """Rend un fragment HTML en texte nu.

    Sert de dernier repli quand Telegram refuse malgre tout le balisage : on
    envoie le meme morceau sans balise, plutot que de perdre la reponse.
    """
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"</(p|pre|div)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()
