# Unified Multi-Provider LLM Gateway

A high-performance, resilient OpenAI-compatible Unified Gateway that consolidates **32 LLM Providers** behind **ONE UNIFIED API KEY**, with automated free-model discovery, per-model quota tracking, and intelligent dynamic routing (`model="auto"`).

---

## 🏛️ Architecture

```
                    ONE UNIFIED API KEY (Bearer sk-unified-...)
                                      │
                                      ▼
                           LiteLLM / FastAPI Gateway
                                      │
                                Routing Engine
                                      │
             ┌────────────────────────┼────────────────────────┐
             ▼                        ▼                        ▼
        Provider A               Provider B               Provider C
         healthy                  quota OK                exhausted
      40% remaining            80% remaining                 SKIP
             │                        │
             └────────────────────────┴────────► SELECT BEST AVAILABLE
                                                       │
                                                       ▼
                                                   Response
```

---

## 🚀 Quick Start (Windows)

Just double click or run:
```cmd
api.bat
```

This will:
1. Initialize credentials and display your **UNIFIED API KEY**.
2. Display sample Python and cURL code snippets.
3. Launch the Unified Gateway server at `http://127.0.0.1:8000`.

---

## 💻 Sample Code (Python OpenAI SDK)

```python
from openai import OpenAI

# Initialize client pointing to Unified Gateway
client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="<YOUR_UNIFIED_KEY>"  # From unified_key.txt or displayed by api.bat
)

# 1. Dynamic Auto-Routing (routes to best healthy provider with most remaining quota)
response = client.chat.completions.create(
    model="auto",
    messages=[
        {"role": "user", "content": "Explain quantum computing in simple terms."}
    ],
    temperature=0.7
)
print("Response:", response.choices[0].message.content)

# 2. Streaming Response with Dynamic Routing
stream = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Write a poem about space."}],
    stream=True
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

---

## 🎯 Supported Virtual Routing Aliases

| Alias | Description | Target Providers & Models |
|---|---|---|
| `auto` | Dynamic load balancer & health scoring | Routes to highest score healthy provider with maximum remaining quota |
| `fast` | Ultra low-latency responses | Groq, Cerebras, Gemini Flash |
| `smart` | High-reasoning complex tasks | Llama 3.3 70B, DeepSeek R1, Gemini Pro |
| `coder` | Code generation & refactoring | Codestral, Qwen 2.5 Coder 32B |

---

## 📊 Endpoints

- **Chat Completions**: `POST /v1/chat/completions`
- **Models Catalog**: `GET /v1/models`
- **Gateway Health**: `GET /health`
- **Real-Time Quota & Health Stats**: `GET /stats`
- **Interactive Swagger Docs**: `GET /docs`
