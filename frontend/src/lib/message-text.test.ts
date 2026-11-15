import { describe, expect, it } from 'vitest';
import { stripEmojis, toSpeakableText } from './message-text';

describe('toSpeakableText and stripEmojis', () => {
  it('removes emphasis markers so they are not read aloud', () => {
    expect(toSpeakableText('This is **very important** and *useful*.')).toBe(
      'This is very important and useful.',
    );
  });

  it('drops headings, bullets and numbering and pauses between lines', () => {
    const out = toSpeakableText('## Steps\n- first\n- second\n1. third');
    expect(out).toBe('Steps. first. second. third.');
  });

  it('keeps link text, drops URLs and inline code ticks', () => {
    expect(toSpeakableText('See [the docs](https://x.dev/a) and run `ls` now. https://x.dev')).toBe(
      'See the docs and run ls now.',
    );
  });

  it('replaces code blocks and removes emoji and think tags', () => {
    const out = toSpeakableText('<think>hmm</think>Done 😀\n```py\nprint(1)\n```');
    expect(out).toContain('code block omitted');
    expect(out).not.toMatch(/print|😀|think|hmm/);
  });

  it('keeps snake_case words intact', () => {
    expect(toSpeakableText('use file_read now')).toBe('use file_read now');
  });

  describe('emoji stripping compliance for voice mode', () => {
    it('1. strips trailing emoji with punctuation ("That\'s great! 😊" -> "That\'s great!")', () => {
      expect(toSpeakableText("That's great! 😊")).toBe("That's great!");
      expect(stripEmojis("That's great! 😊")).toBe("That's great!");
    });

    it('2. strips inline emoji between words ("Hello 👋 world" -> "Hello world")', () => {
      expect(toSpeakableText('Hello 👋 world')).toBe('Hello world');
      expect(stripEmojis('Hello 👋 world')).toBe('Hello world');
    });

    it('3. strips leading emoji ("🚀 Launching the service now." -> "Launching the service now.")', () => {
      expect(toSpeakableText('🚀 Launching the service now.')).toBe('Launching the service now.');
      expect(stripEmojis('🚀 Launching the service now.')).toBe('Launching the service now.');
    });

    it('4. strips symbol emoji ("Task complete! ✅" -> "Task complete!")', () => {
      expect(toSpeakableText('Task complete! ✅')).toBe('Task complete!');
      expect(stripEmojis('Task complete! ✅')).toBe('Task complete!');
    });

    it('5. strips ZWJ compound emojis ("The team is ready: 👨‍💻👩‍🔬" -> "The team is ready:")', () => {
      expect(toSpeakableText('The team is ready: 👨‍💻👩‍🔬')).toBe('The team is ready:');
      expect(stripEmojis('The team is ready: 👨‍💻👩‍🔬')).toBe('The team is ready:');
    });

    it('6. strips regional indicator flags ("Proudly made in 🇺🇸 and 🇯🇵." -> "Proudly made in and.")', () => {
      expect(toSpeakableText('Proudly made in 🇺🇸 and 🇯🇵.')).toBe('Proudly made in and.');
      expect(stripEmojis('Proudly made in 🇺🇸 and 🇯🇵.')).toBe('Proudly made in and.');
    });

    it('7. strips keycaps ("Step 1️⃣: open the file." -> "Step: open the file.")', () => {
      expect(toSpeakableText('Step 1️⃣: open the file.')).toBe('Step: open the file.');
      expect(stripEmojis('Step 1️⃣: open the file.')).toBe('Step: open the file.');
    });

    it('8. strips multi-ZWJ family emojis ("Family emoji test 👨‍👩‍👧‍👦 done." -> "Family emoji test done.")', () => {
      expect(toSpeakableText('Family emoji test 👨‍👩‍👧‍👦 done.')).toBe('Family emoji test done.');
      expect(stripEmojis('Family emoji test 👨‍👩‍👧‍👦 done.')).toBe('Family emoji test done.');
    });

    it('9. strips Fitzpatrick skin tone modified emojis ("Skin tone test 👍🏽 approved." -> "Skin tone test approved.")', () => {
      expect(toSpeakableText('Skin tone test 👍🏽 approved.')).toBe('Skin tone test approved.');
      expect(stripEmojis('Skin tone test 👍🏽 approved.')).toBe('Skin tone test approved.');
    });

    it('10. strips multiple consecutive emojis ("Multiple emojis 🎉 🎊 🥳 in a row!" -> "Multiple emojis in a row!")', () => {
      expect(toSpeakableText('Multiple emojis 🎉 🎊 🥳 in a row!')).toBe('Multiple emojis in a row!');
      expect(stripEmojis('Multiple emojis 🎉 🎊 🥳 in a row!')).toBe('Multiple emojis in a row!');
    });

    it('11. returns empty string for emoji-only text ("Only emojis: 🚀✨🎉" -> "Only emojis:", "🚀✨🎉" -> "")', () => {
      expect(toSpeakableText('Only emojis: 🚀✨🎉')).toBe('Only emojis:');
      expect(toSpeakableText('🚀✨🎉')).toBe('');
      expect(stripEmojis('🚀✨🎉')).toBe('');
    });

    it('12. handles markdown combined with emojis ("**Bold** and *italic* with 💡 idea." -> "Bold and italic with idea.")', () => {
      expect(toSpeakableText('**Bold** and *italic* with 💡 idea.')).toBe(
        'Bold and italic with idea.',
      );
    });

    it('13. strips trailing emoji without final punctuation ("Done 👍" -> "Done")', () => {
      expect(toSpeakableText('Done 👍')).toBe('Done');
      expect(stripEmojis('Done 👍')).toBe('Done');
    });

    it('14. strips emoji before question mark without stray space ("Really 🤔?" -> "Really?")', () => {
      expect(toSpeakableText('Really 🤔?')).toBe('Really?');
      expect(stripEmojis('Really 🤔?')).toBe('Really?');
    });

    it('15. strips emoji between sentences cleanly ("First sentence. 😊 Second sentence." -> "First sentence. Second sentence.")', () => {
      expect(toSpeakableText('First sentence. 😊 Second sentence.')).toBe(
        'First sentence. Second sentence.',
      );
      expect(stripEmojis('First sentence. 😊 Second sentence.')).toBe(
        'First sentence. Second sentence.',
      );
    });
  });
});
