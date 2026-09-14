import re
from dataclasses import dataclass, field
from datetime import date


class SearchSyntaxError(ValueError):
    pass


FIELDS = "source|country|after|before|entity|keyword|language|story_country|mentioned_country"
TOKEN = re.compile(rf'(?:{FIELDS}):(?:"[^"]*"|\S+)|"[^"]*"|\S+')


@dataclass
class ParsedQuery:
    terms: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    source_values: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    entity_values: list[str] = field(default_factory=list)
    keyword_values: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    story_countries: list[str] = field(default_factory=list)
    mentioned_countries: list[str] = field(default_factory=list)
    after: date | None = None
    before: date | None = None


def parse_query(value: str) -> ParsedQuery:
    if value.count('"') % 2:
        raise SearchSyntaxError("Unterminated quoted phrase")
    tokens = TOKEN.findall(value)
    if "".join(tokens).replace(" ", "") != value.replace(" ", ""):
        raise SearchSyntaxError("Malformed search syntax")
    parsed = ParsedQuery()
    expect_term = True
    for token in tokens:
        if token == "AND":
            if expect_term:
                raise SearchSyntaxError("AND must appear between search terms")
            expect_term = True
            continue
        if ":" in token:
            field_name, raw = token.split(":", 1)
            if field_name not in {
                "source",
                "country",
                "after",
                "before",
                "entity",
                "keyword",
                "language",
                "story_country",
                "mentioned_country",
            }:
                raise SearchSyntaxError(f"Unsupported search field: {field_name}")
            raw = raw.strip('"')
            if not raw:
                raise SearchSyntaxError(f"{field_name}: requires a value")
            if field_name == "source":
                parsed.source_values.append(raw)
            elif field_name == "country":
                if len(raw) != 2 or not raw.isalpha():
                    raise SearchSyntaxError("country: requires a two-letter source country")
                parsed.countries.append(raw.upper())
            elif field_name == "entity":
                parsed.entity_values.append(raw)
            elif field_name == "keyword":
                parsed.keyword_values.append(raw)
            elif field_name == "language":
                if not 2 <= len(raw) <= 16 or not raw.replace("-", "").isalpha():
                    raise SearchSyntaxError("language: requires a language code")
                parsed.languages.append(raw.casefold())
            elif field_name in {"story_country", "mentioned_country"}:
                if len(raw) != 2 or not raw.isalpha():
                    raise SearchSyntaxError(f"{field_name}: requires a two-letter country")
                target = (
                    parsed.story_countries
                    if field_name == "story_country"
                    else parsed.mentioned_countries
                )
                target.append(raw.upper())
            else:
                try:
                    parsed_date = date.fromisoformat(raw)
                except ValueError as exc:
                    raise SearchSyntaxError(f"{field_name}: requires YYYY-MM-DD") from exc
                setattr(parsed, field_name, parsed_date)
        elif token.startswith('"'):
            parsed.phrases.append(token[1:-1])
        else:
            parsed.terms.append(token)
        expect_term = False
    if tokens and expect_term:
        raise SearchSyntaxError("AND must be followed by a search term")
    return parsed
