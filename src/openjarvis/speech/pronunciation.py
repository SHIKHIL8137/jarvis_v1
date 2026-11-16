"""Intelligent pronunciation normalization and technical phrasing for natural speech synthesis.

Converts technical terminology, programming languages, database names,
abbreviations, version numbers, dates, currency, and formatting into
fluent, naturally spoken English suitable for a sophisticated AI assistant.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# Default pronunciation dictionary for technical words, acronyms, and frameworks
DEFAULT_PRONUNCIATION_MAP: Dict[str, str] = {
    # System and Assistant identity
    "jarvis": "Jarvis",
    "j.a.r.v.i.s.": "Jarvis",
    "openjarvis": "Open Jarvis",

    # Architectural & Computing Acronyms
    "api": "A.P.I.",
    "apis": "A.P.I.s",
    "cli": "C.L.I.",
    "clis": "C.L.I.s",
    "gui": "gooey",
    "guis": "gooeys",
    "ui": "U.I.",
    "ux": "U.X.",
    "cpu": "C.P.U.",
    "cpus": "C.P.U.s",
    "gpu": "G.P.U.",
    "gpus": "G.P.U.s",
    "vram": "V-Ram",
    "ram": "Ram",
    "rom": "Rom",
    "ssd": "S.S.D.",
    "hdd": "H.D.D.",
    "os": "O.S.",
    "sdk": "S.D.K.",
    "sdks": "S.D.K.s",
    "llm": "L.L.M.",
    "llms": "L.L.M.s",
    "tts": "T.T.S.",
    "stt": "S.T.T.",
    "asr": "A.S.R.",
    "pr": "pull request",
    "prs": "pull requests",
    "ci/cd": "C.I. C.D.",
    "ci": "C.I.",
    "cd": "C.D.",
    "pwa": "P.W.A.",
    "iot": "I.O.T.",
    "uuid": "U.U.I.D.",
    "uuids": "U.U.I.D.s",
    "guid": "G.U.I.D.",
    "rest": "Rest",
    "restful": "Restful",
    "json": "Jason",
    "yaml": "Yammel",
    "toml": "Tommel",
    "xml": "X.M.L.",
    "html": "H.T.M.L.",
    "css": "C.S.S.",
    "sql": "Sequel",
    "nosql": "No Sequel",
    "sqlite": "S.Q.Lite",
    "mysql": "My Sequel",
    "postgresql": "Postgres Q.L.",
    "postgres": "Postgres",
    "mongodb": "Mongo D.B.",
    "db": "database",
    "dbs": "databases",
    "http": "H.T.T.P.",
    "https": "H.T.T.P.S.",
    "url": "U.R.L.",
    "urls": "U.R.L.s",
    "uri": "U.R.I.",
    "uris": "U.R.I.s",
    "dns": "D.N.S.",
    "tcp": "T.C.P.",
    "udp": "U.D.P.",
    "ip": "I.P.",
    "ssh": "S.S.H.",
    "ssl": "S.S.L.",
    "tls": "T.L.S.",
    "jwt": "J.W.T.",
    "oauth": "O Auth",
    "saml": "Sammel",
    "cors": "Cors",
    "csrf": "C.S.R.F.",
    "ssrf": "S.S.R.F.",
    "xss": "X.S.S.",

    # Programming Languages & Runtimes
    "python": "Python",
    "pytorch": "Pie-Torch",
    "tensorflow": "TensorFlow",
    "scikit-learn": "SciKit Learn",
    "numpy": "Num-Pie",
    "scipy": "Sigh-Pie",
    "pandas": "Pandas",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "ecmascript": "EcmaScript",
    "node.js": "Node J.S.",
    "nodejs": "Node J.S.",
    "deno": "Dee-no",
    "bun": "Bun",
    "rust": "Rust",
    "golang": "Go lang",
    "c++": "C plus plus",
    "c#": "C sharp",
    "dotnet": ".NET",
    ".net": "dot net",
    "php": "P.H.P.",
    "ruby": "Ruby",
    "kotlin": "Kotlin",
    "swift": "Swift",

    # Frameworks & Tools
    "fastapi": "Fast A.P.I.",
    "django": "Django",
    "flask": "Flask",
    "uvicorn": "Uvi-corn",
    "gunicorn": "Guni-corn",
    "pydantic": "Pie-dantic",
    "react": "React",
    "vue": "View",
    "vue.js": "View J.S.",
    "next.js": "Next J.S.",
    "nextjs": "Next J.S.",
    "nuxt": "Nuxt",
    "vite": "Veet",
    "webpack": "Webpack",
    "rollup": "Rollup",
    "tailwind": "Tailwind",
    "docker": "Docker",
    "kubernetes": "Koo-ber-net-ees",
    "k8s": "K-eights",
    "nginx": "Engine X",
    "redis": "Red-iss",
    "kafka": "Kafka",
    "rabbitmq": "Rabbit M.Q.",
    "celery": "Celery",
    "graphql": "Graph Q.L.",
    "grpc": "G.R.P.C.",
    "protobuf": "Proto-buf",
    "onnx": "Onnx",
    "onnxruntime": "Onnx Runtime",
    "huggingface": "Hugging Face",
    "github": "Git-Hub",
    "gitlab": "Git-Lab",
    "bitbucket": "Bitbucket",
    "jira": "Jeera",
    "confluence": "Confluence",
    "slack": "Slack",
    "n8n": "N-eight-N",
    "whisper": "Whisper",
    "kokoro": "Koh-koro",
    "piper": "Piper",

    # AI Providers & Models
    "openai": "Open A.I.",
    "anthropic": "Anthropic",
    "claude": "Claude",
    "deepseek": "Deep Seek",
    "gemini": "Gemini",
    "chatgpt": "Chat G.P.T.",
    "gpt-4": "G.P.T. four",
    "gpt-4o": "G.P.T. four-o",
    "gpt-5": "G.P.T. five",
    "llama": "Lah-ma",
    "mistral": "Mistral",
    "mixtral": "Mixtral",
    "qwen": "Qwen",
    "gemma": "Jemma",
    "phi": "Fie",

    # Unix & Command Line commands
    "sudo": "soo-doo",
    "chmod": "ch-mod",
    "chown": "ch-own",
    "grep": "grep",
    "sed": "sed",
    "awk": "awk",
    "stdin": "standard in",
    "stdout": "standard out",
    "stderr": "standard error",
    "regex": "reg-ex",
    "regexp": "reg-ex-p",
    "nan": "not a number",
    "null": "null",
    "boolean": "boolean",
    "bool": "bool",
    "async": "ay-sync",
    "await": "a-wait",
    "enum": "ee-num",
    "tuple": "toople",
    "pip": "pip",
    "pypi": "Pie P.I.",
    "npm": "N.P.M.",
    "npx": "N.P.X.",
    "pnpm": "P.N.P.M.",
    "yarn": "Yarn",
    "pytest": "Pie-test",
    "vitest": "Vee-test",
}

# Number and unit pronunciation patterns
_CURRENCY_PATTERNS = [
    (r"\$([0-9]+(?:\.[0-9]+)?)\s*[bB]", r"\1 billion dollars"),
    (r"\$([0-9]+(?:\.[0-9]+)?)\s*[mM]", r"\1 million dollars"),
    (r"\$([0-9]+(?:\.[0-9]+)?)\s*[kK]", r"\1 thousand dollars"),
    (r"\$([0-9]+)\.([0-9]{2})\b", r"\1 dollars and \2 cents"),
    (r"\$([0-9]+)\b", r"\1 dollars"),
    (r"£([0-9]+)\.([0-9]{2})\b", r"\1 pounds and \2 pence"),
    (r"£([0-9]+)\b", r"\1 pounds"),
    (r"€([0-9]+)\.([0-9]{2})\b", r"\1 euros and \2 cents"),
    (r"€([0-9]+)\b", r"\1 euros"),
]

_UNIT_PATTERNS = [
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*ms\b", r"\1 milliseconds"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*s\b", r"\1 seconds"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*min\b", r"\1 minutes"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*hr?s?\b", r"\1 hours"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*kb\b", r"\1 kilobytes"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*mb\b", r"\1 megabytes"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*gb\b", r"\1 gigabytes"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*tb\b", r"\1 terabytes"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*ghz\b", r"\1 gigahertz"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*mhz\b", r"\1 megahertz"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*khz\b", r"\1 kilohertz"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*fps\b", r"\1 frames per second"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*%\b", r"\1 percent"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*deg\s*c\b", r"\1 degrees Celsius"),
    (r"\b([0-9]+(?:\.[0-9]+)?)\s*deg\s*f\b", r"\1 degrees Fahrenheit"),
]

# Version pattern: e.g. v1.2.3 -> version 1.2.3
_VERSION_PATTERN = re.compile(r"\bv([0-9]+(?:\.[0-9]+)+)\b", re.IGNORECASE)

# ISO date pattern: YYYY-MM-DD -> Month Day, Year
_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]


def _format_iso_date(match: re.Match) -> str:
    try:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            # Ordinal suffix
            if 11 <= day <= 13:
                suffix = "th"
            else:
                suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
            return f"{_MONTH_NAMES[month]} {day}{suffix}, {year}"
    except Exception:
        pass
    return match.group(0)


class PronunciationNormalizer:
    """Normalizes response text for natural, dignified speech synthesis."""

    def __init__(self, custom_dictionary: Optional[Dict[str, str]] = None) -> None:
        self.dictionary: Dict[str, str] = dict(DEFAULT_PRONUNCIATION_MAP)
        if custom_dictionary:
            self.dictionary.update({k.lower(): v for k, v in custom_dictionary.items()})

    def add_term(self, term: str, pronunciation: str) -> None:
        """Add or override a pronunciation term."""
        self.dictionary[term.lower()] = pronunciation

    def normalize(self, text: str) -> str:
        """Normalize raw text into fluent, articulate spoken prose."""
        if not text:
            return ""

        t = text

        # 1. Strip think / internal chain-of-thought tags
        t = re.sub(r"<think>[\s\S]*?</think>\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"^[\s\S]*?</think>\s*", "", t, flags=re.IGNORECASE)

        # 2. Replace markdown code blocks with dignified brief transition
        t = re.sub(r"```[a-zA-Z0-9_-]*\s*\n[\s\S]*?\n```", " The code block is displayed on screen. ", t)
        t = re.sub(r"```[\s\S]*?```", " The code block is displayed on screen. ", t)

        # 3. Clean inline code tokens: keep the content, strip backticks
        t = re.sub(r"`([^`]+)`", r"\1", t)

        # 4. Clean markdown links: [Text](url) -> Text
        t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)

        # 5. Clean standalone URLs (read as link reference rather than reading characters)
        def _replace_url(match: re.Match) -> str:
            url = match.group(0)
            if "github.com" in url:
                return "GitHub link"
            if "docs." in url:
                return "documentation link"
            return "web link"

        t = re.sub(r"https?://\S+", _replace_url, t)

        # 6. Clean markdown headers (# Header -> Header)
        t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.MULTILINE)

        # 7. Convert bullet points and numbered lists to natural conversational phrasing
        t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.MULTILINE)
        t = re.sub(r"^\s*(\d+)\.\s+", r"\1: ", t, flags=re.MULTILINE)

        # 8. Clean markdown styling: bold, italics, strikethrough
        t = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", t)
        t = re.sub(r"~~(.*?)~~", r"\1", t)

        # 9. Normalize version numbers: v1.2.3 -> version 1.2.3
        t = _VERSION_PATTERN.sub(r"version \1", t)

        # 10. Normalize ISO dates: 2026-11-16 -> November 16th, 2026
        t = re.sub(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", _format_iso_date, t)

        # 11. Normalize currencies ($50M, $100.50, etc.)
        for pattern, replacement in _CURRENCY_PATTERNS:
            t = re.sub(pattern, replacement, t)

        # 12. Normalize units of measurement
        for pattern, replacement in _UNIT_PATTERNS:
            t = re.sub(pattern, replacement, t, flags=re.IGNORECASE)

        # 13. Common code operators into spoken words
        operator_map = [
            (r"\s*!=\s*", " is not equal to "),
            (r"\s*==\s*", " equals "),
            (r"\s*>=\s*", " is greater than or equal to "),
            (r"\s*<=\s*", " is less than or equal to "),
            (r"\s*(?:=>|->)\s*", " yields "),
            (r"\s*&&\s*", " and "),
            (r"\s*\|\|\s*", " or "),
        ]
        for op_pat, op_word in operator_map:
            t = re.sub(op_pat, op_word, t)

        # 14. Pronunciation dictionary lookup (word boundary matching)
        for term, spoken in self.dictionary.items():
            # Match whole words, case-insensitively
            pattern = rf"\b{re.escape(term)}\b"
            t = re.sub(pattern, spoken, t, flags=re.IGNORECASE)

        # 15. Strip Unicode emojis
        emoji_pattern = re.compile(
            r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]"
        )
        t = emoji_pattern.sub("", t)

        # 16. Normalize punctuation and whitespace for natural pauses
        # Collapse multiple spaces and tabs
        t = re.sub(r"[ \t]+", " ", t)
        # Ensure punctuation doesn't have leading space
        t = re.sub(r"\s+([.!?,;:])", r"\1", t)
        # Collapse multiple dots that aren't ellipsis
        t = re.sub(r"\.{4,}", "...", t)
        # Clean up empty lines
        lines = [line.strip() for line in t.splitlines() if line.strip()]
        # Add period to lines lacking terminal punctuation for realistic sentence cadence
        formatted_lines = []
        for line in lines:
            if line and not line[-1] in ".!?:;":
                formatted_lines.append(f"{line}.")
            else:
                formatted_lines.append(line)

        result = " ".join(formatted_lines).strip()
        return result


_GLOBAL_NORMALIZER = PronunciationNormalizer()


def get_pronunciation_normalizer() -> PronunciationNormalizer:
    """Return the global PronunciationNormalizer singleton."""
    return _GLOBAL_NORMALIZER


def normalize_for_speech(text: str) -> str:
    """Convenience function to normalize text for natural speech synthesis."""
    return _GLOBAL_NORMALIZER.normalize(text)

