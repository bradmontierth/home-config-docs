# Beelink Agents: GX10 Music LLM Endpoints

Use this note when an agent or service running on the Beelink needs to call the GX10-hosted music LLM stack.

## Network Context

- Agent host: Beelink, `192.168.10.217`
- LLM host: GX10, `gx10-5398`, `192.168.10.187`
- Do not use `localhost` from Beelink for the LLM services. `localhost` would refer to the Beelink itself.
- No API key is required by these local services.

## OpenAI-Compatible Base URLs

| Purpose | Base URL for Beelink agents | Model name |
| --- | --- | --- |
| Chat completions | `http://192.168.10.187:8095/v1` | `qwen3-next` |
| Embeddings | `http://192.168.10.187:8096/v1` | `qwen3-embedding` |

Current GX10 backing models:

- Chat: `Qwen/Qwen3-30B-A3B-Thinking-2507-FP8`, served as `qwen3-next`
- Embeddings: `Qwen/Qwen3-Embedding-4B`, served as `qwen3-embedding`

## Environment Variables For Agents

Use these defaults in Beelink services, scripts, or agent configs:

```bash
export MUSIC_LLM_CHAT_BASE_URL="http://192.168.10.187:8095/v1"
export MUSIC_LLM_CHAT_MODEL="qwen3-next"
export MUSIC_LLM_EMBED_BASE_URL="http://192.168.10.187:8096/v1"
export MUSIC_LLM_EMBED_MODEL="qwen3-embedding"
export OPENAI_API_KEY="not-used"
```

For libraries that expect OpenAI's conventional variable names, set the chat endpoint as the default OpenAI base:

```bash
export OPENAI_BASE_URL="http://192.168.10.187:8095/v1"
export OPENAI_API_KEY="not-used"
```

Use a separate client or explicit base URL for embeddings because embeddings are served on port `8096`.

## Health Checks From Beelink

```bash
curl http://192.168.10.187:8095/v1/models
curl http://192.168.10.187:8096/v1/models
```

## Chat Completion

```bash
curl http://192.168.10.187:8095/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen3-next",
    "temperature": 0,
    "max_tokens": 200,
    "messages": [
      {
        "role": "system",
        "content": "Return concise JSON only."
      },
      {
        "role": "user",
        "content": "Normalize: Beethoven Sym. 5 Op. 67"
      }
    ]
  }'
```

## Embeddings

```bash
curl http://192.168.10.187:8096/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen3-embedding",
    "input": [
      "Beethoven Symphony No. 5 in C minor, Op. 67",
      "Symphony 5 C minor op67 Beethoven"
    ]
  }'
```

## Python Agent Example

```python
from openai import OpenAI

chat = OpenAI(
    base_url="http://192.168.10.187:8095/v1",
    api_key="not-used",
)

embeddings = OpenAI(
    base_url="http://192.168.10.187:8096/v1",
    api_key="not-used",
)

completion = chat.chat.completions.create(
    model="qwen3-next",
    temperature=0,
    max_tokens=200,
    messages=[
        {"role": "system", "content": "Return concise JSON only."},
        {"role": "user", "content": "Normalize: Beethoven Sym. 5 Op. 67"},
    ],
)
print(completion.choices[0].message.content)

vector_response = embeddings.embeddings.create(
    model="qwen3-embedding",
    input=["Beethoven Symphony No. 5 Op. 67"],
)
print(len(vector_response.data[0].embedding))
```

## Operating Notes

- GX10 service source: `/home/pi/local-llm` (renamed from `music-llm` 2026-07-29 —
  music was the first use case and didn't pan out; this is the house LLM now)
- GX10 endpoint reference: `/home/pi/home_config/gx10-local-llm-openai-endpoints.md` (on the GX10)
- Start services on GX10 with:

```bash
cd /home/pi/local-llm
docker compose up -d qwen-chat qwen-embed
```

- If Beelink calls fail, first verify the GX10 services are up and reachable with the health checks above.
