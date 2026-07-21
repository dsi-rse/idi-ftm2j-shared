"""Tests for sec.py — daily index discovery and S3-backed ScrapedFiling functions."""

import dataclasses
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from botocore.exceptions import ClientError

from idi_ftm2j_shared.sec import (
    _daily_index_url,
    _find_form_type_col,
    _load_filing,
    _manifest_key,
    _parse_daily_index,
    get_daily_index,
    get_filing,
    iter_filings_by_discovered,
    iter_filings_by_form_type,
    s3_prefix,
)
from idi_ftm2j_shared.types import DiscoveredFiling, ScrapedDocument, ScrapedFiling

BUCKET = "test-bucket"

# Minimal crawler.idx fixture matching the real SEC format.
# "Form Type" header label determines the column split point dynamically.
_SAMPLE_IDX = (
    "Description:           Daily Crawler Index\n"
    "Last Data Received:    Apr 1, 2026\n"
    "\n"
    "\n"
    "Company Name                                                  Form Type   CIK\n"
    "      Date Filed  URL \n"
    "-----------------------------------------------------------------------------------\n"
    "20/20 Biolabs, Inc.                                           8-K              1139685     20260401    http://www.sec.gov/Archives/edgar/data/1139685/0001213900-26-037770-index.htm\n"
    "Apple Inc.                                                    10-K             320193      20260401    http://www.sec.gov/Archives/edgar/data/320193/0000320193-26-000123-index.htm\n"
    "Apple Inc.                                                    10-K/A           320193      20260401    http://www.sec.gov/Archives/edgar/data/320193/0000320193-26-000124-index.htm\n"
    "Some Fund                                                     13F-HR           999999      20260401    http://www.sec.gov/Archives/edgar/data/999999/0000999999-26-000001-index.htm\n"
    "Acme Holdings Corp.                                           SCHEDULE 13G/A   888888      20260401    http://www.sec.gov/Archives/edgar/data/888888/0000888888-26-000001-index.htm\n"
)


def _make_filing(
    form_type: str = "10-K",
    filing_date: date = date(2024, 1, 15),
    cik: str = "0001234567",
    accession_number: str = "0001234567-24-000001",
    failure_reason: str = "",
    documents: list | None = None,
) -> ScrapedFiling:
    return ScrapedFiling(
        cik=cik,
        accession_number=accession_number,
        form_type=form_type,
        filing_date=filing_date,
        index_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
        company_name="Acme Corp",
        failure_reason=failure_reason,
        documents=documents or [],
    )


def _as_json_bytes(filing: ScrapedFiling) -> bytes:
    def _default(obj: object) -> str:
        if isinstance(obj, date):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    return json.dumps(dataclasses.asdict(filing), default=_default).encode()


def _no_such_key_error() -> ClientError:
    return ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")


def _mock_s3(mocker) -> MagicMock:
    """Patch storage._get_s3_client and return the mock S3 client."""
    import idi_ftm2j_shared.storage as storage

    storage._s3_client = None
    client = MagicMock()
    mocker.patch("idi_ftm2j_shared.storage._get_s3_client", return_value=client)
    return client


def _manifest_row(
    form_type: str = "10-K",
    filing_date: str = "2024-01-15",
    cik: str = "001",
    accession_number: str = "acc",
    s3_key: str = "s3://test-bucket/doc.htm",
    date_scraped: str = "",
) -> dict:
    """Build one per-document row of the bucket-level manifest.parquet."""
    return {
        "cik": cik,
        "accession_number": accession_number,
        "filing_date": filing_date,
        "form_type": form_type,
        "seq": "1",
        "description": "",
        "filename": "doc.htm",
        "type": form_type,
        "s3_key": s3_key,
        "url": "https://sec.gov/doc.htm",
        "date_scraped": date_scraped,
    }


def _manifest_df(rows: list[dict]) -> pd.DataFrame:
    """Assemble manifest rows into a DataFrame with a UTC date_scraped column."""
    columns = list(_manifest_row().keys())
    df = pd.DataFrame(rows, columns=columns)
    df["date_scraped"] = pd.to_datetime(df["date_scraped"], utc=True, errors="coerce")
    return df


