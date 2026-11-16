"""Unit tests for intelligent sentence segmentation and streaming."""

from __future__ import annotations

from openjarvis.speech.segmenter import segment_sentences, stream_sentences


def test_segment_simple_sentences():
    text = "Good day, sir. Systems are fully operational! How may I assist you today?"
    sentences = segment_sentences(text)
    assert len(sentences) == 3
    assert sentences[0] == "Good day, sir."
    assert sentences[1] == "Systems are fully operational!"
    assert sentences[2] == "How may I assist you today?"


def test_protect_abbreviations():
    text = "Dr. Smith arrived at 8 a.m. to meet e.g. the security team, etc. Everything went well."
    sentences = segment_sentences(text)
    assert len(sentences) == 2
    assert "Dr. Smith arrived at 8 a.m. to meet e.g. the security team, etc." == sentences[0]
    assert "Everything went well." == sentences[1]


def test_protect_decimal_numbers_and_versions():
    text = "Version 2.5 of the engine improved throughput by 14.8 percent. It is stable."
    sentences = segment_sentences(text)
    assert len(sentences) == 2
    assert "Version 2.5 of the engine improved throughput by 14.8 percent." == sentences[0]
    assert "It is stable." == sentences[1]


def test_stream_sentences_generator():
    tokens = [
        "Good ", "morning, ", "sir. ", "All ", "diagnostics ",
        "have ", "passed. ", "Ready ", "for ", "commands."
    ]
    received = list(stream_sentences(tokens))
    assert len(received) == 3
    assert received[0] == "Good morning, sir."
    assert received[1] == "All diagnostics have passed."
    assert received[2] == "Ready for commands."


def test_avoid_tiny_fragments():
    text = "Yes. Indeed, we are ready."
    sentences = segment_sentences(text, min_words=3)
    # "Yes." has 1 word (< min_words), so it merges with the next sentence
    assert len(sentences) == 1
    assert "Yes. Indeed, we are ready." in sentences[0]
