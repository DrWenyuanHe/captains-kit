"""Tests for `msw.py refs` (mswlib.refs) and the polite HTTP client (mswlib.http).

No network: PubMed and Crossref are replaced by fake transports. The live checks at
the end run only with MSW_NETWORK_TESTS=1.
"""

import argparse
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "scientific-manuscript" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import msw_fixtures as fx  # noqa: E402
from mswlib import http, refs  # noqa: E402
from mswlib.docx import Package  # noqa: E402
from mswlib.endnote import census  # noqa: E402
from mswlib.views import project  # noqa: E402
from mswlib.wordml import iter_paragraphs, paragraph_text  # noqa: E402
from mswlib.xmltree import XMLDoc  # noqa: E402

DOCTYPE = ('<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January 2025//EN" '
           '"https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">')
LONG_ABSTRACT = "Synthetic sentence about marker Q-17 and GENE1. " * 110   # about 5,300 characters

ARTICLES = {
    "10000001": (
        '<PubmedArticle><MedlineCitation Status="MEDLINE" Owner="NLM"><PMID Version="1">10000001</PMID>'
        '<Article PubModel="Print-Electronic"><Journal><ISSN IssnType="Electronic">0000-0000</ISSN>'
        '<JournalIssue CitedMedium="Internet"><Volume>12</Volume><Issue>3</Issue><PubDate><Year>2020</Year>'
        '<Month>Mar</Month></PubDate></JournalIssue><Title>Journal of Fixture Studies</Title>'
        '<ISOAbbreviation>J Fixture Stud</ISOAbbreviation></Journal>'
        '<ArticleTitle>Marker Q-17 and <i>GENE1</i> in a synthetic cohort.</ArticleTitle>'
        '<Pagination><StartPage>e100</StartPage><MedlinePgn>e100</MedlinePgn></Pagination>'
        '<ELocationID EIdType="pii" ValidYN="Y">e100</ELocationID>'
        '<ELocationID EIdType="doi" ValidYN="Y">10.1000/fixture.1</ELocationID>'
        '<Abstract><AbstractText Label="BACKGROUND" NlmCategory="BACKGROUND">Synthetic background about '
        '<i>GENE1</i> expression.</AbstractText><AbstractText Label="RESULTS" NlmCategory="RESULTS">Marker Q-17 '
        'rose after training.</AbstractText></Abstract>'
        '<AuthorList CompleteYN="Y"><Author ValidYN="Y"><LastName>Alpha</LastName><ForeName>Anna B</ForeName>'
        '<Initials>AB</Initials></Author><Author ValidYN="Y"><CollectiveName>Fixture Study Group</CollectiveName>'
        '</Author></AuthorList><Language>eng</Language><PublicationTypeList><PublicationType UI="D016428">'
        'Journal Article</PublicationType></PublicationTypeList><ArticleDate DateType="Electronic"><Year>2019</Year>'
        '<Month>12</Month><Day>30</Day></ArticleDate></Article><MedlineJournalInfo><MedlineTA>J Fixture Stud'
        '</MedlineTA></MedlineJournalInfo></MedlineCitation><PubmedData><ArticleIdList>'
        '<ArticleId IdType="pubmed">10000001</ArticleId><ArticleId IdType="doi">10.1000/fixture.1</ArticleId>'
        '<ArticleId IdType="pmc">PMC0000001</ArticleId></ArticleIdList></PubmedData></PubmedArticle>'),
    "10000002": (
        '<PubmedArticle><MedlineCitation><PMID Version="1">10000002</PMID><Article><Journal><JournalIssue>'
        '<Volume>1</Volume><PubDate><MedlineDate>2021 Jan-Feb</MedlineDate></PubDate></JournalIssue>'
        '<Title>Fixture Letters</Title><ISOAbbreviation>Fixture Lett</ISOAbbreviation></Journal>'
        '<ArticleTitle>Second fixture paper.</ArticleTitle><Pagination><MedlinePgn>100-9</MedlinePgn></Pagination>'
        '<AuthorList><Author><LastName>Beta</LastName><ForeName>Bo</ForeName></Author></AuthorList></Article>'
        '</MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/fixture.2</ArticleId>'
        '</ArticleIdList></PubmedData></PubmedArticle>'),
    "10000003": (
        '<PubmedArticle><MedlineCitation><PMID Version="1">10000003</PMID><Article><Journal><JournalIssue>'
        '<Volume>2</Volume><PubDate><Year>2022</Year></PubDate></JournalIssue><Title>Fixture Reports</Title>'
        '</Journal><ArticleTitle>Third fixture paper.</ArticleTitle><Abstract><AbstractText>' + LONG_ABSTRACT
        + '</AbstractText></Abstract><AuthorList><Author><LastName>Gamma</LastName><Initials>C</Initials>'
        '</Author></AuthorList></Article></MedlineCitation></PubmedArticle>'),
}


def efetch_xml(pmids) -> bytes:
    body = "".join(ARTICLES[p] for p in pmids if p in ARTICLES)
    return (f'<?xml version="1.0" ?>\n{DOCTYPE}\n<PubmedArticleSet>{body}</PubmedArticleSet>').encode("utf-8")


class FakeTransport:
    """Records calls; answers efetch from ARTICLES and Crossref from a dict of works."""

    def __init__(self, responses=None, works=None):
        self.calls = []
        self.responses = list(responses or [])
        self.works = works or {}

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if self.responses:
            reply = self.responses.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            return reply
        if "efetch.fcgi" in url:
            ids = urllib.parse.parse_qs(body.decode())["id"][0].split(",")
            return 200, {"Content-Type": "text/xml"}, efetch_xml(ids)
        if url.startswith(refs.CROSSREF_WORKS):
            doi = urllib.parse.unquote(url[len(refs.CROSSREF_WORKS):])
            if doi in self.works:
                return 200, {"content-type": "application/json"}, json.dumps(
                    {"status": "ok", "message": self.works[doi]}).encode()
            return 404, {}, b"Resource not found."
        raise AssertionError(f"unexpected URL {url}")


