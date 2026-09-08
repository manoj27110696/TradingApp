from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape

import pytest

from app.providers.market_chameleon import MarketChameleonFeaturedIdeasProvider


def test_market_chameleon_provider_parses_rss_items():
    provider = MarketChameleonFeaturedIdeasProvider("https://example.com/feed.xml")
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>Bullish On SPY? Consider This Credit Put Spread</title>
          <description>SPY credit put spread idea with elevated option volume.</description>
          <link>https://example.com/spy-idea</link>
        </item>
        <item>
          <title>Market update without a ticker</title>
          <description>No option strategy here.</description>
          <link>https://example.com/market</link>
        </item>
      </channel>
    </rss>
    """

    ideas = provider._parse_feed(feed, {"SPY"})

    assert len(ideas) == 1
    assert ideas[0].symbol == "SPY"
    assert ideas[0].strategy == "bull put"
    assert "<p>" not in ideas[0].description
    assert ideas[0].url == "https://example.com/spy-idea"


def test_market_chameleon_provider_parses_atom_items():
    provider = MarketChameleonFeaturedIdeasProvider("https://example.com/atom.xml")
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>QQQ Traders May Like This Bull Call Spread</title>
        <summary>QQQ call spread setup from a research feed.</summary>
        <link href="https://example.com/qqq-idea" />
      </entry>
    </feed>
    """

    ideas = provider._parse_feed(feed, {"QQQ"})

    assert len(ideas) == 1
    assert ideas[0].symbol == "QQQ"
    assert ideas[0].strategy == "bull call"
    assert ideas[0].url == "https://example.com/qqq-idea"


def test_market_chameleon_provider_trims_large_html_descriptions():
    provider = MarketChameleonFeaturedIdeasProvider("https://example.com/feed.xml")
    huge_html = "<p>SPY bull put spread</p><img src=\"data:image/png;base64,AAA\" />" + (" detail" * 200)

    description = provider._clean_description(huge_html)

    assert "data:image" not in description
    assert "<img" not in description
    assert len(description) <= 600


@pytest.mark.parametrize(
    ("misleading_word", "company", "expected_symbol"),
    [
        ("Q", "Bath & Body Works", "BBWI"),
        ("III", "Summit Therapeutics", "SMMT"),
        ("FDA", "Zymeworks", "ZYME"),
        ("US", "Nuwellis", "NUWE"),
        ("MODA", "Biohaven", "BHVN"),
    ],
)
def test_feed_uses_explicit_ticker_instead_of_uppercase_prose(
    misleading_word: str,
    company: str,
    expected_symbol: str,
):
    provider = MarketChameleonFeaturedIdeasProvider("https://example.com/feed.xml")
    published = format_datetime(datetime.now(timezone.utc) - timedelta(days=1))
    escaped_company = escape(company)
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>{misleading_word} update for {escaped_company} ({expected_symbol})</title>
          <description>{escaped_company} options activity is elevated.</description>
          <pubDate>{published}</pubDate>
          <link>https://example.com/article</link>
        </item>
      </channel>
    </rss>
    """

    ideas = provider._parse_feed(feed, set())

    assert [idea.symbol for idea in ideas] == [expected_symbol]


def test_feed_discards_stale_articles():
    provider = MarketChameleonFeaturedIdeasProvider("https://example.com/feed.xml", max_age_days=7)
    published = format_datetime(datetime.now(timezone.utc) - timedelta(days=8))
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>Bath &amp; Body Works (BBWI) options idea</title>
          <description>BBWI call spread setup.</description>
          <pubDate>{published}</pubDate>
          <link>https://example.com/article</link>
        </item>
      </channel>
    </rss>
    """

    assert provider._parse_feed(feed, set()) == []


def test_feed_does_not_treat_generic_uppercase_word_as_ticker():
    provider = MarketChameleonFeaturedIdeasProvider("https://example.com/feed.xml")
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>FDA provides a market update</title>
          <description>No explicitly identified company ticker appears here.</description>
          <link>https://example.com/article</link>
        </item>
      </channel>
    </rss>
    """

    assert provider._parse_feed(feed, set()) == []


def test_feed_can_extract_symbol_from_market_chameleon_url():
    provider = MarketChameleonFeaturedIdeasProvider("https://example.com/feed.xml")
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>FDA update for a biotechnology company</title>
          <description>Options activity is elevated.</description>
          <link>https://marketchameleon.com/Overview/ZYME/Some-Article</link>
        </item>
      </channel>
    </rss>
    """

    ideas = provider._parse_feed(feed, set())

    assert [idea.symbol for idea in ideas] == ["ZYME"]
