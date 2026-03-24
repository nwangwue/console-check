from app.services.unlocode import UnlocodeService


def get_service():
    return UnlocodeService("data/us_locode.csv")


def test_exact_match():
    svc = get_service()
    result = svc.resolve("Dallas", "TX")
    assert result.resolved is True
    assert result.code == "DAL"
    assert result.confidence == 1.0


def test_exact_match_case_insensitive():
    svc = get_service()
    result = svc.resolve("dallas", "tx")
    assert result.resolved is True
    assert result.code == "DAL"


def test_normalized_saint():
    svc = get_service()
    result = svc.resolve("St. Louis", "MO")
    assert result.resolved is True
    assert result.confidence >= 0.85


def test_normalized_fort():
    svc = get_service()
    result = svc.resolve("Ft. Worth", "TX")
    assert result.resolved is True
    assert result.confidence >= 0.85


def test_fuzzy_match():
    svc = get_service()
    # Misspelling of "Springfield"
    result = svc.resolve("Springfild", "IL")
    assert result.resolved is True
    assert result.confidence >= 0.85


def test_no_match_bad_state():
    svc = get_service()
    result = svc.resolve("Xyzzyville", "ZZ")
    assert result.resolved is False
    assert result.code is None


def test_search():
    svc = get_service()
    results = svc.search("DAL")
    assert len(results) > 0
    codes = [e.code for e in results]
    assert "DAL" in codes
