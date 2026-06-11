"""Provides SEC EDGAR data access utilities."""

# Standard library imports
import os
import re
from collections.abc import Iterator
from datetime import date, timedelta

# Application imports
from idi_ftm2j_shared.api import SecClient
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.storage import _get_s3_client, load_json
from idi_ftm2j_shared.types import DiscoveredFiling, ScrapedDocument, ScrapedFiling

_logger = get_logger(__name__)

_CRAWLER_IDX_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/crawler.{date}.idx"
)

# After the company name column, fields are separated by runs of 2+ spaces.
# Form types can contain single spaces (e.g. "SCHEDULE 13G/A"), so we use
# \s{2,} — the padding between columns — as the field boundary.
_FIELDS_RE = re.compile(
    r"(.+?)\s{2,}"  # form type — ends at first run of 2+ spaces
    r"(\d+)\s+"  # CIK
    r"(\d{8})\s+"  # date filed (YYYYMMDD)
    r"(https?://\S+)"  # URL
)

# ---------------------------------------------------------------------------
# S3 path helpers
# ---------------------------------------------------------------------------

_S3_ROOT = "sec"
# Characters not in this set are replaced with "_" when used in S3 keys.
_SAFE_RE = re.compile(r"[^0-9a-zA-Z!._*'()-]")


def s3_prefix(
    form_type: str,
    filing_date: date,
    cik: str,
    accession_number: str,
) -> str:
    """Return the S3 key prefix (no trailing slash) for all files in a filing.

    Pattern: ``sec/{filing_date}/{form_type_safe}/{cik}/{accession_nodash}``

    Args:
        form_type: SEC form type (e.g. ``"10-K"``).
        filing_date: Date the filing was submitted.
        cik: SEC CIK number.
        accession_number: SEC accession number (dashes included or omitted).

    Returns:
        S3 key prefix string without a leading or trailing slash.
    """
    form_type_safe = _SAFE_RE.sub("_", form_type)
    accession_nodash = accession_number.replace("-", "")
    return f"{_S3_ROOT}/{filing_date}/{form_type_safe}/{cik}/{accession_nodash}"


# ---------------------------------------------------------------------------
# Daily index
# ---------------------------------------------------------------------------


def _quarter(d: date) -> int:
    return (d.month - 1) // 3 + 1


def _daily_index_url(d: date) -> str:
    return _CRAWLER_IDX_URL.format(
        year=d.year,
        quarter=_quarter(d),
        date=d.strftime("%Y%m%d"),
    )


