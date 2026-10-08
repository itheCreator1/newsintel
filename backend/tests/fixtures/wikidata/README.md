# Wikidata API fixtures

Responses of the MediaWiki Action API on www.wikidata.org, in the shapes the client asks for
(`format=json`, `formatversion=2`). The unit tests read them through `httpx.MockTransport`, so no
test reaches the network.

Q42 (Douglas Adams) and its identifiers are real; the other items are trimmed or invented for the
tests. `python -m app.wikidata.contract` (the "Wikidata contract" workflow) asks the real API for
Q42 and the class roots and checks that the parsers still read what it returns.
