import pytest

from app.feeds.opml import OpmlEntry, OpmlError, parse_opml, render_opml

READER_EXPORT = b"""<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>My feeds</title></head>
  <body>
    <outline text="Greek" title="Greek">
      <outline type="rss" text="Kathimerini" xmlUrl="https://www.kathimerini.gr/feed/"
               htmlUrl="https://www.kathimerini.gr/"/>
      <outline text="Economy">
        <outline type="rss" title="Naftemporiki" xmlUrl="https://www.naftemporiki.gr/feed/"
                 category="/business,/daily"/>
      </outline>
    </outline>
    <outline type="rss" xmlUrl="https://feeds.example.com/world.xml"/>
    <outline type="rss" text="Duplicate" xmlUrl="https://www.kathimerini.gr/feed/"/>
    <outline type="rss" text="Local file" xmlUrl="file:///etc/passwd"/>
    <outline type="rss" text="No address"/>
  </body>
</opml>"""


def test_reader_exports_become_sources_with_folders_and_categories_as_tags() -> None:
    result = parse_opml(READER_EXPORT)
    assert result.entries == [
        OpmlEntry("Kathimerini", "https://www.kathimerini.gr/feed/", ["Greek"], None, None),
        OpmlEntry(
            "Naftemporiki",
            "https://www.naftemporiki.gr/feed/",
            ["Greek", "Economy", "business", "daily"],
            None,
            None,
        ),
        OpmlEntry("feeds.example.com", "https://feeds.example.com/world.xml", [], None, None),
    ]
    # The duplicate address, the file: address; an outline with neither address nor children
    # is an empty folder, not a broken source.
    assert result.invalid == 2


def test_export_round_trips_names_addresses_tags_country_and_language() -> None:
    entries = [
        OpmlEntry("Wire & Co", "https://wire.example/rss?a=1&b=2", ["greek", "news"], "GR", "el"),
        OpmlEntry("Daily", "https://daily.example/atom", [], None, None),
    ]
    document = render_opml(entries, title="newsintel sources")
    assert document.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    assert parse_opml(document).entries == entries


def test_a_file_that_is_not_opml_says_so() -> None:
    with pytest.raises(OpmlError, match="not an OPML file"):
        parse_opml(b"<rss><channel/></rss>")
    with pytest.raises(OpmlError, match="could not be read"):
        parse_opml(b"<opml><body>")


def test_entities_are_never_expanded() -> None:
    bomb = b"""<?xml version="1.0"?>
<!DOCTYPE opml [<!ENTITY a "aaaaaaaaaa">]>
<opml version="2.0"><body><outline text="&a;" xmlUrl="https://x.example/rss"/></body></opml>"""
    with pytest.raises(OpmlError):
        parse_opml(bomb)
