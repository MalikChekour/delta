"""Catalogue des fournisseurs LLM utilisables par Hermes.

Deux familles seulement :
  - ``openai``    : tout endpoint parlant le protocole OpenAI /chat/completions.
                    C'est le cas de la quasi-totalite du marche, y compris les
                    serveurs locaux (Ollama, LM Studio, vLLM, llama.cpp).
  - ``anthropic`` : le SDK officiel Anthropic, dont le protocole differe.

Ajouter un fournisseur = ajouter une entree ici. Rien d'autre a toucher.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    kind: str  # "openai" | "anthropic"
    base_url: str | None
    api_key_env: str
    default_model: str
    #: Modeles suggeres, affiches par /model. Purement indicatif.
    suggested: tuple[str, ...] = ()
    #: Cle factice acceptee quand le serveur est local et n'authentifie rien.
    local: bool = False
    notes: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def needs_key(self) -> bool:
        return not self.local


# Ordonne du plus permissif/autonome au plus encadre.
PROVIDERS: dict[str, ProviderSpec] = {
    # --- Local : aucun filtrage cote fournisseur, aucune donnee ne sort ---------
    "ollama": ProviderSpec(
        name="ollama",
        kind="openai",
        base_url="http://localhost:11434/v1",
        api_key_env="OLLAMA_API_KEY",
        default_model="hermes4:70b",
        suggested=(
            "hermes4:70b",
            "dolphin3:8b",
            "huihui_ai/mistral-small-abliterated:24b",
            "qwen3.8:27b",
            "deepseek-r1:70b",
        ),
        local=True,
        notes="Serveur local. Zero filtrage, zero telemetrie. `ollama serve` requis.",
    ),
    "lmstudio": ProviderSpec(
        name="lmstudio",
        kind="openai",
        base_url="http://localhost:1234/v1",
        api_key_env="LMSTUDIO_API_KEY",
        default_model="local-model",
        local=True,
        notes="LM Studio, serveur local compatible OpenAI.",
    ),
    "vllm": ProviderSpec(
        name="vllm",
        kind="openai",
        base_url="http://localhost:8000/v1",
        api_key_env="VLLM_API_KEY",
        default_model="NousResearch/Hermes-4-70B",
        local=True,
        notes="vLLM auto-heberge : le plus rapide pour servir des poids ouverts.",
    ),
    # --- Hebergees a politique legere ------------------------------------------
    "openrouter": ProviderSpec(
        name="openrouter",
        kind="openai",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        default_model="deepseek/deepseek-chat",
        suggested=(
            "deepseek/deepseek-chat",
            "deepseek/deepseek-r1",
            "nousresearch/hermes-4-405b",
            "cognitivecomputations/dolphin3.0-mistral-24b",
            "qwen/qwen3-235b-a22b",
            "x-ai/grok-4",
        ),
        notes="Une cle, des centaines de modeles, y compris les fine-tunes sans refus.",
        extra_headers={"X-Title": "Hermes Agent"},
    ),
    "deepseek": ProviderSpec(
        name="deepseek",
        kind="openai",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        default_model="deepseek-chat",
        suggested=("deepseek-chat", "deepseek-reasoner"),
        notes="Tres peu de refus, excellent en code, prix plancher.",
    ),
    "venice": ProviderSpec(
        name="venice",
        kind="openai",
        base_url="https://api.venice.ai/api/v1",
        api_key_env="VENICE_API_KEY",
        default_model="venice-uncensored",
        suggested=("venice-uncensored", "qwen-2.5-qwq-32b", "llama-3.3-70b"),
        notes="Oriente vie privee : pas de log de conversation, pas de filtre.",
    ),
    "xai": ProviderSpec(
        name="xai",
        kind="openai",
        base_url="https://api.x.ai/v1",
        api_key_env="XAI_API_KEY",
        default_model="grok-4",
        suggested=("grok-4", "grok-4-fast"),
    ),
    "groq": ProviderSpec(
        name="groq",
        kind="openai",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        default_model="llama-3.3-70b-versatile",
        notes="Latence tres basse sur poids ouverts.",
    ),
    "together": ProviderSpec(
        name="together",
        kind="openai",
        base_url="https://api.together.xyz/v1",
        api_key_env="TOGETHER_API_KEY",
        default_model="NousResearch/Hermes-4-405B",
    ),
    "fireworks": ProviderSpec(
        name="fireworks",
        kind="openai",
        base_url="https://api.fireworks.ai/inference/v1",
        api_key_env="FIREWORKS_API_KEY",
        default_model="accounts/fireworks/models/deepseek-v3",
    ),
    "mistral": ProviderSpec(
        name="mistral",
        kind="openai",
        base_url="https://api.mistral.ai/v1",
        api_key_env="MISTRAL_API_KEY",
        default_model="mistral-large-latest",
    ),
    # --- Encadrees, mais les plus fortes en code -------------------------------
    "openai": ProviderSpec(
        name="openai",
        kind="openai",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        default_model="gpt-5",
    ),
    "anthropic": ProviderSpec(
        name="anthropic",
        kind="anthropic",
        base_url=None,
        api_key_env="ANTHROPIC_API_KEY",
        default_model="claude-opus-5",
        suggested=("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"),
        notes="Le plus capable en code et en agentique long.",
    ),
    # --- Echappatoire : n'importe quel endpoint compatible OpenAI --------------
    "custom": ProviderSpec(
        name="custom",
        kind="openai",
        base_url=None,  # renseigne par HERMES_CUSTOM_BASE_URL
        api_key_env="CUSTOM_API_KEY",
        default_model="",
        notes="Pour tout endpoint maison : voir HERMES_CUSTOM_BASE_URL.",
    ),
}


def get(name: str) -> ProviderSpec:
    key = name.strip().lower()
    if key not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise KeyError(f"Fournisseur inconnu : {name!r}. Connus : {known}")
    return PROVIDERS[key]
