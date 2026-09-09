"""Hierarchie d'erreurs d'Hermes.

Une seule regle : tout ce qui est previsible leve une ``HermesError`` porteuse
d'un message lisible par un humain. Les couches hautes (CLI, Telegram) affichent
ce message tel quel, sans traceback. Le reste remonte et est journalise.
"""

from __future__ import annotations


class HermesError(Exception):
    """Erreur attendue, dont le message est destine a l'utilisateur final."""


class ConfigError(HermesError):
    """Configuration absente ou incoherente."""


class ProviderError(HermesError):
    """Le fournisseur LLM a refuse la requete ou est injoignable."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ToolError(HermesError):
    """Un outil a echoue de facon previsible (chemin invalide, timeout...).

    Le message est renvoye au modele pour qu'il corrige de lui-meme : il doit
    donc etre precis et actionnable.
    """
