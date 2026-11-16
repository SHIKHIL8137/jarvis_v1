// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, Root } from 'react-dom/client';

// @ts-ignore
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../lib/api', () => ({
  fetchSpeechHealth: vi.fn(async () => ({ available: true })),
  transcribeAudio: vi.fn(async () => ({ text: 'test speech' })),
  synthesizeSpeech: vi.fn(async () => new Blob(['fake-audio'], { type: 'audio/wav' })),
  interruptSpeech: vi.fn(async () => true),
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
  let spokenUtterances: any[] = [];
  let cancelledCalls = 0;
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    spokenUtterances = [];
    cancelledCalls = 0;
    mockMessages = [];
    mockStreamState = { isStreaming: false };

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    // Mock speechSynthesis with full API spec
    const mockSynth = {
      speaking: false,
      paused: false,
      pending: false,
      getVoices: () => [
        { name: 'en-US-Natural', lang: 'en-US', default: true } as any,
      ],
      speak: vi.fn((u: any) => {
        spokenUtterances.push(u);
        if (u.onstart) u.onstart();
      }),
      cancel: vi.fn(() => {
        cancelledCalls += 1;
      }),
    };
    vi.stubGlobal('speechSynthesis', mockSynth);

    // Mock SpeechSynthesisUtterance
    class MockUtterance {
      text: string;
      rate = 1.0;
      voice: any = null;
      onstart: (() => void) | null = null;
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(text: string) {
        this.text = text;
      }
    }
    vi.stubGlobal('SpeechSynthesisUtterance', MockUtterance);

    // Mock Web Audio and mediaDevices with disconnect methods
    vi.stubGlobal('AudioContext', class {
      createMediaStreamSource() {
        return { connect: vi.fn(), disconnect: vi.fn() };
      }
      createScriptProcessor() {
        return { connect: vi.fn(), disconnect: vi.fn(), onaudioprocess: null };
      }
      close() {
        return Promise.resolve();
      }
    });

    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        getUserMedia: vi.fn(async () => ({
          getTracks: () => [{ stop: vi.fn() }],
        })),
      },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  // Helper to mount and send assistant message
  async function renderAndSendAssistantMessage(content: string, isStreaming = false) {
    mockMessages = [{ id: 'm-user-1', role: 'user', content: 'prompt' }];
    mockStreamState = { isStreaming: false };

    await act(async () => {
      root.render(<VoiceMode onClose={() => {}} />);
    });

    // Now update store with new assistant message
    mockMessages = [
      { id: 'm-user-1', role: 'user', content: 'prompt' },
      { id: 'm-asst-2', role: 'assistant', content },
    ];
    mockStreamState = { isStreaming };

    await act(async () => {
      root.render(<VoiceMode onClose={() => {}} />);
    });
  }

  it('renders display text with emojis intact in the visible UI', async () => {
    await renderAndSendAssistantMessage("That's great! 😊 🚀");

    expect(container.textContent).toContain('Jarvis · Voice');
    expect(container.textContent).toContain("That's great! 😊 🚀");
  });

  // 1. Simple emoji: "Hello 😊" -> spoken: "Hello"
  it('condition 1: simple emoji "Hello 😊" speaks "Hello" without emoji while visible text retains emoji', async () => {
    await renderAndSendAssistantMessage('Hello 😊');

    expect(spokenUtterances.length).toBeGreaterThan(0);
    const spokenText = spokenUtterances.map((u) => u.text).join(' ');
    expect(spokenText).toBe('Hello');
    expect(spokenText).not.toContain('😊');
    expect(container.textContent).toContain('Hello 😊');
  });

  // 2. Punctuation + emoji: "Great work 🎉!" -> spoken: "Great work!"
  it('condition 2: punctuation + emoji "Great work 🎉!" speaks "Great work!" without emoji', async () => {
    await renderAndSendAssistantMessage('Great work 🎉!');

    expect(spokenUtterances.length).toBeGreaterThan(0);
    const spokenText = spokenUtterances.map((u) => u.text).join(' ');
    expect(spokenText).toBe('Great work!');
    expect(spokenText).not.toContain('🎉');
    expect(container.textContent).toContain('Great work 🎉!');
  });

  // 3. Country flag: "India 🇮🇳 is beautiful." -> spoken: "India is beautiful."
  it('condition 3: regional indicator flag "India 🇮🇳 is beautiful." speaks "India is beautiful."', async () => {
    await renderAndSendAssistantMessage('India 🇮🇳 is beautiful.');

    expect(spokenUtterances.length).toBeGreaterThan(0);
    const spokenText = spokenUtterances.map((u) => u.text).join(' ');
    expect(spokenText).toBe('India is beautiful.');
    expect(spokenText).not.toContain('🇮🇳');
    expect(container.textContent).toContain('India 🇮🇳 is beautiful.');
  });

  // 4. ZWJ sequence (developer): "Developer 👨‍💻" -> spoken: "Developer"
  it('condition 4: ZWJ sequence "Developer 👨‍💻" speaks "Developer"', async () => {
    await renderAndSendAssistantMessage('Developer 👨‍💻');

    expect(spokenUtterances.length).toBeGreaterThan(0);
    const spokenText = spokenUtterances.map((u) => u.text).join(' ');
    expect(spokenText).toBe('Developer');
    expect(spokenText).not.toContain('👨');
    expect(container.textContent).toContain('Developer 👨‍💻');
  });

  // 5. ZWJ sequence (family): "Family 👨‍👩‍👧‍👦" -> spoken: "Family"
  it('condition 5: family ZWJ compound "Family 👨‍👩‍👧‍👦" speaks "Family"', async () => {
    await renderAndSendAssistantMessage('Family 👨‍👩‍👧‍👦');

    expect(spokenUtterances.length).toBeGreaterThan(0);
    const spokenText = spokenUtterances.map((u) => u.text).join(' ');
    expect(spokenText).toBe('Family');
    expect(spokenText).not.toContain('👨');
    expect(container.textContent).toContain('Family 👨‍👩‍👧‍👦');
  });

  // 6. Skin tone modifier: "Good job 👍🏽" -> spoken: "Good job"
  it('condition 6: skin tone modifier "Good job 👍🏽" speaks "Good job"', async () => {
    await renderAndSendAssistantMessage('Good job 👍🏽');

    expect(spokenUtterances.length).toBeGreaterThan(0);
    const spokenText = spokenUtterances.map((u) => u.text).join(' ');
    expect(spokenText).toBe('Good job');
    expect(spokenText).not.toContain('👍');
    expect(container.textContent).toContain('Good job 👍🏽');
  });

  // 7. Keycap sequence: "Step 1️⃣ complete." -> spoken: "Step complete."
  it('condition 7: keycap sequence "Step 1️⃣ complete." speaks without keycap emoji', async () => {
    await renderAndSendAssistantMessage('Step 1️⃣ complete.');

    expect(spokenUtterances.length).toBeGreaterThan(0);
    const spokenText = spokenUtterances.map((u) => u.text).join(' ');
    expect(spokenText).toBe('Step complete.');
    expect(spokenText).not.toContain('1️⃣');
    expect(container.textContent).toContain('Step 1️⃣ complete.');
  });

  // 8. Emoji-only response: "😊🎉" -> spoken: "" (silence / not spoken)
  it('condition 8: emoji-only response "😊🎉" does not invoke speech synthesis speak', async () => {
    await renderAndSendAssistantMessage('😊🎉');

    // No utterances should be queued for speaking
    expect(spokenUtterances.length).toBe(0);
    expect(container.textContent).toContain('😊🎉');
  });

  // 9. Streaming response: chunks progressively received -> spoken parts strip emojis
  it('condition 9: streaming response receives chunks progressively and strips emojis', async () => {
    mockMessages = [{ id: 'm-user-1', role: 'user', content: 'prompt' }];
    mockStreamState = { isStreaming: false };

    await act(async () => {
      root.render(<VoiceMode onClose={() => {}} />);
    });

    // Chunk 1 (sentence 1 with emoji, still streaming)
    mockMessages = [
      { id: 'm-user-1', role: 'user', content: 'prompt' },
      { id: 'm-asst-stream', role: 'assistant', content: 'Hello there! 😊 ' },
    ];
    mockStreamState = { isStreaming: true };

    await act(async () => {
      root.render(<VoiceMode onClose={() => {}} />);
    });

    expect(spokenUtterances.length).toBe(1);
    expect(spokenUtterances[0].text).toBe('Hello there!');
    expect(spokenUtterances[0].text).not.toContain('😊');

    // Chunk 2 (sentence 2 with emoji, streaming complete)
    mockMessages = [
      { id: 'm-user-1', role: 'user', content: 'prompt' },
      { id: 'm-asst-stream', role: 'assistant', content: 'Hello there! 😊 How can I help you? 🚀' },
    ];
    mockStreamState = { isStreaming: false };

    await act(async () => {
      root.render(<VoiceMode onClose={() => {}} />);
    });

    const allSpoken = spokenUtterances.map((u) => u.text).join(' ');
    expect(allSpoken).toBe('Hello there! How can I help you?');
    expect(allSpoken).not.toContain('😊');
    expect(allSpoken).not.toContain('🚀');
    expect(container.textContent).toContain('😊');
    expect(container.textContent).toContain('🚀');
  });

  // 10. Barge-in / interruption: user clicks Stop or speaks -> speechSynthesis.cancel() called
  it('condition 10: barge-in interruption calls speechSynthesis.cancel()', async () => {
    await renderAndSendAssistantMessage('Long ongoing explanation that can be interrupted. 📢');

    expect(spokenUtterances.length).toBeGreaterThan(0);

    // Find and click the Stop button
    const stopButton = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Stop'),
    );
    expect(stopButton).toBeDefined();

    await act(async () => {
      stopButton?.click();
    });

    expect(cancelledCalls).toBeGreaterThan(0);
    expect(window.speechSynthesis.cancel).toHaveBeenCalled();
  });

  // 11. Multi-sentence with multiple emojis: speakable text has no emojis, visible text retains emojis
  it('condition 11: multi-sentence "Welcome! 🚀 Let\'s start. 💡" speaks cleanly and keeps DOM emojis', async () => {
    await renderAndSendAssistantMessage("Welcome! 🚀 Let's start. 💡");

    expect(spokenUtterances.length).toBeGreaterThan(0);
    const spokenText = spokenUtterances.map((u) => u.text).join(' ');
    expect(spokenText).toBe("Welcome! Let's start.");
    expect(spokenText).not.toContain('🚀');
    expect(spokenText).not.toContain('💡');
    expect(container.textContent).toContain("Welcome! 🚀 Let's start. 💡");
  });

  // Direct helper unit tests
  it('ensures toSpeakableText strips emojis while preserving words', () => {
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
