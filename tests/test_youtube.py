"""Keyless YouTube watcher — pure parsing (no network). The fetch_* functions do
HTTP; the parse_*/channel-id helpers are exercised here with string fixtures."""

from chat import youtube

# A trimmed but realistic channel upload Atom feed (two entries, newest first).
SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>codewithbod</title>
  <author><name>Code With Bod</name></author>
  <entry>
    <id>yt:video:AAAAAAAAAAA</id>
    <yt:videoId>AAAAAAAAAAA</yt:videoId>
    <title>Newest video — building VibeClip</title>
    <published>2026-06-12T10:00:00+00:00</published>
    <link rel="alternate" href="https://www.youtube.com/watch?v=AAAAAAAAAAA"/>
  </entry>
  <entry>
    <id>yt:video:BBBBBBBBBBB</id>
    <yt:videoId>BBBBBBBBBBB</yt:videoId>
    <title>Older video</title>
    <published>2026-06-01T10:00:00+00:00</published>
    <link rel="alternate" href="https://www.youtube.com/watch?v=BBBBBBBBBBB"/>
  </entry>
</feed>"""


def test_parse_feed_extracts_videos_newest_first():
    vids = youtube.parse_feed(SAMPLE_FEED)
    assert [v["video_id"] for v in vids] == ["AAAAAAAAAAA", "BBBBBBBBBBB"]
    assert vids[0]["title"] == "Newest video — building VibeClip"
    assert vids[0]["url"] == "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    # The author name is the channel title (preferred over the feed <title>).
    assert vids[0]["channel_title"] == "Code With Bod"


def test_parse_feed_tolerates_garbage():
    assert youtube.parse_feed("not xml at all") == []
    assert youtube.parse_feed("") == []


def test_channel_id_from_html():
    html = '...,"channelId":"UC0123456789abcdefghijkl","foo":1...'
    assert youtube._channel_id_from_html(html) == "UC0123456789abcdefghijkl"
    # canonical /channel/ link fallback
    html2 = '<link rel="canonical" href="https://www.youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa">'
    assert youtube._channel_id_from_html(html2) == "UCaaaaaaaaaaaaaaaaaaaaaa"
    assert youtube._channel_id_from_html("nothing here") is None


def test_normalize_channel_input_handles_each_form():
    # Raw UC id → no scrape needed.
    cid, page = youtube._normalize_channel_input("UC0123456789abcdefghijkl")
    assert cid == "UC0123456789abcdefghijkl" and page is None
    # /channel/ URL → id extracted directly.
    cid, page = youtube._normalize_channel_input(
        "https://youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa")
    assert cid == "UCaaaaaaaaaaaaaaaaaaaaaa" and page is None
    # @handle → scrape the handle page.
    cid, page = youtube._normalize_channel_input("@codewithbod")
    assert cid is None and page.endswith("/@codewithbod")
    # bare handle → same.
    cid, page = youtube._normalize_channel_input("codewithbod")
    assert cid is None and page.endswith("/@codewithbod")
    # empty → nothing.
    assert youtube._normalize_channel_input("") == (None, None)


def test_title_from_html():
    html = '<meta property="og:title" content="Code With Bod">'
    assert youtube._title_from_html(html) == "Code With Bod"
    html2 = "<title>Code With Bod - YouTube</title>"
    assert youtube._title_from_html(html2) == "Code With Bod"