def _get_object_ok(filing: ScrapedFiling) -> dict:
    return {"Body": MagicMock(read=lambda: _as_json_bytes(filing))}


def _make_discovered(
    form_type: str = "10-K",
    filing_date: date = date(2024, 1, 15),
    cik: str = "0001234567",
    accession_number: str = "0001234567-24-000001",
) -> DiscoveredFiling:
    return DiscoveredFiling(
        cik=cik,
        accession_number=accession_number,
        form_type=form_type,
        filing_date=filing_date,
        url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_number}-index.htm",
        company_name="Acme Corp",
    )


@pytest.fixture
def bucket_env(monkeypatch):
    """Set BUCKET_NAME so get_filing and iter_filings can read it."""
    monkeypatch.setenv("BUCKET_NAME", BUCKET)


# ---------------------------------------------------------------------------
# _daily_index_url
# ---------------------------------------------------------------------------


class TestDailyIndexUrl:
    """Tests for _daily_index_url."""

    def test_q1_url(self):
        assert _daily_index_url(date(2026, 1, 15)) == (
            "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR1/crawler.20260115.idx"
        )

    def test_q2_url(self):
        assert _daily_index_url(date(2026, 4, 1)) == (
            "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR2/crawler.20260401.idx"
        )

    def test_q3_url(self):
        assert _daily_index_url(date(2026, 7, 31)) == (
            "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/crawler.20260731.idx"
        )

    def test_q4_url(self):
        assert _daily_index_url(date(2026, 12, 1)) == (
            "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/crawler.20261201.idx"
        )

    def test_uses_crawler_not_form(self):
        url = _daily_index_url(date(2026, 4, 1))
        assert "crawler." in url
        assert "form" not in url


# ---------------------------------------------------------------------------
# _find_form_type_col
# ---------------------------------------------------------------------------


class TestFindFormTypeCol:
    """Tests for _find_form_type_col."""

    def test_finds_column_in_header(self):
        lines = _SAMPLE_IDX.splitlines()
        col = _find_form_type_col(lines)
        assert col is not None
        assert col > 0

    def test_returns_none_when_no_header(self):
        assert _find_form_type_col(["no header here", "just data"]) is None

    def test_returns_none_for_empty_lines(self):
        assert _find_form_type_col([]) is None


# ---------------------------------------------------------------------------
# _parse_daily_index
# ---------------------------------------------------------------------------


class TestParseDailyIndex:
    """Tests for _parse_daily_index."""

    def test_yields_discovered_filings(self):
        results = list(_parse_daily_index(_SAMPLE_IDX))
        assert all(isinstance(r, DiscoveredFiling) for r in results)

    def test_parses_correct_count(self):
        results = list(_parse_daily_index(_SAMPLE_IDX))
        assert len(results) == 5

    def test_parses_company_name(self):
        results = list(_parse_daily_index(_SAMPLE_IDX))
        assert results[0].company_name == "20/20 Biolabs, Inc."

    def test_parses_form_type(self):
        results = list(_parse_daily_index(_SAMPLE_IDX))
        assert results[0].form_type == "8-K"

    def test_parses_cik(self):
        results = list(_parse_daily_index(_SAMPLE_IDX))
        assert results[0].cik == "1139685"

    def test_parses_filing_date_as_date(self):
        results = list(_parse_daily_index(_SAMPLE_IDX))
        assert results[0].filing_date == date(2026, 4, 1)

    def test_parses_accession_number(self):
        results = list(_parse_daily_index(_SAMPLE_IDX))
        assert results[0].accession_number == "0001213900-26-037770"

    def test_parses_absolute_url(self):
        results = list(_parse_daily_index(_SAMPLE_IDX))
        assert results[0].url.startswith("http://www.sec.gov/")

    def test_form_type_with_space(self):
        results = list(_parse_daily_index(_SAMPLE_IDX))
        schedule = next(r for r in results if "SCHEDULE" in r.form_type)
        assert schedule.form_type == "SCHEDULE 13G/A"

    def test_returns_empty_for_no_header(self):
        results = list(_parse_daily_index("no header\njust lines"))
        assert results == []

    def test_returns_empty_for_empty_string(self):
        assert list(_parse_daily_index("")) == []


# ---------------------------------------------------------------------------
# get_daily_index
# ---------------------------------------------------------------------------


