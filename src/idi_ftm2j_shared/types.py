"""Shared data types for SEC EDGAR data access."""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class DiscoveredFiling:
    """A filing discovered from a SEC EDGAR daily crawler index."""

    cik: str
    accession_number: str
    form_type: str
    filing_date: date
    url: str
    company_name: str


@dataclass
class ScrapedDocument:
    """A single document within a scraped filing."""

    filename: str
    url: str
    description: str = ""
    type: str = ""
    seq: str = ""
    s3_key: str = ""


@dataclass
class ScrapedFiling:
    """Manifest of a scraped filing written to S3 as manifest.json."""

    cik: str
    accession_number: str
    form_type: str
    filing_date: str
    last_scraped_at: str
    index_url: str
    company_name: str
    report_date: str = ""
    failure_reason: str = ""
    documents: list[ScrapedDocument] = field(default_factory=list)
