import http from 'node:http';
import { readFileSync } from 'node:fs';
import { pipeline, env } from '@huggingface/transformers';
import silk from 'silk-wasm';

const PORT = Number(process.env.ASR_PORT || 18321);
const MODEL_ID = process.env.ASR_MODEL || 'Xenova/whisper-base';
const MODEL_DIR = process.env.ASR_MODEL_DIR || '';
const MAX_SECONDS = Math.max(5, Number(process.env.ASR_MAX_SECONDS) || 180);

if (MODEL_DIR) {
  env.localModelPath = MODEL_DIR;
  env.cacheDir = MODEL_DIR;
}
env.allowRemoteModels = false;
env.allowLocalModels = true;
env.useBrowserCache = false;

function pcmToFloat(pcm) {
  const n = Math.floor(pcm.length / 2);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = pcm.readInt16LE(i * 2) / 32768;
  return out;
}

function resample(src, from, to) {
  if (from === to) return src;
  const ratio = to / from;
  const len = Math.round(src.length * ratio);
  const out = new Float32Array(len);
  for (let i = 0; i < len; i++) {
    const pos = i / ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(src.length - 1, i0 + 1);
    const w = pos - i0;
    out[i] = src[i0] * (1 - w) + src[i1] * w;
  }
  return out;
}

let asrPromise = null;
function getAsr() {
  if (!asrPromise) {
    asrPromise = pipeline('automatic-speech-recognition', MODEL_ID, {
      dtype: 'q8',
      device: 'cpu',
    });
    asrPromise.catch(() => { asrPromise = null; });
  }
  return asrPromise;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > 20 * 1024 * 1024) {
        reject(new Error('request too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function json(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
  });
  res.end(body);
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    return json(res, 200, { ok: true, model: MODEL_ID });
  }
  if (req.method !== 'POST' || req.url !== '/transcribe') {
    return json(res, 404, { ok: false, error: 'not found' });
  }
  try {
    const body = JSON.parse(await readBody(req));
    const audioPath = String(body.path || '');
    if (!audioPath) return json(res, 400, { ok: false, error: 'path is required' });

    const raw = readFileSync(audioPath);
    let decoded;
    try {
      decoded = await silk.decode(raw, 24000);
    } catch (error) {
      return json(res, 422, {
        ok: false,
        error: `SILK 解码失败: ${error?.message ?? error}`,
      });
    }
    const pcm = Buffer.from(decoded.data);
    const seconds = pcm.length / 2 / 24000;
    if (seconds > MAX_SECONDS) {
      return json(res, 413, {
        ok: false,
        error: `语音太长：${seconds.toFixed(1)} 秒，上限 ${MAX_SECONDS} 秒`,
      });
    }

    const asr = await getAsr();
    const audio = resample(pcmToFloat(pcm), 24000, 16000);
    const out = await asr(audio, { language: 'chinese', task: 'transcribe' });
    const text = String(out?.text ?? out ?? '').trim();
    return json(res, 200, {
      ok: true,
      text,
      seconds: Number(seconds.toFixed(2)),
      model: MODEL_ID,
    });
  } catch (error) {
    return json(res, 500, { ok: false, error: error?.message ?? String(error) });
  }
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[qq_voice_asr] listening on 127.0.0.1:${PORT} model=${MODEL_ID}`);
});
