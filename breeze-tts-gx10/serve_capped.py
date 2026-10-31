"""Launch breeze_infer.api with a hard cap on this process GPU allocation (GB10 unified memory: GPU allocs are invisible to the OOM killer).

Adds POST /v1/audio/speech/batch: several texts in one voice (pure clone, no
instruction) rendered together. Decoding is memory-bandwidth-bound on the GB10,
so one pass over the weights serves every row: measured 2026-10-30, 4 paragraphs
at once render at ~2.2x real time against 1.0x for one.

The fast path's CUDA graphs only know classifier-free-guidance layouts, 2N rows =
N cond + N uncond. A batch of N texts runs as that layout with the uncond rows an
exact copy and guidance 1.0, which makes the guided logits the cond logits.
N is padded to 1, 2 or 4 (the captured graph sizes) by repeating the last text.
"""
import asyncio, base64, os, sys, tempfile, threading, time, uuid
from dataclasses import replace
from pathlib import Path

import torch

torch.cuda.set_per_process_memory_fraction(float(os.environ.get("GPU_MEM_FRACTION", "0.13")))

import numpy as np
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from breeze_infer import api
from breeze_infer.runtime import set_all_seeds
from breeze_infer.templates import get_template, prepare_inputs, select_template_name
from models.cudagraph.sampling import sample_logits
from models.generation_breeze import _extract_decoded_audio_tensor

BATCH_SIZES = (1, 2, 4)
# A row that never says EOS would run to max_new_tokens (120 s) and hold the whole
# batch; cut it at ~1.75x a normal reading (0.068 s/char at 12.5 frames/s) instead.
# The cut take fails the caller's length check and is redone on its own.
ROW_FRAMES_PER_CHAR = 0.12 * 12.5
ROW_FRAMES_SLACK = 50
_original_load_app = api._load_app


def _load_app(app, settings):
    _original_load_app(app, settings)
    runtime = app.state.runtime
    runtime._frozen_branch_batch_sizes = None  # allow the batch graph sizes below
    # The warmup profile only takes graph sizes that match its guidance scales, so
    # the larger depth-decoder buckets are captured here, after it.
    depth = runtime._depth_decoder_graph
    extra = [2 * n for n in BATCH_SIZES[1:] if 2 * n not in depth._bucket_graphs]
    with torch.inference_mode():
        for rows in extra:
            depth._capture_single_bucket(rows, 8)
        depth.bucket_sizes = sorted(set(depth.bucket_sizes) | set(extra))
        depth._swap_to_bucket(depth._bucket_graphs[depth.bucket_sizes[0]])
        for n in BATCH_SIZES[1:]:
            runtime._ensure_graphs(2 * n, 1.0)
    print(f"batch graphs ready: backbone {sorted(runtime._backbone_graphs)}, depth {depth.bucket_sizes}", flush=True)


api._load_app = _load_app


