import { useCallback, useEffect, useRef, useState } from 'react';
import { Mic, MicOff, Square, X } from 'lucide-react';
import { useAppStore } from '../../lib/store';
import { transcribeAudio, fetchSpeechHealth } from '../../lib/api';
import { stripThinkTags, toSpeakableText, stripEmojis } from '../../lib/message-text';

type VState = 'starting' | 'listening' | 'hearing' | 'transcribing' | 'thinking' | 'speaking' | 'error';

const SAMPLE_RATE = 16000;
const FRAME = 1024; // ~64 ms at 16 kHz
const PRE_ROLL_FRAMES = 10; // keep ~0.65 s before speech starts
const START_FRAMES = 3; // ~190 ms of voice to start an utterance
const BARGE_FRAMES = 8; // ~510 ms of voice to interrupt Jarvis
const END_SILENCE_FRAMES = 19; // ~1.2 s of silence ends the utterance
const MIN_UTTERANCE_FRAMES = 6;

const LABEL: Record<VState, string> = {
  starting: 'Starting microphone…',
  listening: 'Listening — just talk',
  hearing: 'Hearing you…',
  transcribing: 'Understanding…',
  thinking: 'Thinking…',
  speaking: 'Speaking — talk to interrupt',
  error: 'Voice unavailable',
};

const COLOR: Record<VState, string> = {
  starting: '#64748b',
  listening: '#22d3ee',
  hearing: '#4ade80',
  transcribing: '#a78bfa',
  thinking: '#fbbf24',
  speaking: '#38bdf8',
  error: '#f87171',
};

function encodeWav(chunks: Float32Array[]): Blob {
  const len = chunks.reduce((n, c) => n + c.length, 0);
  const buf = new ArrayBuffer(44 + len * 2);
  const v = new DataView(buf);
  const w = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, 'RIFF'); v.setUint32(4, 36 + len * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, SAMPLE_RATE, true); v.setUint32(28, SAMPLE_RATE * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, len * 2, true);
  let o = 44;
  for (const c of chunks) {
    for (let i = 0; i < c.length; i++, o += 2) {
      v.setInt16(o, Math.max(-1, Math.min(1, c[i])) * 0x7fff, true);
    }
  }
  return new Blob([buf], { type: 'audio/wav' });
}

