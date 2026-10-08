"""Which Wikidata classes stand for each NER entity type.

An item matches a type when one of its P31 classes is, or is a subclass (P279, up to four
levels) of, one of these roots. The check scores a candidate; it never rejects one.
"""

TYPE_ROOTS: dict[str, tuple[str, ...]] = {
    "PERSON": ("Q5",),  # human
    "ORG": ("Q43229",),  # organization
    "GPE": ("Q6256", "Q515", "Q10864048"),  # country, city, first-level administrative division
    "LOCATION": ("Q2221906", "Q618123"),  # geographic location, geographical feature
}
SUBCLASS_DEPTH = 4