def _make_sec_client(mocker, content: str = _SAMPLE_IDX):
    client = mocker.MagicMock()
    client.query_endpoint.return_value = {"status_code": 200, "data": content}
    return client


class TestGetDailyIndex:
    """Tests for get_daily_index."""

    def test_yields_filing_index_rows(self, mocker):
        client = _make_sec_client(mocker)
        results = list(get_daily_index(date(2026, 4, 1), date(2026, 4, 1), client=client))
        assert len(results) == 5

    def test_calls_client_with_crawler_idx_url(self, mocker):
        client = _make_sec_client(mocker)
        list(get_daily_index(date(2026, 4, 1), date(2026, 4, 1), client=client))
        url = client.query_endpoint.call_args.kwargs["sec_url"]
        assert "crawler.20260401.idx" in url

    def test_iterates_each_date_in_range(self, mocker):
        client = _make_sec_client(mocker)
        list(get_daily_index(date(2026, 4, 1), date(2026, 4, 3), client=client))
        assert client.query_endpoint.call_count == 3

    def test_skips_dates_with_error_response(self, mocker):
        client = mocker.MagicMock()
        client.query_endpoint.return_value = {"error": "not found"}
        results = list(get_daily_index(date(2026, 4, 1), date(2026, 4, 1), client=client))
        assert results == []

    def test_skips_dates_with_empty_data(self, mocker):
        client = mocker.MagicMock()
        client.query_endpoint.return_value = {"status_code": 200, "data": ""}
        results = list(get_daily_index(date(2026, 4, 1), date(2026, 4, 1), client=client))
        assert results == []

    def test_raises_if_start_after_end(self, mocker):
        client = _make_sec_client(mocker)
        with pytest.raises(ValueError, match="start_date"):
            list(get_daily_index(date(2026, 4, 2), date(2026, 4, 1), client=client))

    def test_creates_default_client_when_none(self, mocker):
        mock_client = _make_sec_client(mocker)
        with patch("idi_ftm2j_shared.sec.SecClient", return_value=mock_client):
            results = list(get_daily_index(date(2026, 4, 1), date(2026, 4, 1)))
        assert len(results) == 5

    def test_is_a_generator(self, mocker):
        import inspect

        client = _make_sec_client(mocker)
        result = get_daily_index(date(2026, 4, 1), date(2026, 4, 1), client=client)
        assert inspect.isgenerator(result)


# ---------------------------------------------------------------------------
# s3_prefix / filing_s3_prefix
# ---------------------------------------------------------------------------


class TestS3Prefix:
    """Tests for s3_prefix."""

    def test_format(self):
        assert (
            s3_prefix("10-K", date(2024, 1, 15), "0001234567", "0001234567-24-000001")
            == "sec/2024-01-15/10-K/0001234567/000123456724000001"
        )

    def test_sanitizes_form_type_slash(self):
        assert s3_prefix("10-K/A", date(2024, 1, 15), "001", "acc").startswith(
            "sec/2024-01-15/10-K_A/"
        )

    def test_sanitizes_form_type_space(self):
        assert s3_prefix("SCHEDULE 13G/A", date(2024, 1, 15), "001", "acc").startswith(
            "sec/2024-01-15/SCHEDULE_13G_A/"
        )

    def test_removes_dashes_from_accession(self):
        assert s3_prefix("10-K", date(2024, 1, 15), "001", "0001234567-24-000001").endswith(
            "/000123456724000001"
        )

    def test_no_trailing_slash(self):
        assert not s3_prefix("10-K", date(2024, 1, 15), "001", "acc").endswith("/")


# ---------------------------------------------------------------------------
# _manifest_key
# ---------------------------------------------------------------------------


class TestManifestKey:
    """Tests for _manifest_key."""

    def test_format(self):
        key = _manifest_key("10-K", date(2024, 1, 15), "0001234567", "0001234567-24-000001")
        assert key == "sec/2024-01-15/10-K/0001234567/000123456724000001/manifest.json"

    def test_ends_with_manifest_json(self):
        assert _manifest_key("8-K", date(2024, 6, 1), "999", "abc").endswith("/manifest.json")


# ---------------------------------------------------------------------------
# _load_filing — tests through the real storage layer
# ---------------------------------------------------------------------------


