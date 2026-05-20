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

    ideas = provider._parse_feed(feed, set())

    assert len(ideas) == 1
    assert ideas[0].symbol == "QQQ"
    assert ideas[0].strategy == "bull call"
    assert ideas[0].url == "https://example.com/qqq-idea"
