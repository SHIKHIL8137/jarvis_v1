"""Unit tests for speech pronunciation normalization."""

from __future__ import annotations

import pytest

from openjarvis.speech.pronunciation import PronunciationNormalizer


def test_normalize_technical_terms():
    text = "Deploy PostgreSQL with FastAPI and Nginx on Docker using PyTorch."
    norm = PronunciationNormalizer.normalize_for_speech(text)
    assert "Postgres Q.L." in norm
    assert "Fast A.P.I." in norm
    assert "Engine X" in norm
    assert "Docker" in norm
    assert "Pie-Torch" in norm


def test_normalize_currencies():
    assert "15 million 500 thousand dollars" in PronunciationNormalizer.normalize_for_speech("$15.5M") or "15.50 million dollars" in PronunciationNormalizer.normalize_for_speech("$15.50M")
    assert "50 pounds" in PronunciationNormalizer.normalize_for_speech("£50")
    assert "100 euros" in PronunciationNormalizer.normalize_for_speech("€100")


def test_normalize_iso_dates():
    text = "The release date is 2026-11-16."
    norm = PronunciationNormalizer.normalize_for_speech(text)
    assert "November 16th, 2026" in norm


def test_normalize_units():
    text = "Latency is 45ms, memory is 16GB, running at 60fps."
    norm = PronunciationNormalizer.normalize_for_speech(text)
    assert "45 milliseconds" in norm
    assert "16 gigabytes" in norm
    assert "60 frames per second" in norm


def test_normalize_versions():
    text = "Running v1.0.4 on production."
    norm = PronunciationNormalizer.normalize_for_speech(text)
    assert "version 1.0.4" in norm


def test_strip_code_blocks():
    text = "Here is the code:\n```python\nprint('hello world')\n```\nIt works!"
    norm = PronunciationNormalizer.normalize_for_speech(text)
    assert "print('hello world')" not in norm
    assert "Here is the code:" in norm
    assert "It works!" in norm


def test_strip_markdown_formatting():
    text = "# Main Title\n**Important:** Check *italics* and [our website](https://openjarvis.ai)."
    norm = PronunciationNormalizer.normalize_for_speech(text)
    assert "#" not in norm
    assert "**" not in norm
    assert "*" not in norm
    assert "https://" not in norm
    assert "Important: Check italics and our website." in norm


def test_strip_emojis():
    text = "Hello there! 😊🎉 Jarvis is online 🚀."
    norm = PronunciationNormalizer.normalize_for_speech(text)
    assert "😊" not in norm
    assert "🎉" not in norm
    assert "🚀" not in norm
    assert "Hello there! Jarvis is online." in norm
