"""
Provider Specifications and Adapters for 32 LLM Providers.
Defines endpoints, default free models, authentication headers, and discovery methods.
"""
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class ProviderSpec(BaseModel):
    id: str
    name: str
    aliases: List[str] = Field(default_factory=list)
    base_url: str
    chat_endpoint: str = "/chat/completions"
    models_endpoint: Optional[str] = "/models"
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer "
    supports_models_discovery: bool = True
    default_free_models: List[str] = Field(default_factory=list)
    # Default estimated daily quota per model in tokens (e.g. 1,000,000)
    default_model_token_quota: int = 1_000_000
    # Requests per minute limit estimation
    rpm_limit: int = 60
    # Custom headers
    extra_headers: Dict[str, str] = Field(default_factory=dict)
    # Priority rank (higher = preferred when healthy)
    base_priority: int = 50


PROVIDER_SPECS: Dict[str, ProviderSpec] = {
    "google": ProviderSpec(
        id="google",
        name="Google AI Studio",
        aliases=["google", "google ai studio", "gemini"],
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        api_key_header="Authorization",
        api_key_prefix="Bearer ",
        supports_models_discovery=True,
        default_free_models=[
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro"
        ],
        default_model_token_quota=2_000_000,
        rpm_limit=15,
        base_priority=90,
    ),
    "groq": ProviderSpec(
        id="groq",
        name="Groq",
        aliases=["groq"],
        base_url="https://api.groq.com/openai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        api_key_header="Authorization",
        api_key_prefix="Bearer ",
        supports_models_discovery=True,
        default_free_models=[
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "llama3-70b-8192",
            "llama3-8b-8192",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
            "qwen-2.5-32b"
        ],
        default_model_token_quota=1_000_000,
        rpm_limit=30,
        base_priority=95,
    ),
    "cerebras": ProviderSpec(
        id="cerebras",
        name="cerebras",
        aliases=["cerebras", "cerebras ai"],
        base_url="https://api.cerebras.ai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        api_key_header="Authorization",
        api_key_prefix="Bearer ",
        supports_models_discovery=True,
        default_free_models=[
            "llama3.1-8b",
            "llama-3.3-70b",
            "llama3.1-70b"
        ],
        default_model_token_quota=1_000_000,
        rpm_limit=30,
        base_priority=95,
    ),
    "openrouter": ProviderSpec(
        id="openrouter",
        name="OpenRouter",
        aliases=["openrouter", "open router"],
        base_url="https://openrouter.ai/api/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        api_key_header="Authorization",
        api_key_prefix="Bearer ",
        extra_headers={"HTTP-Referer": "https://unified-gateway.local", "X-Title": "Unified LLM Gateway"},
        supports_models_discovery=True,
        default_free_models=[
            "deepseek/deepseek-r1:free",
            "deepseek/deepseek-chat:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "meta-llama/llama-3.1-8b-instruct:free",
            "google/gemini-2.0-flash-exp:free",
            "qwen/qwen-2.5-coder-32b-instruct:free",
            "mistralai/mistral-7b-instruct:free",
            "microsoft/phi-3-mini-128k-instruct:free"
        ],
        default_model_token_quota=1_500_000,
        rpm_limit=20,
        base_priority=90,
    ),
    "mistral": ProviderSpec(
        id="mistral",
        name="Mistral",
        aliases=["mistral", "mistral ai"],
        base_url="https://api.mistral.ai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        api_key_header="Authorization",
        api_key_prefix="Bearer ",
        supports_models_discovery=True,
        default_free_models=[
            "mistral-small-latest",
            "open-mistral-nemo",
            "codestral-latest",
            "open-mistral-7b"
        ],
        default_model_token_quota=1_000_000,
        rpm_limit=30,
        base_priority=85,
    ),
    "siliconflow": ProviderSpec(
        id="siliconflow",
        name="SiliconFlow",
        aliases=["siliconflow", "silicon flow"],
        base_url="https://api.siliconflow.cn/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        api_key_header="Authorization",
        api_key_prefix="Bearer ",
        supports_models_discovery=True,
        default_free_models=[
            "Qwen/Qwen2.5-7B-Instruct",
            "Qwen/Qwen2.5-Coder-7B-Instruct",
            "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
            "deepseek-ai/DeepSeek-V3",
            "THUDM/glm-4-9b-chat",
            "internlm/internlm2_5-7b-chat"
        ],
        default_model_token_quota=1_000_000,
        rpm_limit=60,
        base_priority=80,
    ),
    "nvidia": ProviderSpec(
        id="nvidia",
        name="NVIDIA NIM",
        aliases=["nvidia", "nvidia nim", "nim"],
        base_url="https://integrate.api.nvidia.com/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        api_key_header="Authorization",
        api_key_prefix="Bearer ",
        supports_models_discovery=True,
        default_free_models=[
            "meta/llama-3.3-70b-instruct",
            "meta/llama-3.1-8b-instruct",
            "deepseek-ai/deepseek-r1",
            "nvidia/nemotron-4-340b-instruct",
            "mistralai/mistral-large-2407"
        ],
        default_model_token_quota=1_000_000,
        rpm_limit=40,
        base_priority=85,
    ),
    "zhipu": ProviderSpec(
        id="zhipu",
        name="Zhipu AI",
        aliases=["zhipu", "zhipu ai", "bigmodel", "glm"],
        base_url="https://open.bigmodel.cn/api/paas/v4",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        api_key_header="Authorization",
        api_key_prefix="Bearer ",
        supports_models_discovery=True,
        default_free_models=[
            "glm-4-flash",
            "glm-4v-flash",
            "glm-zero-preview"
        ],
        default_model_token_quota=1_000_000,
        rpm_limit=60,
        base_priority=80,
    ),
    "huggingface": ProviderSpec(
        id="huggingface",
        name="HuggingFace Router",
        aliases=["huggingface", "huggingface router", "hf"],
        base_url="https://router.huggingface.co/hf-inference/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        api_key_header="Authorization",
        api_key_prefix="Bearer ",
        supports_models_discovery=False,
        default_free_models=[
            "meta-llama/Llama-3.3-70B-Instruct",
            "Qwen/Qwen2.5-Coder-32B-Instruct",
            "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
            "mistralai/Mistral-7B-Instruct-v0.3"
        ],
        default_model_token_quota=1_000_000,
        rpm_limit=30,
        base_priority=75,
    ),
    "pollinations": ProviderSpec(
        id="pollinations",
        name="Pollinations",
        aliases=["pollinations"],
        base_url="https://text.pollinations.ai/openai",
        chat_endpoint="/chat/completions",
        models_endpoint=None,
        api_key_header="Authorization",
        api_key_prefix="Bearer ",
        supports_models_discovery=False,
        default_free_models=[
            "openai",
            "mistral",
            "claude",
            "qwen-coder",
            "deepseek-r1",
            "llama"
        ],
        default_model_token_quota=2_000_000,
        rpm_limit=60,
        base_priority=80,
    ),
    "sail_research": ProviderSpec(
        id="sail_research",
        name="Sail Research",
        aliases=["sail research", "sail", "sailresearch"],
        base_url="https://api.sailresearch.com/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["sail-7b", "sail-8b", "llama-3.1-8b"],
        base_priority=70,
    ),
    "bai": ProviderSpec(
        id="bai",
        name="B.AI",
        aliases=["b.ai", "bai", "baidev"],
        base_url="https://api.b.ai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["bai-chat", "bai-code", "gpt-4o-mini"],
        base_priority=70,
    ),
    "ollama_cloud": ProviderSpec(
        id="ollama_cloud",
        name="Ollama Cloud",
        aliases=["ollama cloud", "ollama"],
        base_url="https://api.ollama.ai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["llama3.3:70b", "deepseek-r1:latest", "qwen2.5:latest"],
        base_priority=75,
    ),
    "kilo_gateway": ProviderSpec(
        id="kilo_gateway",
        name="Kilo Gateway",
        aliases=["kilo gateway", "kilo"],
        base_url="https://api.kilo.ai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint=None,
        api_key_header="",
        api_key_prefix="",
        default_free_models=["kilo-general", "kilo-fast"],
        base_priority=65,
    ),
    "ovh_ai": ProviderSpec(
        id="ovh_ai",
        name="OVH AI",
        aliases=["ovh ai", "ovh"],
        base_url="https://ai.endpoints.kepler.ovh.net/v1",
        chat_endpoint="/chat/completions",
        models_endpoint=None,
        api_key_header="",
        api_key_prefix="",
        default_free_models=["mistral-7b-instruct", "llama-3-8b-instruct"],
        base_priority=65,
    ),
    "llm7": ProviderSpec(
        id="llm7",
        name="LLM7",
        aliases=["llm7", "llm7.io"],
        base_url="https://api.llm7.io/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["llm7-chat", "llm7-code"],
        base_priority=70,
    ),
    "opencode_zen": ProviderSpec(
        id="opencode_zen",
        name="OpenCode Zen",
        aliases=["opencode zen", "opencode", "zen"],
        base_url="https://api.opencodezen.com/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["opencode-zen-32b", "opencode-zen-coder"],
        base_priority=70,
    ),
    "agnes_ai": ProviderSpec(
        id="agnes_ai",
        name="Agnes AI",
        aliases=["agnes ai", "agnes"],
        base_url="https://api.agnes.ai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["agnes-general", "agnes-smart"],
        base_priority=70,
    ),
    "reka": ProviderSpec(
        id="reka",
        name="Reka",
        aliases=["reka", "reka ai"],
        base_url="https://api.reka.ai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["reka-flash", "reka-core", "reka-edge"],
        base_priority=75,
    ),
    "routeway": ProviderSpec(
        id="routeway",
        name="Routeway",
        aliases=["routeway", "routeway ai"],
        base_url="https://api.routeway.ai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["routeway-auto", "routeway-fast"],
        base_priority=70,
    ),
    "bazaarlink": ProviderSpec(
        id="bazaarlink",
        name="BazaarLink",
        aliases=["bazaarlink", "bazaar link"],
        base_url="https://api.bazaarlink.com/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["bazaar-chat", "bazaar-agent"],
        base_priority=70,
    ),
    "aion_labs": ProviderSpec(
        id="aion_labs",
        name="Aion Labs",
        aliases=["aion labs", "aion"],
        base_url="https://api.aionlabs.ai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["aion-chat", "aion-fast"],
        base_priority=70,
    ),
    "requesty": ProviderSpec(
        id="requesty",
        name="Requesty",
        aliases=["requesty", "requesty ai"],
        base_url="https://router.requesty.ai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["openai/gpt-4o-mini", "anthropic/claude-3-haiku"],
        base_priority=75,
    ),
    "navyai": ProviderSpec(
        id="navyai",
        name="NavyAI",
        aliases=["navyai", "navy ai"],
        base_url="https://api.navyai.com/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["navy-llm-1", "navy-fast"],
        base_priority=70,
    ),
    "nararouter": ProviderSpec(
        id="nararouter",
        name="NaraRouter",
        aliases=["nararouter", "nara router", "nara"],
        base_url="https://api.nararouter.com/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["nara-auto", "nara-code"],
        base_priority=70,
    ),
    "sea_lion": ProviderSpec(
        id="sea_lion",
        name="SEA-LION",
        aliases=["sea-lion", "sea lion", "sealion"],
        base_url="https://api.sea-lion.ai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["sea-lion-7b-instruct", "sea-lion-v3-8b"],
        base_priority=70,
    ),
    "orcarouter": ProviderSpec(
        id="orcarouter",
        name="OrcaRouter",
        aliases=["orcarouter", "orca router", "orca"],
        base_url="https://api.orcarouter.com/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["orca-fast", "orca-smart"],
        base_priority=70,
    ),
    "unorouter": ProviderSpec(
        id="unorouter",
        name="UnoRouter",
        aliases=["unorouter", "uno router", "uno"],
        base_url="https://api.unorouter.com/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["uno-chat", "uno-fast"],
        base_priority=70,
    ),
    "kiro": ProviderSpec(
        id="kiro",
        name="Kiro",
        aliases=["kiro", "kiro ai"],
        base_url="https://api.kiro.ai/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["kiro-base", "kiro-chat"],
        base_priority=70,
    ),
    "anyapi": ProviderSpec(
        id="anyapi",
        name="AnyAPI",
        aliases=["anyapi", "any api"],
        base_url="https://api.anyapi.io/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["anyapi-chat", "anyapi-fast"],
        base_priority=70,
    ),
    "ai_horde": ProviderSpec(
        id="ai_horde",
        name="AI Horde",
        aliases=["ai horde", "aihorde", "horde"],
        base_url="https://aihorde.net/api/v2",
        chat_endpoint="/chat/completions",
        models_endpoint=None,
        api_key_header="apikey",
        api_key_prefix="",
        default_free_models=["aphrodite/mythomax-13b", "aphrodite/llama-3-8b"],
        base_priority=60,
    ),
    "longcat": ProviderSpec(
        id="longcat",
        name="LongCat",
        aliases=["longcat", "long cat"],
        base_url="https://api.longcat.chat/v1",
        chat_endpoint="/chat/completions",
        models_endpoint="/models",
        default_free_models=["longcat-chat", "longcat-coder"],
        base_priority=70,
    ),
}


def find_provider_spec(name_or_alias: str) -> Optional[ProviderSpec]:
    """Finds provider spec by matching exact ID, name, or any registered alias."""
    norm = name_or_alias.strip().lower()
    if norm in PROVIDER_SPECS:
        return PROVIDER_SPECS[norm]
    
    for spec in PROVIDER_SPECS.values():
        if norm == spec.name.lower():
            return spec
        if any(norm == alias.lower() for alias in spec.aliases):
            return spec
    
    # Fuzzy fallback match
    for spec in PROVIDER_SPECS.values():
        if norm in spec.name.lower() or any(norm in alias.lower() for alias in spec.aliases):
            return spec
            
    return None
