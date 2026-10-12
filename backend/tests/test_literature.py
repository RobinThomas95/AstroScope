import pytest
from fastapi.testclient import TestClient

from app import main
from app.routers import literature as lit_router
from app.services.ads import (
    LiteratureService,
    bibliometrics,
    build_query,
    h_index,
    to_paper,
)
from app.services.facilities import detect_facilities
from app.services.gemini import GeminiError
from app.services.synthesis import SynthesisService, build_user_prompt, select_abstracts

DOCS = [
    {
        "bibcode": "2019ApJ...876...12A",
        "title": ["MUSE and ALMA view of NGC 1365"],
        "author": ["Alpha, A.", "Beta, B."],
        "year": "2019",
        "pubdate": "2019-05-00",
        "pub": "ApJ",
        "doctype": "article",
        "property": ["REFEREED", "ARTICLE"],
        "citation_count": 120,
        "keyword": ["galaxies: Seyfert", "galaxies: ISM"],
        "abstract": "We present VLT/MUSE and ALMA CO(2-1) observations of the barred Seyfert.",
    },
    {
        "bibcode": "2024MNRAS.530.1000B",
        "title": ["JWST/MIRI imaging of the nucleus"],
        "author": ["Beta, B."],
        "year": "2024",
        "pubdate": "2024-02-00",
        "pub": "MNRAS",
        "doctype": "article",
        "property": ["REFEREED"],
        "citation_count": 8,
        "keyword": ["galaxies: nuclei"],
        "abstract": "JWST MIRI and NIRCam images reveal ... HST too.",
    },
    {
        "bibcode": "2001AAS...199.0101C",
        "title": ["A poster"],
        "author": ["Gamma, C."],
        "year": "2001",
        "pubdate": "2001-12-00",
        "pub": "AAS",
        "doctype": "abstract",
        "property": ["NONARTICLE"],
        "citation_count": 0,
        "keyword": [],
        "abstract": None,
    },
    {
        "bibcode": "2015A&A...580A..10D",
        "title": ["HI in Fornax with ATCA and MeerKAT"],
        "author": ["Delta, D."],
        "year": "2015",
        "pubdate": "2015-08-00",
        "pub": "A&A",
        "doctype": "article",
        "property": ["REFEREED"],
        "citation_count": 35,
        "keyword": ["radio lines: galaxies"],
        "facility": ["ATCA"],
        "abstract": "MeerKAT and ATCA HI 21 cm observations of the Fornax cluster.",
    },
]


def test_detect_facilities():
    assert detect_facilities("VLT/MUSE and ALMA data", None) == ["MUSE", "VLT", "ALMA"]
    assert "JWST" in detect_facilities("NIRCam imaging")
    assert detect_facilities("nothing here") == []
    assert detect_facilities(None) == []


def test_build_query():
    q = build_query(["NGC 1365", "FCC 121", "NGC 1365"])
    assert 'object:"NGC 1365"' in q and 'object:"FCC 121"' in q
    assert 'abs:"NGC1365"' in q and 'abs:"FCC121"' in q  # compact forms
    assert q.count('object:"NGC 1365"') == 1 and "collection:astronomy" in q
    assert "property:refereed" in build_query(["M 87"], refereed_only=True)
    with pytest.raises(ValueError):
        build_query(["  "])


def test_to_paper_and_metrics():
    papers = [to_paper(d) for d in DOCS]
    p = papers[0]
    assert p.first_author == "Alpha, A." and p.refereed and p.year == 2019
    assert p.facilities == ["MUSE", "VLT", "ALMA"]
    assert papers[3].facilities[0] == "ATCA" and "MeerKAT" in papers[3].facilities
    assert p.ads_url.endswith("/2019ApJ...876...12A/abstract")
    m = bibliometrics(papers)
    assert m.total == 4 and m.refereed == 3
    assert [y.year for y in m.years] == [2001, 2015, 2019, 2024]
    assert m.years[-1].refereed == 1
    assert m.facilities[0].name in ("MUSE", "VLT", "ALMA", "ATCA", "MeerKAT", "JWST", "HST")
    assert m.total_citations == 163 and m.h_index == 3
    assert m.top_cited[0] == "2019ApJ...876...12A" and m.most_recent[0] == "2024MNRAS.530.1000B"
    assert sum(b.count for b in m.citation_bins) == 4
    assert h_index([]) == 0 and h_index([10, 10, 10]) == 3


