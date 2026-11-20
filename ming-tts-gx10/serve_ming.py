"""Ming-3.0-tts-0.5B with a BATCHED decode loop: production model server for Listen (2026-11-12).

Also answers the Breeze batch API (POST /v1/audio/speech/batch {texts, ref_audio_b64,
ref_text, seed} -> {sample_rate, pcm16_b64}), so the long-form service runs on it unchanged.

The reference sampler runs one fragment at a time and is launch-bound (one CPU
thread at 100 %, ~30 steps/s), so separate processes do not scale. Here every
fragment of every text in a request is left-padded into one batch: one Qwen2
step + one flow-matching solve (CFG inside) per decode step for all rows, each
row stopping on its own stop head. Same prompt/clone conditioning as
MingTTS.synthesize(task="TTS", use_spk_emb=True, text_normalization=False).

POST /render {texts, seed, max_rows?} -> {sample_rate: 24000, pcm16_b64: [...]}
"""
import base64, hashlib, os, sys, tempfile, threading, time
import torch, torchaudio, soundfile as sf
from fastapi import FastAPI

frac = float(os.environ.get("GPU_MEM_FRACTION", "0"))
if frac:
    torch.cuda.set_per_process_memory_fraction(frac)

def _sf_load(path, *a, **k):  # torchaudio 2.9 load() needs torchcodec
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    return torch.from_numpy(data.T.copy()), rate
torchaudio.load = _sf_load
sys.path.insert(0, "/opt/ming/code")
import tts_tasks
from ming_tts import MingTTS, trim_trailing_silence
from modeling_bailingmm import MAX_CACHE_LEN

MODEL = os.environ["MING_MODEL"]
REF_WAV, REF_TXT = "/voices/calm_explainer_f.wav", open("/voices/calm_explainer_f.txt").read().strip()
MAX_ROWS = int(os.environ.get("MAX_ROWS", "32"))
tts = MingTTS(MODEL, text_normalization=False)
m = tts.model
dev = next(m.parameters()).device
app = FastAPI()
lock = threading.Lock()
stats = {}


@torch.inference_mode()
def decode_batch(frags, req, prompt_latent, spk_emb, prompt_text):
    embs = []
    for f in frags:
      with torch.autocast(device_type="cuda", dtype=torch.bfloat16):  # as MingTTS.generate does
        _, e = m.prepare_input_embed(prompt=req["prompt"], text=f, spk_emb=[x.clone() for x in spk_emb],
                                     instruction=req["instruction"], prompt_latent=prompt_latent,
                                     prompt_text=prompt_text, use_zero_spk_emb=False)
        embs.append(e[0])
    B, L, D = len(embs), max(e.shape[0] for e in embs), embs[0].shape[1]
    x = torch.zeros(B, L, D, dtype=embs[0].dtype, device=dev)
    am = torch.zeros(B, L, dtype=torch.long, device=dev)
    for i, e in enumerate(embs):
        x[i, L - e.shape[0]:] = e
        am[i, L - e.shape[0]:] = 1
    pos = (am.cumsum(-1) - 1).masked_fill(am == 0, 1)
    budget = (MAX_CACHE_LEN - L) // m.patch_size
    hist = None
    ends = [None] * B
    outs = []
    pkv = None
    steps = 0
    for step in range(min(1000, budget)):
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            out = m.model(attention_mask=am, position_ids=pos, inputs_embeds=x, audio_mask=None, image_mask=None,
                          output_hidden_states=True, return_dict=True, use_cache=True, past_key_values=pkv)
            pkv = out.past_key_values
            z = out.hidden_states[-1][:, -1:, :]
            if hist is None:
                hist = torch.zeros(B, m.history_patch_size, m.latent_dim, device=dev, dtype=z.dtype)
                if prompt_latent is not None:
                    pl = prompt_latent.to(z.dtype)
                    start = m.history_patch_size - pl.size(1)
                    if start < 0:
                        hist[:] = pl[:, -start:, :]
                    else:
                        hist[:, start:, :] = pl
            s = m.flowloss.sample(z, hist, req["cfg"], m.patch_size, sigma=req["sigma"], temperature=req["temperature"])
            outs.append(s)
            steps = step + 1
            if step > 3:
                stop = (m.stop_head(z)[:, 0].softmax(dim=-1)[:, 1] > 0.5).tolist()
                for i, st in enumerate(stop):
                    if st and ends[i] is None:
                        ends[i] = step + 1
            if all(e is not None for e in ends):
                break
            x = m.linear_proj_audio(s)
            am = torch.cat([am, torch.ones(B, 1, dtype=am.dtype, device=dev)], dim=1)
            pos = pos[:, -1:] + 1
            hist[:, :-m.patch_size, :] = hist[:, m.patch_size:, :].clone()
            hist[:, -m.patch_size:, :] = s
    lat = torch.cat(outs, dim=1)  # (B, steps * patch, latent)
    wavs = []
    with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
        for i in range(B):
            n = (ends[i] or steps) * m.patch_size
            speech = m.audio.decode(lat[i:i + 1, :n], past_key_values=None, use_cache=False, last_chunk=True)[0]
            wavs.append(trim_trailing_silence(speech.cpu().float()[0], tts.sample_rate))
    return wavs, steps, sum(e is None for e in ends)


