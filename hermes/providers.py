"""Catalogue des fournisseurs et des modeles.

Tous les fournisseurs cables parlent le protocole OpenAI ``/chat/completions``.
C'est un choix : un seul dialecte, un seul adaptateur, donc une seule source de
bugs au lieu de trois. Ajouter un fournisseur = ajouter une entree dans
``PROVIDERS``. Rien d'autre a toucher.

Un *modele* d'Hermes n'est pas un identifiant unique mais un alias resolu vers
une liste ordonnee de ``Route`` (fournisseur, identifiant reel). Hermes retient
la premiere route utilisable — celle dont la cle API est renseignee, ou dont le
serveur est local — et bascule sur la suivante si elle tombe. C'est ce qui
permet de servir « GLM-4.7-Heretic » indifferemment depuis vLLM en local,
Chutes ou OpenRouter, avec Venice en filet de securite.
"""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

#: Duree de validite d'une sonde de port local.
_PROBE_TTL = 30.0
_probes: dict[str, tuple[float, bool]] = {}


def local_server_up(base_url: str, *, timeout: float = 0.3) -> bool:
    """Teste si un serveur local ecoute, avec cache court.

    Sans cela, une route locale non demarree serait toujours essayee en premier :
    chaque conversation paierait un echec de connexion avant de basculer.
    """
    parsed = urlparse(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    key = f"{host}:{port}"
    cached = _probes.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _PROBE_TTL:
        return cached[1]
    try:
        with socket.create_connection((host, port), timeout=timeout):
            up = True
    except OSError:
        up = False
    _probes[key] = (now, up)
    return up


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    api_key_env: str
    #: Un serveur local n'authentifie rien : une cle factice suffit.
    local: bool = False
    notes: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)
    #: Corps supplementaire injecte dans chaque requete (options maison).
    extra_body: dict[str, object] = field(default_factory=dict)

    def api_key(self) -> str | None:
        """Cle configuree, ou ``None``. Les serveurs locaux n'authentifient rien."""
        key = os.getenv(self.api_key_env, "").strip()
        if key:
            return key
        return "local" if self.local else None

    @property
    def configured(self) -> bool:
        """Vrai si la route est reellement servable maintenant.

        Pour un fournisseur distant, cela veut dire « une cle est renseignee » ;
        pour un serveur local, « le port repond ».
        """
        if self.local:
            return local_server_up(self.base_url)
        return self.api_key() is not None


PROVIDERS: dict[str, ProviderSpec] = {
    # --- Auto-heberge : aucun filtrage, aucune donnee ne sort de la machine ---
    "vllm": ProviderSpec(
        name="vllm",
        base_url="http://localhost:8000/v1",
        api_key_env="VLLM_API_KEY",
        local=True,
        notes="vLLM local — la facon canonique de servir les poids GLM-4.7-Heretic.",
    ),
    "ollama": ProviderSpec(
        name="ollama",
        base_url="http://localhost:11434/v1",
        api_key_env="OLLAMA_API_KEY",
        local=True,
        notes="Ollama local (`ollama serve`).",
    ),
    "lmstudio": ProviderSpec(
        name="lmstudio",
        base_url="http://localhost:1234/v1",
        api_key_env="LMSTUDIO_API_KEY",
        local=True,
        notes="LM Studio, serveur local compatible OpenAI.",
    ),
    # --- Hebergeurs de poids ouverts ----------------------------------------
    "chutes": ProviderSpec(
        name="chutes",
        base_url="https://llm.chutes.ai/v1",
        api_key_env="CHUTES_API_KEY",
        notes="Heberge des fine-tunes communautaires, dont les variantes decensurees.",
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        notes="Une cle, des centaines de modeles. Le plus simple pour demarrer.",
        extra_headers={"X-Title": "Hermes Agent"},
    ),
    "venice": ProviderSpec(
        name="venice",
        base_url="https://api.venice.ai/api/v1",
        api_key_env="VENICE_API_KEY",
        notes="Oriente vie privee : pas de retention de conversation.",
        # Venice prefixe sinon ses propres consignes au system prompt, ce qui
        # perturbe l'appel d'outils.
        extra_body={"venice_parameters": {"include_venice_system_prompt": False}},
    ),
    "zai": ProviderSpec(
        name="zai",
        base_url="https://api.z.ai/api/paas/v4",
        api_key_env="ZAI_API_KEY",
        notes="API officielle Z.ai (GLM d'origine, non decensure).",
    ),
    "deepseek": ProviderSpec(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        notes="Solide en code, tres bon marche.",
    ),
    "groq": ProviderSpec(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        notes="Latence tres basse sur poids ouverts.",
    ),
    "together": ProviderSpec(
        name="together",
        base_url="https://api.together.xyz/v1",
        api_key_env="TOGETHER_API_KEY",
    ),
    "openai": ProviderSpec(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
    ),
    # --- Echappatoire : n'importe quel endpoint compatible OpenAI ------------
    "custom": ProviderSpec(
        name="custom",
        base_url="",  # renseigne par HERMES_CUSTOM_BASE_URL
        api_key_env="CUSTOM_API_KEY",
        notes="Endpoint maison : renseigne HERMES_CUSTOM_BASE_URL.",
    ),
}