class FakeAds:
    calls: list[tuple[str, int]] = []

    def __init__(self, token):
        self.token = token

    def search(self, query, max_rows=2000, fields=""):
        FakeAds.calls.append((query, max_rows))
        if self.token == "bad":
            raise PermissionError("ADS rejected the token (401 Unauthorized)")
        if self.token == "no-object" and "object:" in query:
            from app.services.ads import AdsQueryError

            raise AdsQueryError("ADS rejected the query: object query failed")
        if self.token == "no-facility" and ("object:" in query or "facility" in fields):
            from app.services.ads import AdsQueryError

            raise AdsQueryError("ADS rejected the query: unknown field facility")
        return [dict(d) for d in DOCS], 4


class FakeGemini:
    last_prompt: str = ""

    def __init__(self, api_key, model="gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model

    def generate_json(self, system, user, schema=None, **kw):
        FakeGemini.last_prompt = user
        if self.api_key == "bad":
            raise GeminiError("Gemini rejected the API key (403): invalid")
        return (
            {
                "summary": "NGC 1365 is a barred Seyfert galaxy in Fornax.",
                "consensus": ["Hosts a Seyfert nucleus [2019ApJ...876...12A]"],
                "open_questions": ["Origin of the nuclear outflow"],
                "key_papers": [
                    {"bibcode": "[2019ApJ...876...12A]", "reason": "MUSE+ALMA"},
                    {"bibcode": "1999XXX", "reason": "not supplied"},
                ],
                "keyword_clusters": [
                    {"theme": "AGN", "keywords": ["Seyfert"], "bibcodes": ["2019ApJ...876...12A"]}
                ],
                "facilities": [{"name": "MUSE", "role": "ionised gas kinematics"}],
                "publication_trend": "Rising since 2015.",
            },
            {"promptTokenCount": 1234, "candidatesTokenCount": 321},
        )


def test_literature_service_caches():
    FakeAds.calls.clear()
    svc = LiteratureService(client_factory=FakeAds)
    r = svc.search("NGC 1365", ["NGC 1365", "FCC 121"], "tok", "header")
    assert r.num_found == 4 and len(r.papers) == 4 and not r.truncated
    assert r.metrics.h_index == 3
    again = svc.search("NGC 1365", ["FCC 121", "NGC 1365"], "tok", "header")
    assert again.cached and len(FakeAds.calls) == 1


def test_literature_falls_back_when_ads_rejects_the_query():
    FakeAds.calls.clear()
    svc = LiteratureService(client_factory=FakeAds)
    r = svc.search("NGC 1365", ["NGC 1365"], "no-object", "header")
    assert r.metrics.total == 4 and "object:" not in r.ads_query and 'abs:"NGC 1365"' in r.ads_query
    assert len(FakeAds.calls) == 2
    FakeAds.calls.clear()
    r2 = svc.search("NGC 1365", ["NGC 1365"], "no-facility", "header")
    assert r2.metrics.total == 4 and len(FakeAds.calls) == 3


def test_select_abstracts_and_prompt():
    papers = [to_paper(d) for d in DOCS]
    m = bibliometrics(papers)
    chosen = select_abstracts(papers, m, max_abstracts=2)
    assert [p.bibcode for p in chosen] == ["2019ApJ...876...12A", "2024MNRAS.530.1000B"]
    assert all(
        p.abstract for p in select_abstracts(papers, m, 10)
    )  # poster without abstract skipped
    prompt = build_user_prompt("NGC 1365", chosen, m, ["FCC 121"])
    assert "TARGET: NGC 1365" in prompt and "ALIASES: FCC 121" in prompt
    assert "[2019ApJ...876...12A] 2019 | Alpha, A. et al. | 120 citations" in prompt
    assert "h-index 3" in prompt and "FACILITIES MENTIONED" in prompt


def test_synthesis_service_coerces_output():
    papers = [to_paper(d) for d in DOCS]
    m = bibliometrics(papers)
    svc = SynthesisService(client_factory=FakeGemini)
    r = svc.synthesise("NGC 1365", ["FCC 121"], papers, m, "key", "header", "gemini-2.5-flash", 10)
    assert r.n_abstracts == 3 and r.prompt_tokens == 1234
    assert r.synthesis.key_papers[0].bibcode == "2019ApJ...876...12A"  # brackets stripped
    assert any("1999XXX" in w for w in r.warnings)  # unknown bibcode flagged
    assert r.synthesis.facilities[0].name == "MUSE"
    assert svc.synthesise("NGC 1365", [], papers, m, "key", "header", "gemini-2.5-flash", 10).cached


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        lit_router, "get_literature_service", lambda: LiteratureService(client_factory=FakeAds)
    )
    monkeypatch.setattr(
        lit_router, "get_synthesis_service", lambda: SynthesisService(client_factory=FakeGemini)
    )
    return TestClient(main.app)


