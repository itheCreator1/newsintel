import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref_src",
}


def normalize_article_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if not scheme or not hostname:
        raise ValueError("Article URL must be absolute")
    port = parsed.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host if port is None or default_port else f"{host}:{port}"
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_PARAMETERS and not key.casefold().startswith("utm_")
    )
    return urlunsplit((scheme, netloc, parsed.path or "/", urlencode(query, doseq=True), ""))


def normalized_title_hash(title: str) -> str:
    normalized = re.sub(r"\s+", " ", title).strip().casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()
