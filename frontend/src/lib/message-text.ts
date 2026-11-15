/** Text helpers shared between the message renderer and voice output. */

/** Remove <think> reasoning blocks so they are neither shown nor spoken. */
export function stripThinkTags(text: string): string {
  let cleaned = text.replace(/<think>[\s\S]*?<\/think>\s*/gi, '');
  cleaned = cleaned.replace(/^[\s\S]*?<\/think>\s*/i, '');
  return cleaned.trim();
}

/**
 * Unicode emoji matcher covering:
 * - Regional indicator symbol pairs (country flags)
 * - Keycap sequences ([0-9#*]\uFE0F?\u20E3)
 * - Fitzpatrick skin tone modifiers (U+1F3FB - U+1F3FF)
 * - Dingbats & Miscellaneous Symbols (U+2600 - U+27BF)
 * - Emoji & Pictograph blocks (U+1F300 - U+1FAFF)
 * - Compound emojis with Zero-Width Joiners (ZWJ, U+200D) and tag sequences
 */
export const EMOJI_REGEX =
  /(?:\p{Regional_Indicator}{2}|[0-9#*]\uFE0F?\u20E3|[\u{1F3FB}-\u{1F3FF}]|[\u2600-\u27BF]|[\u{1F300}-\u{1F64F}]|[\u{1F680}-\u{1F6FF}]|[\u{1F900}-\u{1F9FF}]|[\u{1FA00}-\u{1FAFF}]|\p{Extended_Pictographic}(?:[\uFE0E\uFE0F]|[\u{1F3FB}-\u{1F3FF}]|[\u{E0020}-\u{E007F}])*(?:\u200D(?:\p{Extended_Pictographic}|[\u2600-\u27BF])(?:[\uFE0E\uFE0F]|[\u{1F3FB}-\u{1F3FF}]|[\u{E0020}-\u{E007F}])*)*)/gu;

/**
 * Remove emoji sequences from text for speech synthesis so screen readers
 * or browser SpeechSynthesis do not pronounce emoji Unicode descriptions.
 */
export function stripEmojis(text: string): string {
  let cleaned = text.replace(EMOJI_REGEX, '');
  // Clean up any stray variation selectors, zero-width joiners or unattached modifiers
  cleaned = cleaned.replace(/[\uFE0E\uFE0F\u200D]/g, '');
  cleaned = cleaned.replace(/[\u{1F3FB}-\u{1F3FF}]/gu, '');
  // Clean up whitespace before punctuation (e.g. "Great work !" -> "Great work!")
  cleaned = cleaned.replace(/\s+([.!?,;:])/g, '$1');
  // Collapse multiple spaces into one
  cleaned = cleaned.replace(/[ \t]{2,}/g, ' ');
  return cleaned.trim();
}

/**
 * Turn a (usually markdown) answer into text that sounds right when read aloud: no emphasis
 * markers, heading hashes, list bullets, link syntax, bare URLs or emoji, code blocks replaced
 * by a short note, and a pause after lines that lack final punctuation.
 */
export function toSpeakableText(text: string): string {
  let t = stripThinkTags(text);
  t = t.replace(/```[\s\S]*?```/g, ' (code block omitted). ');
  t = t.replace(/`([^`]*)`/g, '$1');
  t = t.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
  t = t.replace(/https?:\/\/\S+/g, '');
  t = t.replace(/^\s{0,3}#{1,6}\s*/gm, '');
  t = t.replace(/^\s*(?:[-*•]|\d+[.)])\s+/gm, '');
  t = t.replace(/\*{1,3}|~~/g, '');
  t = t.replace(/(?<![\p{L}\p{N}])_+|_+(?![\p{L}\p{N}])/gu, '');
  t = stripEmojis(t);
  const lines = t
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  // Only separate multiple lines; a single line is spoken as written.
  const sentences =
    lines.length > 1 ? lines.map((l) => (/[.!?:;]$/.test(l) ? l : `${l}.`)) : lines;
  return sentences.join(' ').replace(/\s+([.!?,;:])/g, '$1').replace(/\s{2,}/g, ' ').trim();
}