@dataclass(frozen=True)
class Route:
    """Un modele concret, chez un fournisseur concret."""

    provider: str
    model: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.provider}:{self.model}"

    @property
    def spec(self) -> ProviderSpec:
        return PROVIDERS[self.provider]

    @property
    def usable(self) -> bool:
        return self.provider in PROVIDERS and PROVIDERS[self.provider].configured


@dataclass(frozen=True)
class ModelAlias:
    """Un nom stable cote utilisateur, plusieurs facons de le servir."""

    name: str
    routes: tuple[Route, ...]
    description: str = ""


def _r(provider: str, model: str) -> Route:
    return Route(provider=provider, model=model)


#: Les identifiants exacts varient d'un hebergeur a l'autre : on les enumere
#: tous plutot que d'en imposer un seul et de tomber en panne s'il change.
MODELS: dict[str, ModelAlias] = {
    "glm-4.7-heretic": ModelAlias(
        name="glm-4.7-heretic",
        description="GLM-4.7 passe au decensureur Heretic. Modele principal d'Hermes.",
        routes=(
            # Poids locaux d'abord quand un serveur tourne : rien ne sort de la machine.
            _r("vllm", "p-e-w/GLM-4.7-Heretic"),
            _r("ollama", "glm-4.7-heretic"),
            # Route verifiee : Venice heberge la variante Heretic, appels d'outils compris.
            _r("venice", "olafangensan-glm-4.7-flash-heretic"),
            _r("chutes", "p-e-w/GLM-4.7-Heretic"),
            # 🚨 `openrouter:p-e-w/glm-4.7-heretic` RETIRE le 10/09 : verifie contre le
            # catalogue OpenRouter, **zero modele « heretic » sur 436**. Ce n'etait pas une
            # faute de frappe, le modele n'y est pas. Une route morte dans une cascade est
            # pire qu'une route absente : elle donne l'illusion d'une redondance, et une
            # cascade ne rattrape QUE les 429 et 5xx — un 404 la traverse (cf. la panne du
            # bot muet). On la remplace par une route reelle vers le meme dernier recours,
            # chez un AUTRE fournisseur : c'est ca, une redondance.
            # Dernier recours : le GLM-4.7 d'origine, meme famille, non decensure.
            _r("venice", "zai-org-glm-4.7"),
            _r("openrouter", "z-ai/glm-4.7"),
        ),
    ),
    "venice": ModelAlias(
        name="venice",
        description="Venice, sans retention de conversation. Repli d'Hermes.",
        routes=(
            _r("venice", "zai-org-glm-4.7"),
            _r("venice", "venice-uncensored-1-2"),
        ),
    ),
    "venice-uncensored": ModelAlias(
        name="venice-uncensored",
        description="Venice Uncensored. Peu enclin aux appels d'outils : a reserver au dialogue.",
        routes=(_r("venice", "venice-uncensored-1-2"),),
    ),
    "glm-4.7": ModelAlias(
        name="glm-4.7",
        description="GLM-4.7 d'origine, tel que publie par Z.ai.",
        routes=(
            _r("venice", "zai-org-glm-4.7"),
            _r("zai", "glm-4.7"),
            _r("openrouter", "z-ai/glm-4.7"),
        ),
    ),
    "deepseek": ModelAlias(
        name="deepseek",
        description="DeepSeek — solide en code.",
        routes=(
            _r("deepseek", "deepseek-chat"),
            _r("venice", "deepseek-v3.2"),
            _r("openrouter", "deepseek/deepseek-chat"),
        ),
    ),
}

#: Ordre de preference par defaut : GLM-4.7-Heretic d'abord, Venice ensuite.
DEFAULT_CHAIN = ("glm-4.7-heretic", "venice")


def get_provider(name: str) -> ProviderSpec:
    key = name.strip().lower()
    if key not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise KeyError(f"Fournisseur inconnu : {name!r}. Connus : {known}")
    return PROVIDERS[key]


def resolve(spec: str) -> tuple[Route, ...]:
    """Traduit une specification utilisateur en routes candidates.

    Accepte trois formes, de la plus explicite a la plus commode :

    * ``"venice:venice-uncensored"`` — fournisseur et modele imposes ;
    * ``"glm-4.7-heretic"``          — alias du catalogue, toutes ses routes ;
    * ``"z-ai/glm-4.7"``             — identifiant brut, essaye chez chaque
      fournisseur configure (utile pour un modele que le catalogue ignore).
    """
    text = spec.strip()
    if not text:
        return ()

    if ":" in text and not text.startswith(("http://", "https://")):
        provider, _, model = text.partition(":")
        provider = provider.strip().lower()
        model = model.strip()
        if provider in PROVIDERS and model:
            return (_r(provider, model),)

    alias = MODELS.get(text.lower())
    if alias:
        return alias.routes

    # Identifiant brut : on le propose a tout fournisseur distant configure.
    return tuple(
        _r(name, text) for name, p in PROVIDERS.items() if p.configured and name != "custom"
    )


def describe(spec: str) -> str:
    """Rend une specification lisible, pour /model et le diagnostic."""
    routes = resolve(spec)
    if not routes:
        return f"{spec} (aucune route)"
    marks = ["✅" if r.usable else "⚪" for r in routes]
    return " ".join(f"{m}{r}" for m, r in zip(marks, routes, strict=True))