class TestLoadFiling:
    """Tests for _load_filing — integrated with the storage module."""

    def test_deserialises_filing(self, mocker):
        s3 = _mock_s3(mocker)
        filing = _make_filing()
        s3.get_object.return_value = _get_object_ok(filing)

        result = _load_filing(BUCKET, "10-K/2024-01-15/001/acc/manifest.json")

        assert result is not None
        assert result.cik == filing.cik
        assert result.form_type == filing.form_type

    def test_returns_none_for_missing_key(self, mocker):
        s3 = _mock_s3(mocker)
        s3.get_object.side_effect = _no_such_key_error()

        assert _load_filing(BUCKET, "missing/manifest.json") is None

    def test_deserialises_documents(self, mocker):
        s3 = _mock_s3(mocker)
        doc = ScrapedDocument(filename="doc.htm", url="https://sec.gov/doc.htm", description="10-K")
        filing = _make_filing(documents=[doc])
        s3.get_object.return_value = _get_object_ok(filing)

        result = _load_filing(BUCKET, "any/key")

        assert result is not None
        assert len(result.documents) == 1
        assert isinstance(result.documents[0], ScrapedDocument)
        assert result.documents[0].filename == "doc.htm"

    def test_empty_documents_list(self, mocker):
        s3 = _mock_s3(mocker)
        filing = _make_filing(documents=[])
        s3.get_object.return_value = _get_object_ok(filing)

        result = _load_filing(BUCKET, "any/key")

        assert result is not None
        assert result.documents == []

    def test_calls_get_object_with_correct_bucket_and_key(self, mocker):
        s3 = _mock_s3(mocker)
        filing = _make_filing()
        s3.get_object.return_value = _get_object_ok(filing)

        _load_filing(BUCKET, "form/date/cik/acc/manifest.json")

        s3.get_object.assert_called_once_with(Bucket=BUCKET, Key="form/date/cik/acc/manifest.json")

    def test_tolerates_legacy_manifest_with_last_scraped_at(self, mocker):
        # Old manifests carry the removed filing-level last_scraped_at and no
        # per-document date_scraped; both must deserialise cleanly.
        s3 = _mock_s3(mocker)
        legacy = {
            "cik": "001",
            "accession_number": "acc",
            "form_type": "10-K",
            "filing_date": "2024-01-15",
            "last_scraped_at": "2024-01-16T00:00:00",
            "index_url": "https://sec.gov/index.htm",
            "company_name": "Acme Corp",
            "documents": [{"filename": "doc.htm", "url": "https://sec.gov/doc.htm"}],
        }
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(legacy).encode())}

        result = _load_filing(BUCKET, "any/key")

        assert result is not None
        assert not hasattr(result, "last_scraped_at")
        assert result.documents[0].date_scraped == ""

    def test_reads_per_document_date_scraped(self, mocker):
        s3 = _mock_s3(mocker)
        doc = ScrapedDocument(
            filename="doc.htm",
            url="https://sec.gov/doc.htm",
            date_scraped="2026-05-16T04:25:07.912991+00:00",
        )
        filing = _make_filing(documents=[doc])
        s3.get_object.return_value = _get_object_ok(filing)

        result = _load_filing(BUCKET, "any/key")

        assert result is not None
        assert result.documents[0].date_scraped == "2026-05-16T04:25:07.912991+00:00"


# ---------------------------------------------------------------------------
# get_filing
# ---------------------------------------------------------------------------