@torch.inference_mode()
def _render_batch(state, texts, ref_path, ref_text, seed):
    rt, model, tok, at = state.runtime, state.model, state.tokenizer, state.audio_tokenizer
    real = len(texts)
    n = next(b for b in BATCH_SIZES if b >= real)
    texts = texts + [texts[-1]] * (n - real)
    reqs = [{"id": f"b{i}", "text": t, "speaker": "S0", "ref_audio_path": ref_path, "ref_text": ref_text}
            for i, t in enumerate(texts)]
    set_all_seeds(seed)
    inputs = prepare_inputs(tok, at, model, reqs, get_template(select_template_name(reqs[0])),
                            guidance_scale=1.0, guidance_scale_ref=None, guidance_scale_ins=None)
    rows = 1 if n == 1 else 2 * n
    rt._ensure_graphs(rows, 1.0)
    graph, depth = rt._backbone_graph, rt._depth_decoder_graph
    cfg = model.config
    max_new, max_seq = rt.config.max_new_tokens, rt.config.max_seq_len
    bparams = rt._sampling_params(model.generation_config)
    dparams = rt._sampling_params(model.depth_decoder.generation_config)
    penalty = rt.config.repetition_penalty

    emb, mask = rt._merge_branch(input_ids=inputs["input_ids"], attention_mask=inputs["attention_mask"],
                                 text_ids_mask=inputs["text_ids_mask"], text_ids_len=inputs["text_ids_len"],
                                 input_values=inputs.get("input_values"))
    if n > 1:
        emb, mask = torch.cat([emb, emb]), torch.cat([mask, mask])
    pos = mask.long().cumsum(-1) - 1
    pos.masked_fill_(mask == 0, 1)
    out = model.backbone_model(inputs_embeds=emb.contiguous(), attention_mask=mask.contiguous(),
                               position_ids=pos, use_cache=True)
    hidden = out.last_hidden_state
    logits = model.lm_head(hidden[:, -1, :].float()).float()[:n]
    prefill_len = graph.prefill_kv(out.past_key_values)
    graph.set_generation_state(mask)

    history = torch.zeros(n, max_new, dtype=torch.long, device=hidden.device)

    def sample(lg, steps):
        lg = lg.clone()
        if steps and penalty != 1.0:  # HF repetition penalty, per row
            seen = history[:, :steps]
            scores = lg.gather(1, seen)
            lg.scatter_(1, seen, torch.where(scores > 0, scores / penalty, scores * penalty))
        return sample_logits(lg, suppress_tokens=rt._reserved_codec_token_ids, **bparams).view(n)

    token = sample(logits, 0)
    limits = torch.tensor([int(len(t) * ROW_FRAMES_PER_CHAR) + ROW_FRAMES_SLACK for t in texts], device=hidden.device)
    finished = torch.zeros(n, dtype=torch.bool, device=hidden.device)
    lengths = torch.zeros(n, dtype=torch.long, device=hidden.device)
    frames = []
    for step in range(max_new):
        finished |= (token >= cfg.vocab_size) | (lengths >= limits)
        if bool(finished.all()) or prefill_len + step >= max_seq - 1:
            break
        safe = torch.where(finished, torch.zeros_like(token), token)
        codes = depth.run(hidden[:, -1, :], safe.repeat(2) if n > 1 else safe, guidance_scale=1.0, **dparams)
        frame = torch.cat([safe.view(n, 1), codes[:n]], dim=1)
        frames.append(frame)
        lengths += (~finished).long()
        step_in = frame.view(n, 1, -1)
        hidden, logits = graph.run(step_in.repeat(2, 1, 1) if n > 1 else step_in, step_idx=step)
        history[:, step] = token
        token = sample(logits.float()[:n], step + 1)
    lengths = lengths.tolist()
    stacked = torch.stack(frames, dim=1) if frames else None
    audio = []
    for i in range(real):
        if not lengths[i]:
            audio.append(np.zeros(0, dtype=np.float32))
            continue
        wav = _extract_decoded_audio_tensor(at.decode({"audio_codes": [stacked[i, :lengths[i]]]}))
        audio.append(wav.float().cpu().numpy().reshape(-1))
    return audio


@api.app.post("/v1/audio/speech/batch")
async def speech_batch(request: Request) -> JSONResponse:
    body = await request.json()
    texts = [str(t).strip() for t in body.get("texts") or []]
    ref_text = str(body.get("ref_text") or "").strip()
    if not texts or not all(texts) or len(texts) > BATCH_SIZES[-1]:
        raise HTTPException(status_code=400, detail=f"texts must be 1-{BATCH_SIZES[-1]} non-empty strings")
    if not ref_text or not body.get("ref_audio_b64"):
        raise HTTPException(status_code=400, detail="ref_audio_b64 and ref_text are required")
    with tempfile.NamedTemporaryFile(prefix="breeze_ref_", suffix=".wav", delete=False) as handle:
        handle.write(base64.b64decode(body["ref_audio_b64"]))
        ref_path = handle.name
    state = api.app.state

    def run():
        # The single-request endpoint answers 409 while this holds the lock; its callers retry.
        if not api._request_lock.acquire(timeout=900):
            raise HTTPException(status_code=409, detail="Breeze stayed busy")
        try:
            started = time.time()
            audio = _render_batch(state, texts, ref_path, ref_text, int(body.get("seed", 42)))
            seconds = sum(len(a) for a in audio) / state.runtime.sample_rate
            print(f"batch: {len(texts)} texts, {seconds:.1f} s audio in {time.time() - started:.1f} s", flush=True)
            return audio
        finally:
            api._request_lock.release()
            Path(ref_path).unlink(missing_ok=True)

    audio = await asyncio.to_thread(run)
    return JSONResponse({"sample_rate": state.runtime.sample_rate,
                         "pcm16_b64": [base64.b64encode(api._pcm16(a)).decode() for a in audio]})


api.main()