export function VoiceMode({ onClose }: { onClose: () => void }) {
  const [vstate, setVstate] = useState<VState>('starting');
  const [muted, setMuted] = useState(false);
  const [level, setLevel] = useState(0);
  const [heard, setHeard] = useState('');
  const [error, setError] = useState('');

  const stateRef = useRef<VState>('starting');
  const mutedRef = useRef(false);
  const setState = (s: VState) => { stateRef.current = s; setVstate(s); };

  const streamState = useAppStore((s) => s.streamState);
  const messages = useAppStore((s) => s.messages);
  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant');
  const rawContent = lastAssistant?.content ?? '';
  const displayReply = stripThinkTags(rawContent);
  const speakableReply = toSpeakableText(rawContent);
  const showReply = heard !== '' && displayReply.length > 0;

  // spoken-text bookkeeping (refs: updated outside render)
  const spokenIdxRef = useRef(0);
  const pendingRef = useRef(0); // utterances queued or playing
  const awaitingReplyRef = useRef(false);
  const streamingRef = useRef(false);
  const replyRef = useRef('');
  replyRef.current = speakableReply;
  streamingRef.current = streamState.isStreaming;
  // The reply we are waiting for is the first assistant message after this id.
  const lastIdRef = useRef<string | undefined>(undefined);
  const baseIdRef = useRef<string | undefined>(undefined);
  lastIdRef.current = lastAssistant?.id;
  const isNewReply = () => lastIdRef.current !== baseIdRef.current;

  const speakChunk = useCallback((text: string) => {
    const cleaned = stripEmojis(text).trim();
    if (!cleaned || !('speechSynthesis' in window)) return;
    const u = new SpeechSynthesisUtterance(cleaned);
    const voices = window.speechSynthesis.getVoices();
    u.voice = voices.find((v) => /en[-_](GB|US|IN)/i.test(v.lang) && /natural|online/i.test(v.name))
      ?? voices.find((v) => /^en/i.test(v.lang)) ?? null;
    u.rate = 1.05;
    pendingRef.current += 1;
    u.onstart = () => { if (stateRef.current !== 'hearing') setState('speaking'); };
    u.onend = u.onerror = () => {
      pendingRef.current = Math.max(0, pendingRef.current - 1);
    };
    window.speechSynthesis.speak(u);
  }, []);

  /** Stop talking and abort the running reply (barge-in / Stop button). */
  const interrupt = useCallback(() => {
    window.speechSynthesis?.cancel();
    pendingRef.current = 0;
    spokenIdxRef.current = replyRef.current.length;
    awaitingReplyRef.current = false;
    window.dispatchEvent(new CustomEvent('jarvis:voice-interrupt'));
  }, []);

  // Speak the reply sentence by sentence while it streams in.
  useEffect(() => {
    if (!awaitingReplyRef.current || !isNewReply()) return;
    const text = speakableReply;
    const rest = text.slice(spokenIdxRef.current);
    const done = !streamState.isStreaming;
    const m = [...rest.matchAll(/[^.!?\n]+[.!?]+(\s|$)|[^\n]+\n/g)];
    const upto = m.length ? m[m.length - 1].index! + m[m.length - 1][0].length : 0;
    const chunk = done ? rest : rest.slice(0, upto);
    if (chunk.trim()) {
      speakChunk(chunk);
      spokenIdxRef.current += chunk.length;
    }
    if (stateRef.current === 'thinking' && text) setState('thinking');
  }, [speakableReply, streamState.isStreaming, speakChunk]);

  // Back to listening once the reply is fully spoken.
  useEffect(() => {
    const t = setInterval(() => {
      if (
        awaitingReplyRef.current && isNewReply() && !streamingRef.current && pendingRef.current === 0 &&
        spokenIdxRef.current >= replyRef.current.length &&
        (stateRef.current === 'speaking' || stateRef.current === 'thinking')
      ) {
        awaitingReplyRef.current = false;
        if (replyRef.current || stateRef.current === 'speaking') setState('listening');
      }
    }, 250);
    return () => clearInterval(t);
  }, []);

  // Microphone + voice-activity detection + utterance capture.
  useEffect(() => {
    let cancelled = false;
    let ctx: AudioContext | null = null;
    let stream: MediaStream | null = null;
    let node: ScriptProcessorNode | null = null;

    const ring: Float32Array[] = [];
    let utter: Float32Array[] = [];
    let voiceRun = 0;
    let silenceRun = 0;
    let floor = 0.01;

    const finish = async (frames: Float32Array[]) => {
      if (frames.length < MIN_UTTERANCE_FRAMES) { setState('listening'); return; }
      setState('transcribing');
      try {
        const res = await transcribeAudio(encodeWav(frames), 'voice.wav');
        const text = (res.text || '').trim();
        if (!text || cancelled) { setState('listening'); return; }
        setHeard(text);
        spokenIdxRef.current = 0;
        baseIdRef.current = lastIdRef.current;
        awaitingReplyRef.current = true;
        setState('thinking');
        window.dispatchEvent(new CustomEvent('jarvis:voice-send', { detail: text }));
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Transcription failed');
        setState('listening');
      }
    };

    const onFrame = (data: Float32Array) => {
      if (mutedRef.current) { voiceRun = 0; return; }
      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
      const rms = Math.sqrt(sum / data.length);
      setLevel(Math.min(1, rms * 12));

      const s = stateRef.current;
      const jarvisTalking = s === 'speaking' || s === 'thinking';
      if (!jarvisTalking && s !== 'hearing') floor = floor * 0.95 + rms * 0.05; // adapt to room noise
      // Speaker bleed is louder than a quiet room: demand more while Jarvis talks.
      const thr = Math.max(0.015, floor * 3) * (jarvisTalking ? 2.5 : 1);
      const voice = rms > thr;
      const copy = new Float32Array(data);

      if (s === 'listening' || jarvisTalking) {
        ring.push(copy);
        if (ring.length > PRE_ROLL_FRAMES + BARGE_FRAMES) ring.shift();
        voiceRun = voice ? voiceRun + 1 : 0;
        const need = jarvisTalking ? BARGE_FRAMES : START_FRAMES;
        if (voiceRun >= need) {
          if (jarvisTalking) interrupt();
          utter = ring.slice(-(PRE_ROLL_FRAMES + need));
          silenceRun = 0;
          voiceRun = 0;
          setState('hearing');
        }
      } else if (s === 'hearing') {
        utter.push(copy);
        silenceRun = voice ? 0 : silenceRun + 1;
        if (silenceRun >= END_SILENCE_FRAMES) {
          const frames = utter;
          utter = [];
          silenceRun = 0;
          void finish(frames);
        }
      }
    };

    (async () => {
      try {
        const health = await fetchSpeechHealth();
        if (!health.available) throw new Error('Speech-to-text backend is not available.');
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
        const src = ctx.createMediaStreamSource(stream);
        node = ctx.createScriptProcessor(FRAME, 1, 1);
        node.onaudioprocess = (e) => onFrame(e.inputBuffer.getChannelData(0));
        const sink = ctx.createGain();
        sink.gain.value = 0; // keep the graph running without playing the mic back
        src.connect(node); node.connect(sink); sink.connect(ctx.destination);
        setState('listening');
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Microphone unavailable');
        setState('error');
      }
    })();

    return () => {
      cancelled = true;
      node?.disconnect();
      stream?.getTracks().forEach((t) => t.stop());
      void ctx?.close();
      window.speechSynthesis?.cancel();
    };
  }, [interrupt]);

  const close = () => { interrupt(); onClose(); };
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  });

  const color = COLOR[vstate];
  const scale = 1 + (vstate === 'hearing' || vstate === 'listening' ? level * 0.35 : 0);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-between py-10 px-6"
      style={{ background: 'radial-gradient(circle at 50% 35%, #0f2a3d 0%, #070b14 70%)', color: '#e2e8f0' }}
      role="dialog"
      aria-label="Voice mode"
    >
      <div className="w-full flex justify-between items-center max-w-2xl">
        <span className="text-sm tracking-widest uppercase" style={{ color: '#94a3b8' }}>Jarvis · Voice</span>
        <button onClick={close} aria-label="Close voice mode" className="p-2 rounded-lg cursor-pointer" style={{ color: '#94a3b8' }}>
          <X size={20} />
        </button>
      </div>

      <div className="flex flex-col items-center gap-8 w-full max-w-2xl">
        <div
          aria-hidden
          style={{
            width: 190, height: 190, borderRadius: '50%',
            background: `radial-gradient(circle at 35% 30%, ${color}ee, ${color}22 70%)`,
            boxShadow: `0 0 ${40 + level * 90}px ${color}66, inset 0 0 40px ${color}55`,
            transform: `scale(${scale})`,
            transition: 'transform 80ms linear, box-shadow 120ms linear, background 300ms',
            animation: vstate === 'thinking' || vstate === 'transcribing' ? 'pulse 1.2s ease-in-out infinite' : undefined,
          }}
        />
        <div className="text-lg" style={{ color }}>{error && vstate === 'error' ? error : LABEL[vstate]}</div>

        <div className="w-full text-center space-y-3 min-h-[120px]">
          {heard && <p className="text-base" style={{ color: '#94a3b8' }}>“{heard}”</p>}
          {showReply && isNewReply() && <p className="text-xl leading-relaxed" style={{ color: '#f1f5f9' }}>{displayReply.slice(-420)}</p>}
        </div>
      </div>

      <div className="flex items-center gap-4">
        <button
          onClick={() => { mutedRef.current = !muted; setMuted(!muted); }}
          className="flex items-center gap-2 px-5 py-3 rounded-full cursor-pointer"
          style={{ background: muted ? '#7f1d1d' : '#1e293b', border: '1px solid #334155' }}
        >
          {muted ? <MicOff size={18} /> : <Mic size={18} />}
          {muted ? 'Mic off' : 'Mic on'}
        </button>
        <button
          onClick={interrupt}
          className="flex items-center gap-2 px-5 py-3 rounded-full cursor-pointer"
          style={{ background: '#1e293b', border: '1px solid #334155' }}
        >
          <Square size={16} /> Stop
        </button>
      </div>
      <style>{`@keyframes pulse{0%,100%{opacity:.75;transform:scale(1)}50%{opacity:1;transform:scale(1.07)}}`}</style>
    </div>
  );
}