class TestGetFiling:
    """Tests for get_filing."""

    def test_returns_filing_when_present(self, bucket_env):
        filing = _make_filing()
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            result = get_filing("10-K", date(2024, 1, 15), "0001234567", "0001234567-24-000001")
        assert result is filing
        mock_load.assert_called_once_with(
            BUCKET, "sec/2024-01-15/10-K/0001234567/000123456724000001/manifest.json"
        )

    def test_returns_none_when_absent(self, bucket_env):
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=None):
            result = get_filing("10-K", date(2024, 1, 15), "001", "acc")
        assert result is None

    def test_constructs_key_with_normalized_path(self, bucket_env):
        filing = _make_filing()
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            get_filing("8-K", date(2024, 3, 31), "999", "xyz")
        key = mock_load.call_args[0][1]
        assert key == "sec/2024-03-31/8-K/999/xyz/manifest.json"

    def test_removes_dashes_from_accession(self, bucket_env):
        filing = _make_filing()
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            get_filing("10-K", date(2024, 1, 15), "0001234567", "0001234567-24-000001")
        key = mock_load.call_args[0][1]
        assert "000123456724000001" in key
        assert "0001234567-24-000001" not in key

    def test_sanitizes_form_type(self, bucket_env):
        filing = _make_filing()
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            get_filing("10-K/A", date(2024, 1, 15), "001", "acc")
        key = mock_load.call_args[0][1]
        assert "/10-K_A/" in key

    def test_reads_bucket_from_env(self, monkeypatch):
        monkeypatch.setenv("BUCKET_NAME", "custom-bucket")
        filing = _make_filing()
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            get_filing("10-K", date(2024, 1, 15), "001", "acc")
        assert mock_load.call_args[0][0] == "custom-bucket"

    def test_explicit_bucket_overrides_env(self, monkeypatch):
        monkeypatch.setenv("BUCKET_NAME", "env-bucket")
        filing = _make_filing()
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            get_filing("10-K", date(2024, 1, 15), "001", "acc", bucket="explicit-bucket")
        assert mock_load.call_args[0][0] == "explicit-bucket"


# ---------------------------------------------------------------------------
# iter_filings
# ---------------------------------------------------------------------------


