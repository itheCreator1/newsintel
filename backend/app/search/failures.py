import structlog
from fastapi import HTTPException, status

from app.search.elasticsearch import ElasticsearchError, ElasticsearchRejected

log = structlog.get_logger()

REJECTED = {"code": "search_rejected", "message": "Search rejected the request"}


def search_failure(
    exc: ElasticsearchError, unavailable: str = "Search is unavailable"
) -> HTTPException:
    """The response for a request Elasticsearch did not carry out.

    A refused request is a 502 with the code `search_rejected`: a retry sends the same request and
    fails the same way, and it is not an outage. What Elasticsearch objected to goes to the log, not
    to the page. Only an Elasticsearch that could not answer is the 503 `unavailable`.
    """
    if isinstance(exc, ElasticsearchRejected):
        log.error("search_rejected", status=exc.status, error=str(exc))
        return HTTPException(status.HTTP_502_BAD_GATEWAY, REJECTED)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, unavailable)


def snapshot_gone(exc: ElasticsearchError) -> bool:
    """Whether a page's point in time has been released: Elasticsearch answers such a search 404."""
    return exc.status == 404
