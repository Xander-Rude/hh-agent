from app.targeted_hunt.research import _queries


def test_research_queries_keep_company_and_role_context():
    queries = _queries("Иви", "Заместитель технического директора")
    assert len(queries) == 3
    assert all("Иви" in query for query in queries)
    assert any("Заместитель технического директора" in query for query in queries)