class TestIterFilingsByFormType:
    """Tests for iter_filings_by_form_type — served from the manifest parquet."""

    def _patch_parquet(self, mocker, df):
        return mocker.patch("idi_ftm2j_shared.sec._read_manifest_parquet", return_value=df)

    def test_yields_matching_filing(self, mocker, bucket_env):
        filing = _make_filing()
        self._patch_parquet(mocker, _manifest_df([_manifest_row()]))
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing):
            results = list(iter_filings_by_form_type("10-K", date(2024, 1, 15), date(2024, 1, 15)))
        assert results == [filing]

    def test_accepts_single_form_type_string(self, mocker, bucket_env):
        filing = _make_filing()
        self._patch_parquet(mocker, _manifest_df([_manifest_row()]))
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing):
            results = list(iter_filings_by_form_type("10-K", date(2024, 1, 15), date(2024, 1, 15)))
        assert len(results) == 1

    def test_accepts_list_of_form_types(self, mocker, bucket_env):
        filing = _make_filing()
        df = _manifest_df(
            [
                _manifest_row(form_type="10-K", cik="001", accession_number="a1", s3_key="k1"),
                _manifest_row(form_type="8-K", cik="002", accession_number="a2", s3_key="k2"),
                _manifest_row(form_type="13F-HR", cik="003", accession_number="a3", s3_key="k3"),
            ]
        )
        self._patch_parquet(mocker, df)
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            list(iter_filings_by_form_type(["10-K", "8-K"], date(2024, 1, 15), date(2024, 1, 15)))
        # Only the 10-K and 8-K rows match; the 13F-HR row is filtered out.
        assert mock_load.call_count == 2

    def test_filters_by_filing_date_range(self, mocker, bucket_env):
        filing = _make_filing()
        df = _manifest_df(
            [
                _manifest_row(filing_date="2024-01-14", accession_number="before", s3_key="k0"),
                _manifest_row(filing_date="2024-01-15", accession_number="in1", s3_key="k1"),
                _manifest_row(filing_date="2024-01-17", accession_number="in2", s3_key="k2"),
                _manifest_row(filing_date="2024-01-18", accession_number="after", s3_key="k3"),
            ]
        )
        self._patch_parquet(mocker, df)
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            list(iter_filings_by_form_type("10-K", date(2024, 1, 15), date(2024, 1, 17)))
        accessions = {call.args[1].split("/")[4] for call in mock_load.call_args_list}
        assert accessions == {"in1", "in2"}

    def test_filters_by_scraped_date_range(self, mocker, bucket_env):
        filing = _make_filing()
        df = _manifest_df(
            [
                _manifest_row(
                    accession_number="before",
                    s3_key="k0",
                    date_scraped="2026-05-14T23:00:00+00:00",
                ),
                _manifest_row(
                    accession_number="in_start",
                    s3_key="k1",
                    date_scraped="2026-05-15T00:00:01+00:00",
                ),
                _manifest_row(
                    accession_number="in_end",
                    s3_key="k2",
                    date_scraped="2026-05-16T23:59:59+00:00",
                ),
                _manifest_row(
                    accession_number="after",
                    s3_key="k3",
                    date_scraped="2026-05-17T00:00:00+00:00",
                ),
            ]
        )
        self._patch_parquet(mocker, df)
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            list(
                iter_filings_by_form_type(
                    "10-K",
                    date(2026, 5, 15),
                    date(2026, 5, 16),
                    search_by="scraped_date",
                )
            )
        accessions = {call.args[1].split("/")[4] for call in mock_load.call_args_list}
        assert accessions == {"in_start", "in_end"}

    def test_scraped_date_excludes_nat(self, mocker, bucket_env):
        filing = _make_filing()
        df = _manifest_df(
            [
                _manifest_row(accession_number="unscraped", s3_key="k0", date_scraped=""),
                _manifest_row(
                    accession_number="scraped",
                    s3_key="k1",
                    date_scraped="2026-05-16T00:00:00+00:00",
                ),
            ]
        )
        self._patch_parquet(mocker, df)
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            list(
                iter_filings_by_form_type(
                    "10-K", date(2026, 5, 1), date(2026, 5, 31), search_by="scraped_date"
                )
            )
        accessions = {call.args[1].split("/")[4] for call in mock_load.call_args_list}
        assert accessions == {"scraped"}

    def test_deduplicates_documents_of_same_filing(self, mocker, bucket_env):
        filing = _make_filing()
        df = _manifest_df(
            [
                _manifest_row(s3_key="k1", date_scraped="2026-05-16T00:00:00+00:00"),
                _manifest_row(s3_key="k2", date_scraped="2026-05-16T01:00:00+00:00"),
            ]
        )
        self._patch_parquet(mocker, df)
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            results = list(iter_filings_by_form_type("10-K", date(2024, 1, 15), date(2024, 1, 15)))
        # Two documents, one filing: the manifest is loaded and yielded once.
        assert mock_load.call_count == 1
        assert len(results) == 1

    def test_builds_correct_manifest_key(self, mocker, bucket_env):
        filing = _make_filing()
        df = _manifest_df(
            [
                _manifest_row(
                    form_type="10-K/A",
                    filing_date="2024-03-31",
                    cik="999",
                    accession_number="0000999999-24-000001",
                )
            ]
        )
        self._patch_parquet(mocker, df)
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            list(iter_filings_by_form_type("10-K/A", date(2024, 3, 31), date(2024, 3, 31)))
        assert (
            mock_load.call_args[0][1]
            == "sec/2024-03-31/10-K_A/999/000099999924000001/manifest.json"
        )

    def test_returns_empty_when_no_parquet(self, mocker, bucket_env):
        self._patch_parquet(mocker, None)
        results = list(iter_filings_by_form_type("10-K", date(2024, 1, 15), date(2024, 1, 15)))
        assert results == []

    def test_returns_empty_when_parquet_empty(self, mocker, bucket_env):
        self._patch_parquet(mocker, _manifest_df([]))
        results = list(iter_filings_by_form_type("10-K", date(2024, 1, 15), date(2024, 1, 15)))
        assert results == []

    def test_skips_none_from_load(self, mocker, bucket_env):
        self._patch_parquet(mocker, _manifest_df([_manifest_row()]))
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=None):
            results = list(iter_filings_by_form_type("10-K", date(2024, 1, 15), date(2024, 1, 15)))
        assert results == []

    def test_raises_if_start_after_end(self):
        with pytest.raises(ValueError, match="start_date"):
            list(iter_filings_by_form_type("10-K", date(2024, 1, 16), date(2024, 1, 15)))

    def test_raises_on_unknown_search_by(self, bucket_env):
        with pytest.raises(ValueError, match="search_by"):
            list(
                iter_filings_by_form_type(
                    "10-K", date(2024, 1, 15), date(2024, 1, 15), search_by="report_date"
                )
            )

    def test_is_a_generator(self):
        import inspect

        result = iter_filings_by_form_type(
            "10-K", date(2024, 1, 15), date(2024, 1, 15), bucket=BUCKET
        )
        assert inspect.isgenerator(result)

    def test_reads_parquet_from_resolved_bucket(self, mocker, monkeypatch):
        monkeypatch.setenv("BUCKET_NAME", "custom-bucket")
        load_content = mocker.patch("idi_ftm2j_shared.sec.load_content", return_value=b"")
        list(iter_filings_by_form_type("10-K", date(2024, 1, 15), date(2024, 1, 15)))
        load_content.assert_called_once_with("s3://custom-bucket/sec/manifest.parquet")

    def test_explicit_bucket_overrides_env(self, mocker, monkeypatch):
        monkeypatch.setenv("BUCKET_NAME", "env-bucket")
        load_content = mocker.patch("idi_ftm2j_shared.sec.load_content", return_value=b"")
        list(
            iter_filings_by_form_type(
                "10-K", date(2024, 1, 15), date(2024, 1, 15), bucket="explicit-bucket"
            )
        )
        load_content.assert_called_once_with("s3://explicit-bucket/sec/manifest.parquet")


