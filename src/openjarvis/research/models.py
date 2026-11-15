"""Data models for Deep Research: evidence, citations, plans, and reports."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EvidenceItem:
    """A verified unit of research evidence extracted from an authoritative source."""

    url: str = ""
    title: str = ""
    passage: str = ""
    exact_quote: str = ""
    publication_date: Optional[str] = None
    access_date: str = field(default_factory=lambda: time.strftime("%Y-%m-%d"))
    source_hash: str = ""
    verified: bool = False
    confidence: float = 1.0

    def __init__(
        self,
        url: str = "",
        title: str = "",
        passage: str = "",
        exact_quote: str = "",
        publication_date: Optional[str] = None,
        access_date: Optional[str] = None,
        source_hash: str = "",
        verified: bool = False,
        confidence: float = 1.0,
        source_url: Optional[str] = None,
        snippet: Optional[str] = None,
        quoted_text: Optional[str] = None,
    ) -> None:
        self.url = source_url if source_url is not None else url
        self.title = title
        self.passage = snippet if snippet is not None else passage
        self.exact_quote = quoted_text if quoted_text is not None else exact_quote
        self.publication_date = publication_date
        self.access_date = access_date or time.strftime("%Y-%m-%d")
        self.source_hash = source_hash
        self.verified = verified
        self.confidence = confidence

        if not self.source_hash and (self.passage or self.exact_quote):
            content = f"{self.url}|{self.exact_quote or self.passage}"
            self.source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    @property
    def source_url(self) -> str:
        return self.url

    @property
    def snippet(self) -> str:
        return self.passage

    @property
    def quoted_text(self) -> str:
        return self.exact_quote

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvidenceItem:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ResearchPlan:
    """Structured plan breaking a research inquiry into verifiable subtopics."""

    query: str
    subtopics: List[str] = field(default_factory=list)
    evidence_needed: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchReport:
    """Structured evidence-backed research report."""

    report_id: str
    query: str
    title: str
    executive_summary: str
    methodology: str
    findings: List[Dict[str, Any]] = field(default_factory=list)
    comparisons: List[Dict[str, Any]] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    evidence: List[EvidenceItem] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    markdown_path: str = ""

    @property
    def topic(self) -> str:
        return self.query

    @property
    def total_sources(self) -> int:
        return len(self.evidence)

    def to_markdown(self) -> str:
        """Render the structured report into a standardized Markdown artifact."""
        lines = [
            f"# {self.title}",
            f"\n**Research Query:** {self.query}",
            f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.created_at))}",
            f"**Report ID:** `{self.report_id}`",
            "\n---\n",
            "## 1. Executive Summary\n",
            self.executive_summary,
            "\n## 2. Research Methodology\n",
            self.methodology,
            "\n## 3. Detailed Findings\n",
        ]

        for finding in self.findings:
            title = finding.get("title", "Finding")
            detail = finding.get("detail", "")
            citations = finding.get("citations", [])
            lines.append(f"### {title}\n")
            lines.append(f"{detail}\n")
            if citations:
                lines.append(f"*Citations: {', '.join(citations)}*\n")

        if self.comparisons:
            lines.append("## 4. Comparisons\n")
            for comp in self.comparisons:
                lines.append(f"- **{comp.get('item', 'Item')}:** {comp.get('summary', '')}")
            lines.append("")

        if self.recommendations:
            lines.append("## 5. Technical Recommendations\n")
            for rec in self.recommendations:
                lines.append(f"- {rec}")
            lines.append("")

        if self.limitations:
            lines.append("## 6. Limitations & Gaps\n")
            for lim in self.limitations:
                lines.append(f"- {lim}")
            lines.append("")

        lines.append("## 7. References & Verified Evidence\n")
        if self.evidence:
            for idx, ev in enumerate(self.evidence, 1):
                status = "✓ Verified" if ev.verified else "⚠ Unverified"
                lines.append(f"[{idx}] **{ev.title}** ({ev.access_date}) — *{status}*")
                lines.append(f"    URL: {ev.url}")
                if ev.exact_quote:
                    lines.append(f"    > \"{ev.exact_quote}\"")
                lines.append("")
        else:
            lines.append("No external references recorded.\n")

        return "\n".join(lines)