@app.get("/health")
def health():
    return {"ok": True, "gpu_gb": round(torch.cuda.max_memory_reserved() / 1e9, 2), "last": stats}


@app.post("/v1/audio/speech/batch")
def breeze_batch(body: dict):
    """Breeze-compatible: the reference arrives as WAV bytes; cached on disk by content hash."""
    wav = base64.b64decode(body["ref_audio_b64"])
    path = os.path.join(tempfile.gettempdir(), "ref-" + hashlib.sha256(wav).hexdigest()[:16] + ".wav")
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(wav)
    out = render({"texts": body["texts"], "seed": body.get("seed", 0), "ref_wav": path, "ref_text_value": body["ref_text"]})
    return {"sample_rate": out["sample_rate"], "pcm16_b64": out["pcm16_b64"]}


@app.post("/render")
def render(body: dict):
    texts = body["texts"]
    max_rows = int(body.get("max_rows", MAX_ROWS))
    ref_wav = body.get("ref_wav", REF_WAV)
    ref_txt = body.get("ref_text_value") or (open(body["ref_txt"]).read().strip() if body.get("ref_txt") else REF_TXT)
    with lock:
        t0 = time.time()
        req = tts_tasks.build_task_generation_kwargs("TTS", text=texts[0], language_id="English", prompt_wav_path=ref_wav,
                                                     prompt_text=ref_txt, use_spk_emb=True)
        prompt_latent, spk_emb = tts.prepare_prompt(req["prompt_wav_path"], req["use_spk_emb"])
        frags, owner = [], []
        for t_i, text in enumerate(texts):
            r = tts_tasks.build_task_generation_kwargs("TTS", text=text, language_id="English", prompt_wav_path=ref_wav,
                                                       prompt_text=ref_txt, use_spk_emb=True)
            for f in tts.prepare_text(r["text"], r["taskname"], r["text_language"], int(body.get("max_length", os.environ.get("MAX_LENGTH", "24")))):
                frags.append(f); owner.append(t_i)
        # similar lengths together keeps padding and run-on rows down
        order = sorted(range(len(frags)), key=lambda i: len(frags[i]))
        pieces = [None] * len(frags)
        torch.manual_seed(int(body.get("seed", 0)))
        total_steps, unstopped = 0, 0
        for k in range(0, len(order), max_rows):
            idx = order[k:k + max_rows]
            wavs, steps, unst = decode_batch([frags[i] for i in idx], req, prompt_latent, spk_emb, ref_txt)
            total_steps += steps; unstopped += unst
            for i, w in zip(idx, wavs):
                pieces[i] = w
        out = []
        for t_i in range(len(texts)):
            wav = torch.cat([pieces[i] for i in range(len(frags)) if owner[i] == t_i], dim=-1)
            wav = torchaudio.functional.resample(wav.unsqueeze(0) if wav.ndim == 1 else wav, tts.sample_rate, 24000)
            out.append(base64.b64encode((wav.clamp(-1, 1)[0] * 32767).to(torch.int16).numpy().tobytes()).decode())
        stats.update(fragments=len(frags), steps=total_steps, unstopped=unstopped, wall_s=round(time.time() - t0, 1))
    return {"sample_rate": 24000, "pcm16_b64": out, "stats": dict(stats)}