# ---------------------------------------------------------------------------
# iter_filings — DiscoveredFiling list convention
# ---------------------------------------------------------------------------


class TestIterFilingsFromDiscovered:
    """Tests for iter_filings called with a list of DiscoveredFiling objects."""

    def test_yields_filing_for_each_discovered(self, mocker, bucket_env):
        discovered = [_make_discovered(cik="001"), _make_discovered(cik="002")]
        filing = _make_filing()
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            results = list(iter_filings_by_discovered(discovered))
        assert len(results) == 2
        assert mock_load.call_count == 2

    def test_constructs_key_from_discovered_fields(self, mocker, bucket_env):
        discovered = [
            _make_discovered(
                form_type="13F-HR",
                filing_date=date(2024, 3, 31),
                cik="999",
                accession_number="0000999999-24-000001",
            )
        ]
        filing = _make_filing()
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            list(iter_filings_by_discovered(discovered))
        assert (
            mock_load.call_args[0][1]
            == "sec/2024-03-31/13F-HR/999/000099999924000001/manifest.json"
        )

    def test_skips_none_from_load(self, bucket_env):
        discovered = [_make_discovered()]
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=None):
            results = list(iter_filings_by_discovered(discovered))
        assert results == []

    def test_excludes_failures_by_default(self, bucket_env):
        discovered = [_make_discovered(cik="001"), _make_discovered(cik="002")]
        success = _make_filing(cik="001", failure_reason="")
        failed = _make_filing(cik="002", failure_reason="timeout")
        with patch("idi_ftm2j_shared.sec._load_filing", side_effect=[success, failed]):
            results = list(iter_filings_by_discovered(discovered))
        assert len(results) == 1
        assert results[0].cik == "001"

    def test_includes_failures_when_flag_set(self, bucket_env):
        discovered = [_make_discovered(cik="001"), _make_discovered(cik="002")]
        success = _make_filing(cik="001", failure_reason="")
        failed = _make_filing(cik="002", failure_reason="timeout")
        with patch("idi_ftm2j_shared.sec._load_filing", side_effect=[success, failed]):
            results = list(iter_filings_by_discovered(discovered, include_failures=True))
        assert len(results) == 2

    def test_empty_list_yields_nothing(self, bucket_env):
        results = list(iter_filings_by_discovered([]))
        assert results == []

    def test_uses_bucket_arg(self, monkeypatch):
        monkeypatch.delenv("BUCKET_NAME", raising=False)
        discovered = [_make_discovered()]
        filing = _make_filing()
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            list(iter_filings_by_discovered(discovered, bucket="explicit-bucket"))
        assert mock_load.call_args[0][0] == "explicit-bucket"

    def test_reads_bucket_from_env(self, monkeypatch):
        monkeypatch.setenv("BUCKET_NAME", "env-bucket")
        discovered = [_make_discovered()]
        filing = _make_filing()
        with patch("idi_ftm2j_shared.sec._load_filing", return_value=filing) as mock_load:
            list(iter_filings_by_discovered(discovered))
        assert mock_load.call_args[0][0] == "env-bucket"
