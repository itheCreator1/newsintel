from app.search.aggregations import ROOT_ORDER, Counted, read_terms, terms


def test_a_filtered_nested_ranking_counts_root_articles_of_matching_records_only() -> None:
    within = {"terms": {"entities.type": ["PERSON"]}}
    assert terms("entities", "entities.id", 5, within=within) == {
        "nested": {"path": "entities"},
        "aggs": {
            "matching": {
                "filter": within,
                "aggs": {
                    "top": {
                        "terms": {"field": "entities.id", "size": 5, "order": ROOT_ORDER},
                        "aggs": {"articles": {"reverse_nested": {}}},
                    }
                },
            }
        },
    }


def test_read_terms_finds_nested_buckets_with_or_without_the_filter_level() -> None:
    bucket = {"key": "e1", "doc_count": 4, "articles": {"doc_count": 2}}
    filtered = {"matching": {"top": {"buckets": [bucket], "sum_other_doc_count": 3}}}
    assert read_terms(filtered, nested=True) == Counted([("e1", 2)], True)
    assert read_terms({"top": {"buckets": [bucket]}}, nested=True) == Counted([("e1", 2)], False)
    assert read_terms({"buckets": [bucket]}, nested=False) == Counted([("e1", 4)], False)
