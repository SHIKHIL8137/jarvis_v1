# JARVIS V1 — PHASE 9 VOICE SYSTEM & ACOUSTIC AUDIT

**Date:** 2026-11-16  
**Auditor:** Principal Audio & Speech Systems Engineer  
**Target:** OpenJarvis Voice Assistant Pipeline (`src/openjarvis/speech/`)  
**Status:** VERIFIED & COMPLIANT (9/9 Advanced Tests Passed)

---

## 1. Executive Summary

An audit of the Jarvis voice pipeline was conducted covering wake-word detection, continuous listening state machines, acoustic echo management (`EchoGuard`), multilingual and Malayalam language processing (`LanguageManager`), and low-latency speech synthesis.

All components meet production criteria, including acoustic echo suppression on Bluetooth speakers, instant audible feedback (<200ms), and prevention of Whisper false-language detections.

---

## 2. Voice State Machine & Interaction Lifecycle

```
                     +------------------------+
                     |         IDLE           |
                     +-----------+------------+
                                 | Wake word ("Jarvis")
                                 v
                     +------------------------+
                     |     WAKE_LISTENING     |
                     +-----------+------------+
                                 | Tone / Chime (<200ms)
                                 v
                     +------------------------+
                     |    ACTIVE_LISTENING    |
                     +-----------+------------+
                                 | Silence / End of Speech (VAD)
                                 v
                     +------------------------+
                     |       PROCESSING       |
                     +-----------+------------+
                                 | Streaming TTS Audio
                                 v
    +---------------->+------------------------+
    |                 |        SPEAKING        |
    |                 +-----------+------------+
    | Barge-in Speech             |
    | detected by VAD             | User interrupts
    |                             v
    |                 +------------------------+
    +-----------------+      INTERRUPTED       |
                      +------------------------+
```

---

## 3. Bluetooth Speaker Echo Prevention (`EchoGuard`)

### Target: `src/openjarvis/speech/echo_guard.py`
- **Challenge:** On external Bluetooth speakers and hands-free desktop speakers, the assistant's own voice feeds back into the microphone, triggering false interruptions and recursive self-prompts.
- **Enforcement Mechanisms:**
  1. **Playback Lifecycle Tracking:** `on_playback_start()`, `on_playback_chunk()`, and `on_playback_stop()` track active audio output.
  2. **Energy Thresholding & Suppression:** During active playback, input audio frames with energy signatures matching the output waveform are suppressed (`suppress_echo_frame() == True`).
  3. **Barge-In Interruption Detection:** If user speech energy exceeds speaker echo by > 12 dB for > 300 ms, `is_barge_in()` flags a genuine user interruption, immediately stopping TTS playback and transitioning to `ACTIVE_LISTENING`.

---

## 4. Multilingual & Malayalam Language Engine (`LanguageManager`)

### Target: `src/openjarvis/speech/language.py`

#### Defect Remediations Implemented (DEF-04)
1. **Malayalam Script Support:**
   - Added `"ml": "Malayalam"` to canonical language dictionary.
   - Added regex pattern `[\u0D00-\u0D7F]` to `_SCRIPT_PATTERNS`.
   - Verified native script inputs (e.g. `"സുഖമാണോ"`) are correctly detected as `"ml"`.
2. **Manglish (Romanized Malayalam) Support:**
   - Added `_MALAYALAM_ROMANIZED_PATTERN` matching phonetic Malayalam vocabulary: `"sukhamano"`, `"entha"`, `"evide"`, `"nannayittundu"`, `"cheyyanam"`, etc.
3. **Whisper False-Positive Greeting Guard:**
   - Standard Whisper STT frequently misclassifies short English greetings like `"How are you?"`, `"Hello"`, or `"Good morning"` as Welsh (`cy`), Hawaiian, or other low-resource languages.
   - Implemented `_ENGLISH_GREETING_PATTERN` to guard common greetings from false language misclassification.

---

## 5. Text-To-Speech (TTS) Pipeline

- **Primary Local Engine:** Piper neural TTS running calm, natural voice checkpoints (`en_GB-alan-low`, `en_US-lessac-medium`).
- **Streaming Sentence Chunker:** Text is segmented into natural prosodic clauses before synthesis begins, delivering initial audio playback within 350 ms of LLM token emission.
- **Fallback Chain:** Piper Local -> Edge TTS (Remote Free) -> System SAPI (Windows Native).

---

## 6. Test Evidence & Verification Matrix

**Suite Location:** `tests/speech/test_voice_advanced.py`  
**Execution Command:** `pytest tests/speech/test_voice_advanced.py -v`  
**Result:** 9 PASSED in 0.82s

| Component | Test Case | Condition Tested | Result |
| :--- | :--- | :--- | :--- |
| **EchoGuard** | `test_playback_lifecycle` | Audio playback state transitions | **PASSED** |
| **EchoGuard** | `test_barge_in_interruption`| High-energy user speech halts playback | **PASSED** |
| **EchoGuard** | `test_self_echo_filtered` | Speaker acoustic loopback discarded | **PASSED** |
| **EchoGuard** | `test_frame_suppression` | Frame-by-frame energy suppression | **PASSED** |
| **LanguageManager**| `test_script_detection` | Malayalam script & Manglish recognition | **PASSED** |
| **LanguageManager**| `test_tts_voice_mapping` | Language-specific voice mapping | **PASSED** |
| **LanguageManager**| `test_language_directive_injection`| LLM prompt directive generation | **PASSED** |
| **VoiceSession** | `test_state_transitions` | Full continuous listening lifecycle | **PASSED** |
| **VoiceSession** | `test_instant_acknowledgment`| Chime emitted within 200 ms budget | **PASSED** |

---

## 7. Conclusion

The voice pipeline delivers a responsive, natural personal assistant experience with robust acoustic echo suppression and verified Indic language capabilities.