def test_literature_endpoint_requires_a_token(client, monkeypatch):
    from app import config

    monkeypatch.setattr(lit_router, "get_settings", lambda: config.Settings(_env_file=None))
    res = client.get("/api/literature", params={"q": "NGC 1365"})
    assert res.status_code == 401 and "Keys dialog" in res.json()["detail"]
    ok = client.get(
        "/api/literature",
        params={"q": "NGC 1365", "names": "FCC 121"},
        headers={"X-ADS-Token": "tok"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["token_source"] == "header" and body["metrics"]["total"] == 4
    bad = client.get("/api/literature", params={"q": "NGC 1365"}, headers={"X-ADS-Token": "bad"})
    assert bad.status_code == 401


def test_literature_uses_server_token_when_configured(client, monkeypatch):
    from app import config

    monkeypatch.setattr(
        lit_router, "get_settings", lambda: config.Settings(_env_file=None, ADS_DEV_KEY="srv")
    )
    body = client.get("/api/literature", params={"q": "NGC 1365"}).json()
    assert body["token_source"] == "server"
    status = client.get("/api/keys/status").json()
    assert status["ads_server_key"] is True and status["gemini_server_key"] is False


def test_synthesis_endpoint(client, monkeypatch):
    from app import config

    monkeypatch.setattr(lit_router, "get_settings", lambda: config.Settings(_env_file=None))
    res = client.post(
        "/api/synthesis",
        json={"q": "NGC 1365", "names": ["FCC 121"]},
        headers={"X-ADS-Token": "tok", "X-Gemini-Key": "gk"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["provider"] == "gemini" and body["key_source"] == "header"
    assert body["synthesis"]["summary"].startswith("NGC 1365")
    assert body["bibcodes_used"][0] == "2019ApJ...876...12A"
    missing = client.post("/api/synthesis", json={"q": "NGC 1365"}, headers={"X-ADS-Token": "tok"})
    assert missing.status_code == 401 and "Gemini" in missing.json()["detail"]
    bad = client.post(
        "/api/synthesis",
        json={"q": "NGC 1365"},
        headers={"X-ADS-Token": "tok", "X-Gemini-Key": "bad"},
    )
    assert bad.status_code == 401


def test_real_client_pages_and_reports_400(monkeypatch):
    import httpx

    from app.services.ads import AdsClient, AdsQueryError

    calls = []

    def fake_get(self, url, params=None, headers=None):
        calls.append(dict(params))
        if "object:" in params["q"]:
            return httpx.Response(
                400, json={"error": "object query failed"}, request=httpx.Request("GET", url)
            )
        start = params["start"]
        docs = [{"bibcode": f"b{start + i}"} for i in range(min(params["rows"], 3 - start))]
        return httpx.Response(
            200, json={"response": {"numFound": 3, "docs": docs}}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    c = AdsClient("tok")
    with pytest.raises(AdsQueryError, match="object query failed"):
        c.search('object:"X"')
    docs, n = c.search("abs:x", max_rows=10, fields="bibcode")
    assert n == 3 and [d["bibcode"] for d in docs] == ["b0", "b1", "b2"]
    assert calls[-1]["fl"] == "bibcode" and "Bearer" not in str(calls)
