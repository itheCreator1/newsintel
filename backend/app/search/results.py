"""One Elasticsearch article hit as a search result, shared by search and the monitors."""

from typing import Any

from app.search.schemas import (
    HighlightSegment,
    SearchResult,
    SearchResultEntity,
    SearchResultSource,
    StoryClusterRef,
)

RESULT_FIELDS = [
    "article_id",
    "title",
    "effective_date",
    "distinct_source_count",
    "provenance",
    "primary_story_country",
    "story_cluster_id",
    "cluster_source_count",
    "entities",
]
# Entity chips shown on a result; indexing stores an article's entities most relevant first.
RESULT_ENTITIES = 3


def _segments(values: list[str]) -> list[HighlightSegment]:
    text_value = " … ".join(values)[:1200]
    parts = text_value.split("\ue000")
    output: list[HighlightSegment] = []
    marked = False
    for part in parts:
        if "\ue001" in part:
            marked_text, remainder = part.split("\ue001", 1)
            if marked_text:
                output.append(HighlightSegment(text=marked_text, marked=True))
            if remainder:
                output.append(HighlightSegment(text=remainder))
            marked = False
        elif part:
            output.append(HighlightSegment(text=part, marked=marked))
        marked = True
    return output


def search_result(hit: dict[str, Any]) -> SearchResult:
    """One Elasticsearch article hit as a result; highlights appear only if the hit has them."""
    source = hit["_source"]
    highlight_values = [value for values in hit.get("highlight", {}).values() for value in values]
    segments = _segments(highlight_values)
    refs = {
        item["source_id"]: SearchResultSource(
            id=item["source_id"], name=item["source_name"], country=item.get("source_country")
        )
        for item in source["provenance"]
    }
    return SearchResult(
        article_id=source["article_id"],
        title=source["title"],
        effective_date=source["effective_date"],
        distinct_source_count=source["distinct_source_count"],
        sources=sorted({item["source_name"] for item in source["provenance"]}),
        source_refs=sorted(refs.values(), key=lambda ref: (ref.name.lower(), str(ref.id))),
        story_country=source.get("primary_story_country"),
        story_cluster=(
            StoryClusterRef(
                id=source["story_cluster_id"],
                source_count=source["cluster_source_count"],
            )
            if source.get("story_cluster_id")
            else None
        ),
        summary="".join(segment.text for segment in segments)[:500] or None,
        highlights=segments,
        entities=[
            SearchResultEntity(id=item["id"], text=item["text"], type=item["type"])
            for item in source.get("entities", [])[:RESULT_ENTITIES]
        ],
    )