class Clock:
    """A clock that advances 10 s per reading, so the rate limiter never waits."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 10.0
        return self.now


def client(transport, **options):
    options.setdefault("contact", "")
    options.setdefault("api_key", "")
    sleeps = options.pop("sleeps", [])
    return http.Client(transport=transport, sleep=sleeps.append, clock=Clock(), rng=lambda: 0.0, **options)


def run_cli(*argv):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    refs.register(sub)
    args = parser.parse_args([str(a) for a in argv])
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = args.func(args)
    return code, out.getvalue(), err.getvalue()


def clean_docx(extra="") -> bytes:
    """Fixture with every earlier revision accepted (no tracked changes left)."""
    return fx.replace_document(fx.make_docx(extra_paragraphs=extra), lambda data: project(XMLDoc(data), "accept"))


def replace_part(docx: bytes, name: str, data: bytes) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(docx))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for info in source.infolist():
            archive.writestr(info, data if info.filename == name else source.read(info))
    return buffer.getvalue()


def endnote_export(records) -> bytes:
    """Synthetic EndNote XML export; values are wrapped in <style> as EndNote writes them."""
    def styled(text):
        return f'<style face="normal" font="default" size="100%">{text}</style>'
    body = []
    for rec in records:
        title = rec["title"].split(" ", 1)
        title_xml = styled(title[0] + " ") + (f'<style face="italic" font="default" size="100%">{title[1]}</style>'
                                             if len(title) > 1 else "")
        parts = [f'<record><database name="Fixture.enl" path="Fixture.enl">Fixture.enl</database>'
                 f'<source-app name="EndNote" version="21.0">EndNote</source-app><rec-number>{rec["rec"]}</rec-number>'
                 f'<foreign-keys><key app="EN" db-id="{rec.get("db", fx.DB)}" timestamp="1700000000">{rec["rec"]}</key>'
                 f'</foreign-keys><ref-type name="{rec.get("type", "Journal Article")}">17</ref-type>'
                 f'<contributors><authors><author>{styled(rec["author"])}</author></authors></contributors>'
                 f'<titles><title>{title_xml}</title></titles><dates><year>{styled(rec["year"])}</year></dates>']
        if rec.get("accession"):
            parts.append(f"<accession-num>{styled(rec['accession'])}</accession-num>")
        if rec.get("label"):
            parts.append(f"<label>{styled(rec['label'])}</label>")
        if rec.get("doi"):
            parts.append(f"<electronic-resource-num>{styled(rec['doi'])}</electronic-resource-num>")
        parts.append("</record>")
        body.append("".join(parts))
    return ('<?xml version="1.0" encoding="UTF-8" ?><xml><records>' + "".join(body)
            + "</records></xml>").encode("utf-8")


LIBRARY = [
    {"rec": 1, "author": "Alpha, A.", "title": "First fixture paper", "year": "2020", "accession": "10000001",
     "doi": "10.1000/fixture.1"},
    {"rec": 2, "author": "Beta, B.", "title": "Second fixture paper", "year": "2021", "accession": "10000002"},
    {"rec": 3, "author": "Gamma, C.", "title": "Third fixture paper", "year": "2022", "accession": "10000003"},
    {"rec": 5, "author": "National Center for Example Statistics,", "title": "Example survey data", "year": "2019",
     "label": "survey2019", "type": "Dataset"},
]

PLACEHOLDER_PARAGRAPHS = (
    fx.para(fx.run("Levels rose [PMID: 10000001] and fell [PMIDs: 10000002, 10000003]."), "20000001")
    + fx.para(fx.run("Survey data were used [REF: survey2019]."), "20000002"))


# =====================================================================================
# HTTP client
# =====================================================================================

class HttpClientTests(unittest.TestCase):
    URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    def test_retries_429_honouring_retry_after(self):
        fake = FakeTransport([(429, {"Retry-After": "3"}, b"slow down"), (200, {}, b"<ok/>")])
        sleeps = []
        c = client(fake, sleeps=sleeps)
        response = c.get(self.URL, {"db": "pubmed", "id": "1"})
        self.assertEqual(response.body, b"<ok/>")
        self.assertEqual(response.attempts, 2)
        self.assertEqual(len(fake.calls), 2)
        self.assertIn(3.0, sleeps)

    def test_retry_after_http_date(self):
        self.assertEqual(http.retry_after_seconds("Thu, 01 Jan 2026 00:00:10 GMT", now=1767225600.0), 10.0)
        self.assertIsNone(http.retry_after_seconds("soon"))

    def test_5xx_and_timeouts_retry_then_fail_clearly(self):
        fake = FakeTransport([(503, {}, b""), (200, {}, b"fine")])
        sleeps = []
        self.assertEqual(client(fake, sleeps=sleeps).get(self.URL).body, b"fine")
        self.assertEqual(sleeps, [1.0])   # 2 ** 0 + jitter 0
        fake = FakeTransport([TimeoutError("timed out"), TimeoutError("timed out")])
        with self.assertRaises(http.FetchError) as caught:
            client(fake, max_retries=1).get(self.URL)
        self.assertEqual(caught.exception.attempts, 2)

    def test_non_retryable_status_raises_at_once(self):
        fake = FakeTransport([(404, {}, b"missing")])
        with self.assertRaises(http.FetchError) as caught:
            client(fake).get("https://api.crossref.org/works/10.1000/none")
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(len(fake.calls), 1)

    def test_cache_hit_is_content_addressed_and_write_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = client(FakeTransport([(200, {"Content-Type": "text/xml"}, b"<cached/>")]), cache_dir=tmp,
                           api_key="KEY-ONE")
            response = first.get(self.URL, {"db": "pubmed", "id": "7", **first.ncbi_params()})
            self.assertFalse(response.from_cache)
            files = list(Path(tmp).rglob("*.bin"))
            self.assertEqual(len(files), 1)
            before = files[0].read_bytes()
            offline = FakeTransport([AssertionError("network used despite cache")])
            second = client(offline, cache_dir=tmp, api_key="KEY-TWO", contact="someone@example.org")
            again = second.get(self.URL, {"db": "pubmed", "id": "7", **second.ncbi_params()})
            self.assertTrue(again.from_cache)
            self.assertEqual(again.body, b"<cached/>")
            self.assertEqual(offline.calls, [])
            second._cache_write(response)          # an existing entry is left as it is
            self.assertEqual(files[0].read_bytes(), before)
            self.assertNotIn(b"KEY-ONE", before)

    def test_corrupt_cache_entry_is_refetched_and_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = http.Client.cache_key("GET", self.URL, {"id": "9"}, None)
            path = Path(tmp) / key[:2] / f"{key}.bin"
            path.parent.mkdir(parents=True)
            path.write_bytes(b'{"schema": "msw-http-cache/1", "body_sha256": "00"}\npartial')
            fake = FakeTransport([(200, {}, b"fresh")])
            c = client(fake, cache_dir=tmp)
            self.assertEqual(c.get(self.URL, {"id": "9"}).body, b"fresh")
            self.assertTrue(c.warnings)
            self.assertTrue(path.read_bytes().endswith(b"partial"))

    def test_only_pubmed_and_crossref_over_https(self):
        fake = FakeTransport()
        for url in ("https://example.org/data", "http://api.crossref.org/works/10.1/x"):
            with self.assertRaises(http.HostNotAllowed):
                client(fake).get(url)
        self.assertEqual(fake.calls, [])

    def test_identity_rate_limits_and_redaction(self):
        with mock.patch.dict(os.environ, {"MSW_CONTACT_EMAIL": "someone@example.org", "NCBI_API_KEY": "SECRETKEY"}):
            c = http.Client(transport=FakeTransport([(200, {}, b"x")]), sleep=lambda s: None, clock=Clock())
        self.assertIn("mailto:someone@example.org", c.user_agent)
        self.assertEqual(c.ncbi_params(), {"tool": http.TOOL_NAME, "email": "someone@example.org",
                                           "api_key": "SECRETKEY"})
        self.assertAlmostEqual(c.min_interval(http.NCBI_HOST), 0.1)
        self.assertAlmostEqual(client(FakeTransport()).min_interval(http.NCBI_HOST), 0.34)
        record = c.get(self.URL, {"id": "1", **c.ncbi_params()}).record()
        text = json.dumps(record)
        self.assertNotIn("SECRETKEY", text)
        self.assertNotIn("someone@example.org", text)
        self.assertEqual(record["params"]["api_key"], "REDACTED")

    def test_rate_limit_spaces_requests_per_host(self):
        sleeps = []
        times = iter([0.0, 0.05, 0.4])
        c = http.Client(transport=FakeTransport([(200, {}, b"a"), (200, {}, b"b")]), sleep=sleeps.append,
                        clock=lambda: next(times), contact="", api_key="")
        c.get(self.URL)
        c.get(self.URL)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.29)

    def test_same_host_https_redirect_is_followed_with_identity(self):
        moved = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch2.fcgi?id=1"
        fake = FakeTransport([(302, {"Location": "/entrez/eutils/efetch2.fcgi?id=1"}, b""), (200, {}, b"<moved/>")])
        response = client(fake, contact="someone@example.org").get(self.URL, {"id": "1"})
        self.assertEqual(response.body, b"<moved/>")
        self.assertEqual(fake.calls[1]["url"], moved)
        self.assertIn("mailto:someone@example.org", fake.calls[1]["headers"]["User-Agent"])
        self.assertEqual(response.url, moved)      # the record names the URL the body came from

    def test_redirect_off_https_or_allowed_hosts_is_refused(self):
        for location in ("http://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", "https://example.org/x",
                         "ftp://api.crossref.org/works/10.1/x", "//example.org/x"):
            fake = FakeTransport([(301, {"Location": location}, b""), AssertionError("redirect followed")])
            with self.assertRaises(http.FetchError) as caught:
                client(fake, contact="someone@example.org").get(self.URL, {"id": "1"})
            self.assertEqual(caught.exception.status, 301, location)
            self.assertIn("refused", str(caught.exception))
            self.assertEqual(len(fake.calls), 1, location)
        fake = FakeTransport([(302, {}, b"")])
        with self.assertRaises(http.FetchError) as caught:
            client(fake).get(self.URL)
        self.assertIn("without a Location", str(caught.exception))
        loop = FakeTransport([(302, {"Location": self.URL}, b"")] * (http.MAX_REDIRECTS + 1))
        with self.assertRaises(http.FetchError) as caught:
            client(loop).get(self.URL)
        self.assertIn("redirects", str(caught.exception))
        self.assertEqual(len(loop.calls), http.MAX_REDIRECTS + 1)

    def test_redirect_to_another_allowed_host_drops_contact_and_key(self):
        other = ("https://api.crossref.org/works/10.1000/x?email=someone%40example.org&api_key=SECRETKEY"
                 "&tool=t&rows=1")
        fake = FakeTransport([(307, {"Location": other}, b""), (200, {}, b"{}")])
        c = client(fake, contact="someone@example.org", api_key="SECRETKEY")
        response = c.post(self.URL, {"id": "1", **c.ncbi_params()})
        first, second = fake.calls
        self.assertIn(b"SECRETKEY", first["body"])
        self.assertIn("mailto:someone@example.org", first["headers"]["User-Agent"])
        self.assertEqual(second["method"], "POST")
        self.assertEqual(second["headers"]["User-Agent"], http.USER_AGENT)
        for text in (second["url"], second["body"].decode("ascii"), json.dumps(response.record())):
            self.assertNotIn("someone", text)
            self.assertNotIn("SECRETKEY", text)
        self.assertIn("rows=1", second["url"])
        self.assertIn("id=1", second["body"].decode("ascii"))
        fake = FakeTransport([(303, {"Location": other}, b""), (200, {}, b"{}")])
        c = client(fake, contact="someone@example.org", api_key="SECRETKEY")
        c.post(self.URL, {"id": "1", **c.ncbi_params()})
        self.assertEqual((fake.calls[1]["method"], fake.calls[1]["body"]), ("GET", None))
        leak = FakeTransport([(302, {"Location": "https://api.crossref.org/works/someone@example.org"}, b"")])
        with self.assertRaises(http.FetchError) as caught:
            client(leak, contact="someone@example.org").get(self.URL)
        self.assertIn("refused", str(caught.exception))
        self.assertNotIn("someone", str(caught.exception))
        self.assertEqual(len(leak.calls), 1)

    def test_default_transport_returns_redirects_unfollowed(self):
        seen = []

        class Redirector(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("User-Agent")))
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "/elsewhere")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", "8")
                    self.end_headers()
                    self.wfile.write(b"followed")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch.dict(os.environ, {"NO_PROXY": "*"}):
                status, headers, body = http.urllib_transport(
                    "GET", f"http://127.0.0.1:{server.server_address[1]}/start",
                    {"User-Agent": "test; mailto:someone@example.org"}, None, 10)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)
        self.assertEqual((status, headers.get("location"), body), (302, "/elsewhere", b""))
        self.assertEqual([path for path, _ in seen], ["/start"])


# =====================================================================================
# PubMed and Crossref
# =====================================================================================

class PubMedTests(unittest.TestCase):
    def test_efetch_parsing(self):
        records = {r["pmid"]: r for r in refs.parse_pubmed_xml(efetch_xml(["10000001", "10000002", "10000003"]))}
        first = records["10000001"]
        self.assertEqual(first["title"], "Marker Q-17 and GENE1 in a synthetic cohort.")
        self.assertEqual(first["author_display"], ["Alpha AB", "Fixture Study Group"])
        self.assertEqual(first["authors"][1], {"collective": "Fixture Study Group", "display": "Fixture Study Group"})
        self.assertEqual((first["journal"], first["journal_iso"]), ("Journal of Fixture Studies", "J Fixture Stud"))
        self.assertEqual((first["year"], first["epub_date"], first["epub_year"]), ("2020", "2019-12-30", "2019"))
        self.assertEqual((first["volume"], first["issue"], first["pages"]), ("12", "3", "e100"))
        self.assertEqual(first["pii"], "e100")
        self.assertEqual(first["doi"], "10.1000/fixture.1")
        self.assertEqual([e["type"] for e in first["elocation"]], ["pii", "doi"])
        self.assertEqual(first["pmcid"], "PMC0000001")
        self.assertEqual(first["abstract"], "BACKGROUND: Synthetic background about GENE1 expression.\n"
                                            "RESULTS: Marker Q-17 rose after training.")
        second = records["10000002"]
        self.assertEqual((second["year"], second["doi"], second["abstract_available"]),
                         ("2021", "10.1000/fixture.2", False))
        self.assertEqual(second["author_display"], ["Beta B"])
        self.assertGreater(len(records["10000003"]["abstract"]), refs.MIN_ABSTRACT_CHARS)
        self.assertEqual(records["10000003"]["xpath"], "/PubmedArticleSet/PubmedArticle[3]")

    def test_fetch_batches_archive_evidence_and_refuse_rerun(self):
        fake = FakeTransport()
        with tempfile.TemporaryDirectory() as tmp:
            c = client(fake, contact="someone@example.org", api_key="SECRETKEY")
            result = refs.fetch_pubmed(["10000001", "10000002", "PMID:10000009"], tmp, c, batch_size=2)
            self.assertEqual(len(fake.calls), 2)
            sent = fake.calls[0]["body"].decode()
            for part in ("db=pubmed", "retmode=xml", "tool=captains-kit-msw", "email=someone%40example.org",
                         "api_key=SECRETKEY", "id=10000001%2C10000002"):
                self.assertIn(part, sent)
            self.assertEqual(result["status"], {"10000001": "ok", "10000002": "no_abstract",
                                                "10000009": "not_found"})
            evidence = Path(tmp) / "evidence"
            names = sorted(p.name for p in evidence.iterdir())
            self.assertEqual(names, ["pubmed_batch_01.request.json", "pubmed_batch_01.xml",
                                     "pubmed_batch_01.xml.sha256", "pubmed_batch_02.request.json",
                                     "pubmed_batch_02.xml", "pubmed_batch_02.xml.sha256"])
            saved = json.loads((Path(tmp) / "records.json").read_text(encoding="utf-8"))
            record = saved["records"]["10000001"]
            self.assertEqual(record["evidence"]["file"], "evidence/pubmed_batch_01.xml")
            self.assertEqual(record["evidence"]["xpath"], "/PubmedArticleSet/PubmedArticle[1]")
            self.assertEqual(record["evidence"]["sha256"],
                             (evidence / "pubmed_batch_01.xml.sha256").read_text().split()[0])
            for path in Path(tmp).rglob("*"):
                if path.is_file():
                    data = path.read_bytes()
                    self.assertNotIn(b"SECRETKEY", data, path.name)
                    self.assertNotIn(b"someone@example.org", data, path.name)
            with self.assertRaises(refs.RefsError):
                refs.fetch_pubmed(["10000001"], tmp, c)

    def test_fetch_error_is_not_reported_as_missing_abstract(self):
        fake = FakeTransport([(400, {}, b"bad request")])
        with tempfile.TemporaryDirectory() as tmp:
            result = refs.fetch_pubmed(["10000001"], tmp, client(fake))
            self.assertEqual(result["status"], {"10000001": "fetch_error"})
            self.assertTrue(result["errors"])

    def test_pubmed_cli_from_docx_uses_cache(self):
        docx_bytes = fx.make_docx(extra_paragraphs=PLACEHOLDER_PARAGRAPHS)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "draft.docx"
            source.write_bytes(docx_bytes)
            fake = FakeTransport()
            with mock.patch.object(refs, "TRANSPORT", fake), \
                    mock.patch.dict(os.environ, {"MSW_CONTACT_EMAIL": "", "NCBI_API_KEY": ""}):
                code, out, _ = run_cli("refs", "pubmed", "--from-docx", source, "--out", Path(tmp) / "run1",
                                       "--cache", Path(tmp) / "cache")
                self.assertEqual(code, 0, out)
                self.assertEqual(len(fake.calls), 1)
                code, out, _ = run_cli("refs", "pubmed", "10000001", "10000002", "10000003", "--out",
                                       Path(tmp) / "run2", "--cache", Path(tmp) / "cache")
            self.assertEqual(len(fake.calls), 1, "second run should come from the cache")
            data = json.loads((Path(tmp) / "run1" / "records.json").read_text(encoding="utf-8"))
            self.assertEqual(data["requested"], ["10000001", "10000002", "10000003"])

    def test_crossref_lookup(self):
        works = {"10.1000/fixture.1": {"DOI": "10.1000/FIXTURE.1", "title": ["First <i>fixture</i> paper"],
                                       "container-title": ["J Fixture"], "issued": {"date-parts": [[2020, 3]]},
                                       "published-online": {"date-parts": [[2019, 12, 30]]},
                                       "author": [{"family": "Alpha", "given": "Anna B."},
                                                  {"name": "Fixture Study Group"}]}}
        with tempfile.TemporaryDirectory() as tmp:
            result = refs.fetch_crossref(["https://doi.org/10.1000/FIXTURE.1", "10.1000/absent"], tmp,
                                         client(FakeTransport(works=works)))
            record = result["records"]["10.1000/fixture.1"]
            self.assertEqual((record["title"], record["year"], record["container"]),
                             ("First fixture paper", "2020", "J Fixture"))
            self.assertEqual(record["years"], ["2019", "2020"])
            self.assertEqual(record["author_display"], ["Alpha AB", "Fixture Study Group"])
            self.assertEqual(result["status"]["10.1000/absent"], "not_found")
            self.assertTrue((Path(tmp) / "evidence" / "crossref_001.json").is_file())


# =====================================================================================
# Placeholders and census
# =====================================================================================

class PlaceholderTests(unittest.TestCase):
    def test_inventory(self):
        extra = PLACEHOLDER_PARAGRAPHS + fx.para(
            fx.run("Split [PMID: ") + fx.run("10000001] and ") + fx.run("{Beta, 2021 #2;Gamma`; Jr, 2022 #3}")
            + fx.run(" then [PMID: 12a] and [PMID: 1234567890]."), "20000003")
        package = Package(fx.make_docx(extra_paragraphs=extra))
        doc = package.document
        for paragraph in iter_paragraphs(doc):
            self.assertEqual(paragraph_text(doc, paragraph),
                             "".join(t for _, t in refs.paragraph_segments(doc, paragraph)))
        data = refs.placeholder_inventory(package)
        summary = data["summary"]
        self.assertEqual(summary["by_kind"], {"pmid": 2, "pmids": 1, "ref": 1, "temporary": 1})
        self.assertEqual(data["pmids_in_order"], ["10000001", "10000002", "10000003"])
        self.assertEqual(summary["pmid_occurrences"], 4)
        self.assertEqual(summary["split_across_runs"], 1)
        self.assertEqual(summary["malformed"], 2)
        first = data["items"][0]
        self.assertEqual((first["paraId"], first["text"], first["context_before"]),
                         ("20000001", "[PMID: 10000001]", "Levels rose "))
        paragraphs = [paragraph_text(doc, p) for p in iter_paragraphs(doc)]
        self.assertIn("[PMID: 10000001]", paragraphs[first["paragraph"]])
        group = data["items"][1]
        self.assertEqual((group["kind"], group["pmids"]), ("pmids", ["10000002", "10000003"]))
        self.assertEqual(data["items"][2]["key"], "survey2019")
        temporary = next(i for i in data["items"] if i["kind"] == "temporary")
        self.assertEqual(temporary["cites"], [{"author": "Beta", "year": "2021", "recnum": "2"},
                                              {"author": "Gamma; Jr", "year": "2022", "recnum": "3"}])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "draft.docx"
            path.write_bytes(fx.make_docx(extra_paragraphs=extra))
            code, out, _ = run_cli("refs", "placeholders", path, "--json", Path(tmp) / "inventory.json")
            self.assertEqual(code, 1)     # malformed placeholders are reported as a failure
            saved = json.loads((Path(tmp) / "inventory.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["summary"]["placeholders"], 5)
            code, out, _ = run_cli("refs", "placeholders", path, "--json", Path(tmp) / "inventory.json")
            self.assertEqual(code, 2)     # never replaces an existing file


class CensusTests(unittest.TestCase):
    def test_fixture_payload_audit_is_clean(self):
        data = refs.census_with_audit(Package(fx.make_docx(multi_chunk=True)))
        self.assertEqual(data["citation_fields_live"], 3)
        self.assertEqual(data["payload_forms"], {"inline": 1, "single_chunk": 1, "multi_chunk": 1, "none": 0})
        audit = data["payload_audit"]
        self.assertEqual((audit["fields"], audit["cite_occurrences"], audit["records"]), (3, 4, 3))
        self.assertEqual((audit["db_id_count"], audit["journal_articles"]), (1, 3))
        for key in ("missing_pages_or_elocator", "missing_doi", "missing_pmid", "corporate_author_suspects",
                    "author_year_collisions", "recnum_collisions", "payload_errors", "warnings"):
            self.assertEqual(audit[key], [], key)

    def test_payload_audit_findings(self):
        other_db = "otherdb000000000000000000000000000000"
        extra = (fx.para(fx.run("More ") + fx.cite_inline(4, fx.record(4, "Alpha", 2020, "Another alpha paper", "",
                                                                         pages="", doi="")), "20000010")
                 + fx.para(fx.run("Group ") + fx.cite_inline(5, fx.record(5, "Example Study Group", 2023,
                                                                          "Group paper", "10000005")), "20000011")
                 + fx.para(fx.run("Other ") + fx.cite_inline(6, fx.record(1, "Delta", 2019, "Other library paper",
                                                                          "10000006", doi="10.1000/other.{rec}")
                                                                .replace(fx.DB, other_db)),
                           "20000012"))
        audit = refs.payload_audit(Package(fx.make_docx(multi_chunk=True, extra_paragraphs=extra)).document)
        key4 = f"{fx.DB}#4"
        self.assertEqual([r["key"] for r in audit["missing_pages_or_elocator"]], [key4])
        self.assertEqual([r["key"] for r in audit["missing_doi"]], [key4])
        self.assertEqual([r["key"] for r in audit["missing_pmid"]], [key4])
        self.assertEqual([r["author"] for r in audit["corporate_author_suspects"]], ["Example Study Group, A."])
        self.assertEqual(len(audit["author_year_collisions"]), 1)
        self.assertEqual({r["recnum"] for r in audit["author_year_collisions"][0]["records"]}, {"1", "4"})
        self.assertEqual(audit["recnum_collisions"], [{"recnum": "1", "db_ids": sorted([fx.DB, other_db])}])
        self.assertEqual(audit["db_id_count"], 2)
        self.assertEqual(len(audit["warnings"]), 7)

    def test_census_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "draft.docx"
            path.write_bytes(fx.make_docx(multi_chunk=True))
            code, out, _ = run_cli("refs", "census", path)
            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertEqual(data["payload_audit"]["records"], 3)
            self.assertEqual(data["pmids"], 3)


# =====================================================================================
# Bibliography audit
# =====================================================================================

class BibAuditTests(unittest.TestCase):
    def test_entry_year_is_style_aware(self):
        self.assertEqual(refs.entry_year("Doe J. Title in 2019: a review. J X. 2021;5:1-9."), ("2021", "vancouver"))
        self.assertEqual(refs.entry_year("Doe J. Title. J X. 2021 Jan 5;5:1-9."), ("2021", "vancouver"))
        self.assertEqual(refs.entry_year("Doe, J. (2018). Title. J X, 5, 1-9.", style="APA 7th"),
                         ("2018", "author_date"))
        self.assertEqual(refs.entry_year("Doe J. Title. doi:10.2020/1999.12", "10.2020/1999.12"), ("", "none"))

    def test_numbering_duplicates_and_comparison(self):
        def add_dois(data):
            data = data.replace(b"2020;1:100-9.</w:t>", b"2020;1:100-9. doi:10.1000/fixture.1</w:t>")
            data = data.replace(b"2021;1:100-9.</w:t>", b"2021;1:100-9. doi:10.1000/fixture.2</w:t>")
            return data.replace(b"2022;1:100-9.</w:t>", b"2022;1:100-9. doi:10.1000/fixture.1</w:t>")
        records = {"schema": "msw-refs-crossref/1", "records": {
            "10.1000/fixture.1": {"doi": "10.1000/fixture.1", "title": "First fixture paper", "year": "2020",
                                  "years": ["2020"]},
            "10.1000/fixture.2": {"doi": "10.1000/fixture.2", "title": "Unrelated cardiac imaging protocol",
                                  "year": "2015", "years": ["2015"]}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "draft.docx"
            path.write_bytes(fx.replace_document(fx.make_docx(), add_dois))
            record_file = Path(tmp) / "crossref.json"
            record_file.write_text(json.dumps(records), encoding="utf-8")
            code, out, _ = run_cli("refs", "bib-audit", path, "--out", Path(tmp) / "audit", "--records", record_file)
            self.assertEqual(code, 1)
            audit = json.loads((Path(tmp) / "audit" / "bib_audit.json").read_text(encoding="utf-8"))
            self.assertTrue(audit["numbering"]["contiguous"])
            self.assertEqual(audit["duplicate_dois"], {"10.1000/fixture.1": [1, 3]})
            rows = {r["number"]: r for r in audit["rows"]}
            self.assertEqual(rows[1]["issues"], ["DUPLICATE_DOI"])
            self.assertEqual(rows[2]["issues"], ["TITLE_MISMATCH", "YEAR_MISMATCH"])
            self.assertEqual(rows[3]["issues"], ["DUPLICATE_DOI", "YEAR_MISMATCH"])
            report = (Path(tmp) / "audit" / "BIB_AUDIT.md").read_text(encoding="utf-8")
            self.assertIn("TITLE_MISMATCH", report)
            self.assertIn("10.1000/fixture.1: 1, 3", report)

    @staticmethod
    def docx_with_three_dois() -> bytes:
        def add_dois(data):
            for year, n in ((b"2020", b"1"), (b"2021", b"2"), (b"2022", b"3")):
                data = data.replace(year + b";1:100-9.</w:t>", year + b";1:100-9. doi:10.1000/fixture." + n + b"</w:t>")
            return data
        return fx.replace_document(fx.make_docx(), add_dois)

    def run_crossref_audit(self, tmp, fake):
        path = Path(tmp) / "draft.docx"
        path.write_bytes(self.docx_with_three_dois())
        with mock.patch.object(refs, "TRANSPORT", fake), \
                mock.patch.dict(os.environ, {"MSW_CONTACT_EMAIL": "", "NCBI_API_KEY": ""}):
            code, out, err = run_cli("refs", "bib-audit", path, "--out", Path(tmp) / "audit", "--crossref",
                                     "--no-cache", "--max-retries", "0")
        audit = json.loads((Path(tmp) / "audit" / "bib_audit.json").read_text(encoding="utf-8"))
        return code, out, err, {r["number"]: r for r in audit["rows"]}, audit

    def test_crossref_lookups_that_all_fail_exit_2_not_no_record(self):
        fake = FakeTransport([OSError("network is unreachable")] * 3)
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err, rows, audit = self.run_crossref_audit(tmp, fake)
            report = (Path(tmp) / "audit" / "BIB_AUDIT.md").read_text(encoding="utf-8")
        self.assertEqual(code, 2, out + err)
        self.assertEqual(len(fake.calls), 3)
        self.assertIn("every Crossref lookup failed (3 of 3)", err)
        self.assertIn("network is unreachable", err)
        self.assertEqual([rows[n]["issues"] for n in (1, 2, 3)], [["FETCH_ERROR"]] * 3)
        self.assertEqual(audit["counts"], {"FETCH_ERROR": 3})
        self.assertEqual(len(audit["fetch_errors"]), 3)
        self.assertNotIn("NO_RECORD", report.split("FETCH_ERROR means")[0])
        self.assertIn("Crossref lookups that failed: 3", report)

    def test_some_crossref_lookups_fail_exit_1_with_fetch_error_rows(self):
        works = {"10.1000/fixture.2": {"DOI": "10.1000/fixture.2", "title": ["Second fixture paper"],
                                       "issued": {"date-parts": [[2021]]}}}
        fake = FakeTransport([(503, {}, b"busy")], works=works)
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err, rows, _ = self.run_crossref_audit(tmp, fake)
        self.assertEqual(code, 1, out + err)
        self.assertEqual((rows[1]["issues"], rows[2]["issues"], rows[3]["issues"]),
                         (["FETCH_ERROR"], [], ["NO_RECORD"]))
        self.assertIn("HTTP 503", rows[1]["fetch_error"])
        self.assertIn("Crossref lookup failed for 10.1000/fixture.1", err)
        self.assertNotIn("every Crossref lookup failed", err)

    def test_missing_dois_and_gaps(self):
        audit = refs.bibliography_audit(Package(fx.replace_document(
            fx.make_docx(), lambda d: d.replace(b"<w:t>2.</w:t>", b"<w:t>4.</w:t>"))))
        self.assertFalse(audit["numbering"]["contiguous"])
        self.assertEqual(audit["numbering"]["gaps"], [2])
        self.assertEqual(audit["no_doi"], [1, 4, 3])
        self.assertFalse(audit["numbering"]["in_order"])


# =====================================================================================
# Claims
# =====================================================================================

def claims_docx() -> bytes:
    c1 = fx.record(1, "Alpha", 2020, "First fixture paper", "10000001")
    extra = (fx.para(fx.run("Discussion"), "20000020", "Heading1")
             + fx.para(fx.run("A rise was seen in cohort Q-17 (e.g., 1.5-fold; Fig. 2).") + fx.cite_inline(1, c1)
                       + fx.run(" Smith et al. found no change."), "20000021"))
    return fx.make_docx(multi_chunk=True, extra_paragraphs=extra)


def write_records(folder: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        refs.fetch_pubmed(["10000001", "10000002", "10000003"], tmp, client(FakeTransport()))
        data = (Path(tmp) / "records.json").read_bytes()
    target = folder / "records.json"
    target.write_bytes(data)
    return target


class ClaimsTests(unittest.TestCase):
    def test_sentence_segmentation(self):
        text = "Doe et al. showed a rise (e.g., 1.5-fold; Fig. 2), i.e. a gain. The next one. P < 0.05 was used."
        self.assertEqual([text[a:b] for a, b in refs.sentence_spans(text)],
                         ["Doe et al. showed a rise (e.g., 1.5-fold; Fig. 2), i.e. a gain.", "The next one.",
                          "P < 0.05 was used."])
        text = "Data from J. Smith were used. Values were 3.5 vs. 2.1 in total."
        self.assertEqual(len(refs.sentence_spans(text)), 2)

    def test_batches_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = write_records(tmp)
            path = tmp / "draft.docx"
            path.write_bytes(claims_docx())
            code, out, _ = run_cli("refs", "claims", path, "--out", tmp / "claims", "--records", records,
                                   "--batch-size", "2")
            self.assertEqual(code, 0, out)
            folder = tmp / "claims"
            self.assertEqual(sorted(p.name for p in (folder / "batches").iterdir()), ["batch_01.json", "batch_02.json"])
            self.assertTrue((folder / "findings").is_dir())
            schema = json.loads((folder / "VERDICT_SCHEMA.json").read_text(encoding="utf-8"))
            finding = schema["$defs"]["finding"]
            self.assertEqual(finding["properties"]["verdict"]["enum"],
                             ["SUPPORTED", "WEAK", "MISMATCH", "UNVERIFIED_NO_ABSTRACT"])
            self.assertEqual(finding["properties"]["suggested_action"]["enum"], ["REUSE", "ADD", "TEXT", "NONE"])
            self.assertEqual(finding["required"], ["field_index", "cited", "verdict", "reason", "suggested_action"])
            first = json.loads((folder / "batches" / "batch_01.json").read_text(encoding="utf-8"))
            second = json.loads((folder / "batches" / "batch_02.json").read_text(encoding="utf-8"))
            self.assertEqual(first["findings_file"], "findings/batch_01.json")
            items = first["items"] + second["items"]
            self.assertEqual([i["field_index"] for i in items], [0, 1, 2, 3])
            self.assertEqual(items[0]["claim"],
                             "Marker levels were compared between groups GENE1 expression rose after training and "
                             "fell later.")
            self.assertEqual(items[0]["display"], "(1)")
            self.assertIn("training (1) and", items[0]["claim_marked"])
            self.assertEqual(items[0]["section"], "Abstract")
            cited = items[0]["cited"][0]
            self.assertEqual((cited["key"], cited["pmid"], cited["abstract_source"]),
                             (f"{fx.DB}#1", "10000001", "pubmed"))
            self.assertIn("RESULTS: Marker Q-17 rose after training.", cited["abstract"])
            self.assertFalse(items[1]["cited"][0]["abstract_available"])   # 10000002 has no abstract
            multi = items[2]
            self.assertEqual([c["key"] for c in multi["cited"]], [f"{fx.DB}#3", f"{fx.DB}#1"])
            self.assertEqual(multi["cited"][0]["abstract"], LONG_ABSTRACT.strip())
            self.assertGreater(len(multi["cited"][0]["abstract"]), refs.MIN_ABSTRACT_CHARS)
            late = items[3]   # citation placed after the full stop belongs to the sentence before it
            self.assertEqual(late["claim"], "A rise was seen in cohort Q-17 (e.g., 1.5-fold; Fig. 2).")
            self.assertEqual(late["context_after"], "Smith et al. found no change.")
            self.assertEqual(late["section"], "Discussion")
            manifest = json.loads((folder / "claims_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual((manifest["fields"], manifest["cite_occurrences"], manifest["distinct_records"]),
                             (4, 5, 3))
            self.assertEqual(manifest["occurrences_without_abstract"], 1)
            code, _, err = run_cli("refs", "claims", path, "--out", folder)
            self.assertEqual(code, 2, "an existing batch folder is never reused")
            with self.assertRaises(refs.RefsError):
                refs.claim_items(Package(path), max_abstract_chars=1000)

    def test_claims_use_the_accepted_text(self):
        items = refs.claim_items(Package(fx.make_docx()))
        texts = " ".join(i["claim"] for i in items)
        self.assertNotIn("deleted words", texts)
        self.assertEqual(len(items), 3)

    def test_claims_report_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            path = tmp / "draft.docx"
            path.write_bytes(claims_docx())
            folder = tmp / "claims"
            refs.write_claim_batches(Package(path), folder, index=refs.RecordIndex.load([write_records(tmp)]),
                                     batch_size=2)
            k1, k2, k3 = (f"{fx.DB}#{n}" for n in (1, 2, 3))

            def save(name, batch, findings):
                (folder / "findings" / name).write_text(json.dumps({"batch": batch, "findings": findings}),
                                                        encoding="utf-8")
            save("batch_01.json", 1, [
                {"field_index": 0, "cited": [k1], "verdict": "MISMATCH", "reason": "Off-topic synthetic paper.",
                 "suggested_action": "REUSE", "confidence": "high", "replacement": "3"},
                {"field_index": 1, "cited": [k2], "verdict": "MISMATCH", "reason": "Probably another cohort.",
                 "suggested_action": "ADD", "confidence": "medium"}])
            save("batch_02.json", 2, [
                {"field_index": 2, "cited": [k3], "verdict": "PERHAPS", "reason": "x", "suggested_action": "NONE"},
                {"field_index": 3, "cited": [k1], "verdict": "WEAK", "reason": "Direction only.",
                 "suggested_action": "TEXT"},
                {"field_index": 3, "cited": [f"{fx.DB}#99"], "verdict": "SUPPORTED", "reason": "Unknown key.",
                 "suggested_action": "NONE"}])
            save("batch_02_fix.json", 2, [
                {"field_index": 2, "cited": [k3, k1], "verdict": "MISMATCH", "reason": "Paper says the opposite.",
                 "suggested_action": "TEXT"}])
            code, out, _ = run_cli("refs", "claims-report", folder)
            self.assertEqual(code, 1)     # invalid findings are reported
            report = (folder / "CLAIM_AUDIT.md").read_text(encoding="utf-8")
            for heading in ("## Tier 1: clearly wrong reference (1)", "## Tier 2: probably wrong reference (1)",
                            "## Tier 3: the sentence misstates the paper (1)", "## WEAK: partial support (1)",
                            "## Unverified: no abstract (0)", "## Invalid findings (2)"):
                self.assertIn(heading, report)
            self.assertIn("| MISMATCH | 3 |", report)
            self.assertIn("| WEAK | 1 |", report)
            self.assertIn("judged: 4; not judged: 0", report)
            self.assertIn("REUSE -> 3", report)
            self.assertIn("verdict must be one of", report)
            self.assertIn("not in field 3", report)
            self.assertIn("Alpha 2020 (PMID 10000001", report)
            tier1 = report.index("## Tier 1")
            self.assertIn("Marker levels were compared", report[tier1:report.index("## Tier 2")])
            code, _, _ = run_cli("refs", "claims-report", folder)
            self.assertEqual(code, 2, "an existing report is never replaced")
            result = refs.claims_report(folder, folder / "CLAIM_AUDIT_2.md")
            self.assertEqual(result["groups"]["tier3"], 1)


# =====================================================================================
# Library map and temporary citations
# =====================================================================================

class LibraryMapTests(unittest.TestCase):
    def test_export_with_style_wrapped_values(self):
        library = LIBRARY + [
            {"rec": 4, "author": "Beta, B.", "title": "Second fixture paper", "year": "2021", "accession": "10000002"},
            {"rec": 6, "author": "Epsilon, E.", "title": "Indexed elsewhere", "year": "2018", "accession": "WOS:0001"}]
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "library.xml"
            export.write_bytes(endnote_export(library))
            code, out, _ = run_cli("refs", "library-map", export, "--out", Path(tmp) / "map.json")
            self.assertEqual(code, 1)     # duplicates are a failure
            data = json.loads((Path(tmp) / "map.json").read_text(encoding="utf-8"))
        first = data["map"]["10000001"]
        self.assertEqual((first["rec_number"], first["db_id"], first["title"]), ("1", fx.DB, "First fixture paper"))
        self.assertEqual((first["surname"], first["year"], first["doi"]), ("Alpha", "2020", "10.1000/fixture.1"))
        self.assertEqual(data["duplicates"], {"10000002": ["2", "4"]})
        self.assertEqual(data["map"]["10000002"]["duplicate_rec_numbers"], ["2", "4"])
        self.assertEqual(data["refs"]["survey2019"]["surname"], "National Center for Example Statistics")
        self.assertEqual({r["rec_number"] for r in data["without_pmid"]}, {"5", "6"})
        self.assertEqual(data["non_pmid_accessions"][0]["accession"], "WOS:0001")
        self.assertEqual(data["records"], 6)


class TempCitationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.map_path = self.folder / "map.json"
        self.map_path.write_text(json.dumps(refs.library_map(endnote_export(LIBRARY), source="library.xml")),
                                 encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, data: bytes, name="clean.docx") -> Path:
        path = self.folder / name
        path.write_bytes(data)
        return path

    def test_replaces_placeholders_in_a_clean_copy(self):
        source = self.source(clean_docx(PLACEHOLDER_PARAGRAPHS))
        self.assertEqual(refs.tracked_changes(Package(source)), {})
        out = self.folder / "cwyw.docx"
        code, stdout, _ = run_cli("refs", "temp-citations", source, "--map", self.map_path, "--out", out)
        self.assertEqual(code, 0, stdout)
        package = Package(out)
        texts = [paragraph_text(package.document, p) for p in iter_paragraphs(package.document)]
        joined = "\n".join(texts)
        self.assertIn("Levels rose {Alpha, 2020 #1} and fell {Beta, 2021 #2;Gamma, 2022 #3}.", texts)
        self.assertIn("{National Center for Example Statistics, 2019 #5}", joined)
        self.assertNotIn("[PMID", joined)
        self.assertNotIn("[REF", joined)
        before, after = census(Package(source).document), census(package.document)
        for key in ("citation_fields_live", "cite_occurrences", "reflist", "field_errors"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(Package(source).parts["word/media/image1.png"], package.parts["word/media/image1.png"])
        ledger = json.loads((self.folder / "cwyw.temp-citations.json").read_text(encoding="utf-8"))
        self.assertEqual((ledger["groups_replaced"], ledger["citations"], ledger["distinct_sources"]), (3, 4, 4))
        self.assertEqual(ledger["rows"][1]["new"], "{Beta, 2021 #2;Gamma, 2022 #3}")
        self.assertEqual(ledger["output"]["sha256"], package.sha256)
        code, _, err = run_cli("refs", "temp-citations", source, "--map", self.map_path, "--out", out)
        self.assertEqual(code, 2)
        self.assertIn("refusing to replace", err)

    def test_refuses_tracked_documents(self):
        source = self.source(fx.make_docx(extra_paragraphs=PLACEHOLDER_PARAGRAPHS), "tracked.docx")
        code, _, err = run_cli("refs", "temp-citations", source, "--map", self.map_path, "--out",
                               self.folder / "never.docx")
        self.assertEqual(code, 2)
        self.assertIn("tracked changes", err)
        self.assertFalse((self.folder / "never.docx").exists())

    def test_refuses_split_and_unmapped_placeholders(self):
        split = fx.para(fx.run("Split [PMID: ") + fx.run("10000001] here."), "20000030")
        with self.assertRaises(refs.RefsError) as caught:
            refs.temporary_citations(Package(clean_docx(split)), json.loads(self.map_path.read_text("utf-8")))
        self.assertIn("split across runs", str(caught.exception))
        unmapped = fx.para(fx.run("Unknown [PMID: 10000099] and known [PMID: 10000001]."), "20000031")
        map_data = json.loads(self.map_path.read_text("utf-8"))
        with self.assertRaises(refs.RefsError) as caught:
            refs.temporary_citations(Package(clean_docx(unmapped)), map_data)
        self.assertIn("PMID 10000099 is not in the library map", str(caught.exception))
        _, ledger = refs.temporary_citations(Package(clean_docx(unmapped)), map_data, skip_unmapped=True)
        self.assertEqual((ledger["groups_replaced"], len(ledger["skipped"])), (1, 1))

    def test_layout_delimiters_and_escaping(self):
        layout = ("&lt;ENLayout&gt;&lt;Style&gt;Vancouver&lt;/Style&gt;&lt;LeftDelim&gt;{{&lt;/LeftDelim&gt;"
                  "&lt;RightDelim&gt;}}&lt;/RightDelim&gt;&lt;/ENLayout&gt;")
        settings = fx.SETTINGS.replace("</w:settings>", f'<w:docVars><w:docVar w:name="EN.Layout" w:val="{layout}"/>'
                                                        "</w:docVars></w:settings>")
        docx = replace_part(clean_docx(PLACEHOLDER_PARAGRAPHS), "word/settings.xml", settings.encode("utf-8"))
        package = Package(docx)
        self.assertEqual(refs.layout_delimiters(package), ("{{", "}}"))
        map_data = json.loads(self.map_path.read_text("utf-8"))
        map_data["map"]["10000001"]["surname"] = "Alpha; Omega\\X"
        new_document, ledger = refs.temporary_citations(package, map_data)
        self.assertEqual(ledger["rows"][0]["new"], "{{Alpha`; Omega`\\X, 2020 #1}}")
        doc = XMLDoc(new_document)
        texts = [paragraph_text(doc, p) for p in iter_paragraphs(doc)]
        self.assertIn("Levels rose {{Alpha`; Omega`\\X, 2020 #1}} and fell {{Beta, 2021 #2;Gamma, 2022 #3}}.", texts)
        inventory = refs.placeholder_inventory(Package(fx.replace_document(docx, lambda _: new_document)))
        self.assertEqual(inventory["items"][0]["cites"][0]["author"], "Alpha; Omega\\X")

    def test_refuses_duplicate_library_records(self):
        library = LIBRARY + [{"rec": 4, "author": "Beta, B.", "title": "Second fixture paper", "year": "2021",
                              "accession": "10000002"}]
        map_data = refs.library_map(endnote_export(library))
        with self.assertRaises(refs.RefsError) as caught:
            refs.temporary_citations(Package(clean_docx(PLACEHOLDER_PARAGRAPHS)), map_data)
        self.assertIn("several library records", str(caught.exception))


# =====================================================================================
# Live services (opt-in)
# =====================================================================================

@unittest.skipUnless(os.environ.get("MSW_NETWORK_TESTS") == "1", "network tests run only with MSW_NETWORK_TESTS=1")
class NetworkTests(unittest.TestCase):
    def test_pubmed_and_crossref_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = http.Client(cache_dir=Path(tmp) / "cache")
            result = refs.fetch_pubmed(["20332509"], Path(tmp) / "pubmed", live)
            self.assertEqual(result["status"]["20332509"], "ok")
            record = result["records"]["20332509"]
            self.assertTrue(record["title"])
            self.assertTrue(record["author_display"])
            crossref = refs.fetch_crossref(["10.1136/bmj.c332"], Path(tmp) / "crossref", live)
            self.assertEqual(crossref["status"]["10.1136/bmj.c332"], "ok")
            self.assertTrue(crossref["records"]["10.1136/bmj.c332"]["title"])


if __name__ == "__main__":
    unittest.main()
