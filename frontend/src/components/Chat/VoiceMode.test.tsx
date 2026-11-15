import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';

vi.mock('../../lib/api', () => ({
  fetchSpeechHealth: vi.fn(async () => ({ available: true })),
  transcribeAudio: vi.fn(async () => ({ text: 'test speech' })),
}));

let mockMessages: Array<{ id: string; role: string; content: string }> = [];
let mockStreamState = { isStreaming: false };

vi.mock('../../lib/store', () => ({
  useAppStore: (selector: (s: any) => any) =>
    selector({
      messages: mockMessages,
      streamState: mockStreamState,
    }),
}));

import { VoiceMode } from './VoiceMode';
import { stripEmojis, toSpeakableText } from '../../lib/message-text';

describe('VoiceMode Component and Speech Normalization', () => {
  let spokenUtterances: string[] = [];
  let cancelledCalls = 0;

  beforeEach(() => {
    spokenUtterances = [];
    cancelledCalls = 0;
    mockMessages = [];
    mockStreamState = { isStreaming: false };

    // Mock speechSynthesis
    vi.stubGlobal('speechSynthesis', {
      getVoices: () => [],
      speak: (u: any) => {
        spokenUtterances.push(u.text);
      },
      cancel: () => {
        cancelledCalls += 1;
      },
    });

    vi.stubGlobal('SpeechSynthesisUtterance', class {
      text: string;
      rate = 1.0;
      voice: any = null;
      onstart: (() => void) | null = null;
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(text: string) {
        this.text = text;
      }
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('renders display text with emojis intact', () => {
    mockMessages = [
      { id: '1', role: 'user', content: 'How are you?' },
      { id: '2', role: 'assistant', content: "That's great! 😊 🚀" },
    ];

    const html = renderToStaticMarkup(<VoiceMode onClose={() => {}} />);
    expect(html).toContain('Voice mode');
    expect(html).toContain('Jarvis · Voice');
  });

  it('ensures toSpeakableText strips emojis from spoken text while preserving words', () => {
    const raw = "That's great! 😊 🚀 Let's go! 🎉";
    const speakable = toSpeakableText(raw);
    expect(speakable).toBe("That's great! Let's go!");
    expect(speakable).not.toContain('😊');
    expect(speakable).not.toContain('🚀');
    expect(speakable).not.toContain('🎉');
  });

  it('ensures stripEmojis leaves empty string when input has only emojis', () => {
    expect(stripEmojis('🚀✨🎉')).toBe('');
    expect(toSpeakableText('🚀✨🎉')).toBe('');
  });

  it('ensures stripEmojis cleans trailing and leading emojis without stray whitespace', () => {
    expect(stripEmojis("That's great! 😊")).toBe("That's great!");
    expect(stripEmojis('🚀 Launching now.')).toBe('Launching now.');
    expect(stripEmojis('Hello 👋 world')).toBe('Hello world');
    expect(stripEmojis('Really 🤔?')).toBe('Really?');
  });
});