def _parse_yyyymmdd(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def _accession_from_url(url: str) -> str:
    return url.rsplit("/", 1)[-1].removesuffix("-index.htm")


def _find_form_type_col(lines: list[str]) -> int | None:
    """Return the column index where 'Form Type' starts in the header, or None."""
    for line in lines:
        idx = line.find("Form Type")
        if idx >= 0:
            return idx
    return None


def _parse_daily_index(text: str) -> Iterator[DiscoveredFiling]:
    """Yield DiscoveredFiling rows parsed from the SEC crawler.idx fixed-width format."""
    lines = text.splitlines()
    form_type_col = _find_form_type_col(lines)
    if form_type_col is None:
        return

    for line in lines:
        if len(line) <= form_type_col:
            continue
        company_name = line[:form_type_col].strip()
        match = _FIELDS_RE.match(line[form_type_col:])
        if not match:
            continue
        url = match.group(4)
        yield DiscoveredFiling(
            company_name=company_name,
            form_type=match.group(1).strip(),
            cik=match.group(2),
            filing_date=_parse_yyyymmdd(match.group(3)),
            accession_number=_accession_from_url(url),
            url=url,
        )


def get_daily_index(
    start_date: date,
    end_date: date,
    client: SecClient | None = None,
) -> Iterator[DiscoveredFiling]:
    """Yield all filings from SEC EDGAR daily crawler indexes for each day in [start_date, end_date].

    Fetches SEC EDGAR daily index files and yields one unfiltered ``DiscoveredFiling``
    per row. Days with no index file (weekends, holidays) are silently skipped.

    Args:
        start_date: First date to fetch (inclusive).
        end_date: Last date to fetch (inclusive).
        client: ``SecClient`` to use for requests. When omitted a default client
            is created using the ``SEC_USER_AGENT`` environment variable, which
            must be set or a ``ValueError`` is raised.

    Yields:
        ``DiscoveredFiling`` instances with ``company_name``, ``form_type``, ``cik``,
        ``filing_date``, ``accession_number``, and ``url`` fields.

    Raises:
        ValueError: If ``start_date`` is after ``end_date``.
    """
    if start_date > end_date:
        raise ValueError(f"start_date {start_date} is after end_date {end_date}")

    if client is None:
        client = SecClient()

    return _iter_daily_index(start_date, end_date, client)


def _iter_daily_index(
    start_date: date,
    end_date: date,
    client: SecClient,
) -> Iterator[DiscoveredFiling]:
    current = start_date
    while current <= end_date:
        url = _daily_index_url(current)
        result = client.query_endpoint(sec_url=url, return_json=False)
        if "error" in result:
            _logger.error("Error fetching daily index for %s: %s", current, result["error"])
        elif result.get("data"):
            yield from _parse_daily_index(result["data"])
        current += timedelta(days=1)


# ---------------------------------------------------------------------------
# Scraped filing manifest
# ---------------------------------------------------------------------------


def _manifest_key(form_type: str, filing_date: date, cik: str, accession_number: str) -> str:
    return f"{s3_prefix(form_type, filing_date, cik, accession_number)}/manifest.json"


def _load_filing(bucket: str, key: str) -> ScrapedFiling | None:
    """Load and deserialise a manifest.json from S3.

    Returns ``None`` when the key does not exist or the object is empty.
    """
    data = load_json(f"s3://{bucket}/{key}", return_type="dict")
    if not data:
        return None
    documents = [ScrapedDocument(**d) for d in data.pop("documents", [])]
    return ScrapedFiling(**{**data, "documents": documents})


def get_filing(
    form_type: str,
    filing_date: date,
    cik: str,
    accession_number: str,
    *,
    bucket: str = "",
) -> ScrapedFiling | None:
    """Return the manifest for a single filing, or ``None`` if it does not exist.

    Args:
        form_type: SEC form type (e.g. ``"10-K"``).
        filing_date: Date the filing was submitted.
        cik: SEC CIK number.
        accession_number: SEC accession number (dashes included or omitted).
        bucket: S3 bucket name. Falls back to the ``BUCKET_NAME`` environment
            variable when omitted.

    Returns:
        Deserialised ``ScrapedFiling``, or ``None`` when the manifest is absent.

    Raises:
        ValueError: If no bucket is provided and ``BUCKET_NAME`` is not set.
    """
    resolved_bucket = bucket or os.environ.get("BUCKET_NAME", "")
    if not resolved_bucket:
        raise ValueError(
            "A bucket name is required. Pass bucket= or set the BUCKET_NAME environment variable."
        )
    return _load_filing(
        resolved_bucket, _manifest_key(form_type, filing_date, cik, accession_number)
    )


def iter_filings_by_form_type(
    form_types: str | list[str],
    start_date: date,
    end_date: date,
    *,
    bucket: str = "",
    include_failures: bool = False,
) -> Iterator[ScrapedFiling]:
    """Yield scraped filing manifests from S3 matching form type and date range.

    Lists ``manifest.json`` objects under ``{form_type}/{YYYY-MM-DD}/`` prefixes
    and yields deserialised ``ScrapedFiling`` instances. By default filings with
    a non-empty ``failure_reason`` are skipped; pass ``include_failures=True`` to
    include them.

    Args:
        form_types: One form type string or a list of form type strings
            (e.g. ``"13F-HR"`` or ``["10-K", "10-K405"]``).
        start_date: First date to include (inclusive).
        end_date: Last date to include (inclusive).
        bucket: S3 bucket name. Falls back to the ``BUCKET_NAME`` environment
            variable when omitted.
        include_failures: When ``True``, also yield filings whose
            ``failure_reason`` is non-empty.

    Yields:
        Deserialised ``ScrapedFiling`` instances.

    Raises:
        ValueError: If ``start_date`` is after ``end_date`` or no bucket is set.
    """
    if start_date > end_date:
        raise ValueError(f"start_date {start_date} is after end_date {end_date}")

    resolved_bucket = bucket or os.environ.get("BUCKET_NAME", "")
    if not resolved_bucket:
        raise ValueError(
            "A bucket name is required. Pass bucket= or set the BUCKET_NAME environment variable."
        )

    return _iter_filings_by_form_type(
        form_types, start_date, end_date, resolved_bucket, include_failures
    )


def _iter_filings_by_form_type(
    form_types: str | list[str],
    start_date: date,
    end_date: date,
    bucket: str,
    include_failures: bool,
) -> Iterator[ScrapedFiling]:
    if isinstance(form_types, str):
        form_types = [form_types]

    paginator = _get_s3_client().get_paginator("list_objects_v2")
    current = start_date
    while current <= end_date:
        for form_type in form_types:
            form_type_safe = _SAFE_RE.sub("_", form_type)
            prefix = f"{_S3_ROOT}/{current.isoformat()}/{form_type_safe}/"
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key: str = obj["Key"]
                    if not key.endswith("/manifest.json"):
                        continue
                    filing = _load_filing(bucket, key)
                    if filing is None:
                        continue
                    if not include_failures and filing.failure_reason:
                        continue
                    yield filing
        current += timedelta(days=1)


def iter_filings_by_discovered(
    filings: list[DiscoveredFiling],
    *,
    bucket: str = "",
    include_failures: bool = False,
) -> Iterator[ScrapedFiling]:
    """Yield scraped filing manifests from S3 for a list of discovered filings.

    Fetches the ``manifest.json`` for each
    :class:`~idi_ftm2j_shared.types.DiscoveredFiling`, useful after filtering
    the output of :func:`get_daily_index`. By default filings with a non-empty
    ``failure_reason`` are skipped; pass ``include_failures=True`` to include
    them.

    Args:
        filings: Discovered filings to fetch manifests for.
        bucket: S3 bucket name. Falls back to the ``BUCKET_NAME`` environment
            variable when omitted.
        include_failures: When ``True``, also yield filings whose
            ``failure_reason`` is non-empty.

    Yields:
        Deserialised ``ScrapedFiling`` instances.

    Raises:
        ValueError: If no bucket is provided and ``BUCKET_NAME`` is not set.
    """
    resolved_bucket = bucket or os.environ.get("BUCKET_NAME", "")
    if not resolved_bucket:
        raise ValueError(
            "A bucket name is required. Pass bucket= or set the BUCKET_NAME environment variable."
        )
    return _iter_filings_by_discovered(filings, resolved_bucket, include_failures)


def _iter_filings_by_discovered(
    filings: list[DiscoveredFiling],
    bucket: str,
    include_failures: bool,
) -> Iterator[ScrapedFiling]:
    for discovered in filings:
        filing = _load_filing(
            bucket,
            _manifest_key(
                discovered.form_type,
                discovered.filing_date,
                discovered.cik,
                discovered.accession_number,
            ),
        )
        if filing is None:
            continue
        if not include_failures and filing.failure_reason:
            continue
        yield filing
