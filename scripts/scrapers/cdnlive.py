import base64
import json
import re
from collections.abc import KeysView
from dataclasses import dataclass
from functools import partial
from typing import Any
from urllib.parse import urlparse

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "CDNLIVE"

CACHE_FILE = Cache(TAG, exp=7_200)

API_FILE = Cache(f"{TAG}-api", exp=19_800)

BASE_URL = "https://api.cdnlivetv.tv"


@dataclass(kw_only=True, slots=True)
class CDNEvent(Event):
    logo: str | None = None


async def process_event(url: str, url_num: int) -> str | None:
    # Domain parsing for strict referer requirement
    parsed_url = urlparse(url)
    referer = f"{parsed_url.scheme}://{parsed_url.netloc}/"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Referer": referer
    }

    if not (html_data := await network.request(url, url_num, headers=headers, log=log)):
        # Try fallback mirror if the primary .tv url fails
        if "cdnlivetv.tv" in url:
            url = url.replace("cdnlivetv.tv", "cdnlivetv.is")
            if not (html_data := await network.request(url, url_num, headers=headers, log=log)):
                return
        else:
            return

    html = html_data.text
    m3u8_url = ""

    # Strategy 1: Direct atob concatenation (e.g. var X = atob("...") + atob("..."))
    atob_concat_mtch = re.search(r"var\s+[a-zA-Z0-9_]+\s*=\s*(atob\([^;]+;)", html)
    if atob_concat_mtch:
        parts = re.findall(r"atob\s*\(\s*[\"']([^\"']+)[\"']\s*\)", atob_concat_mtch.group(1))
        for part in parts:
            # Fix base64 padding for python decode
            b64 = part.replace("-", "+").replace("_", "/")
            b64 += "=" * ((4 - len(b64) % 4) % 4)
            try:
                m3u8_url += base64.b64decode(b64).decode("utf-8")
            except Exception:
                pass

    # Strategy 2: Decoder function with base64 variable lookups
    if not m3u8_url:
        decoder_mtch = re.search(r"function\s+([a-zA-Z0-9_]+)\s*\([a-zA-Z0-9_]+\)\s*\{[\s\S]*?atob", html)
        if decoder_mtch:
            decoder_name = decoder_mtch.group(1)
            concat_regex = rf"var\s+([a-zA-Z0-9_]+)\s*=\s*{decoder_name}\([^;]+;"
            concat_mtch = re.search(concat_regex, html)
            if concat_mtch:
                var_regex = rf"{decoder_name}\(([a-zA-Z0-9_]+)\)"
                vars_list = re.findall(var_regex, concat_mtch.group(0))
                for v in vars_list:
                    val_mtch = re.search(rf"var\s+{v}\s*=\s*['\"]([^'\"]+)['\"]", html)
                    if val_mtch and val_mtch.group(1):
                        b64 = val_mtch.group(1).replace("-", "+").replace("_", "/")
                        b64 += "=" * ((4 - len(b64) % 4) % 4)
                        try:
                            m3u8_url += base64.b64decode(b64).decode("utf-8")
                        except Exception:
                            pass

    # Strategy 3: Plain text .m3u8 URL in page
    if not m3u8_url:
        m3u8_mtch = re.search(r"(https?:\/\/[^\s\"'<>]+\.m3u8[^\s\"'<>]*)", html, re.I)
        if m3u8_mtch:
            m3u8_url = m3u8_mtch.group(1)

    if m3u8_url and ".m3u8" in m3u8_url:
        log.info(f"URL {url_num}) Captured M3U8")
        return m3u8_url

    log.warning(f"URL {url_num}) Unable to decipher m3u encryption.")
    return None


async def get_events(cached_keys: KeysView[str]) -> list[CDNEvent]:
    now = Time.rn()
    events: list[CDNEvent] = []

    if not (api_data := API_FILE.load(per_entry=False)):
        log.info("Refreshing API cache")
        api_data = {"timestamp": now.timestamp()}
        if r := await network.request(
            f"{BASE_URL}/api/v1/events/sports/?user=cdnlivetv&plan=free",
            log=log,
        ):
            api_data.update(r.json())
            api_data["timestamp"] = now.timestamp()
        API_FILE.write(api_data)

    start_dt = now.delta(hours=-3)
    end_dt = now.delta(hours=12)

    sports_data = api_data.get("cdn-live-tv", {})

    sport_mapping = {
        "Soccer": "Football",
        "Football": "Football",
        "Basketball": "Basketball",
        "NBA": "Basketball",
        "NFL": "American Football",
        "NCAA": "American Football",
        "Baseball": "Baseball",
        "MLB": "Baseball",
        "Hockey": "Hockey",
        "NHL": "Hockey",
        "Motorsport": "Motorsport",
        "Tennis": "Tennis",
        "Golf": "Golf",
        "UFC": "MMA",
        "WWE": "MMA",
        "MMA": "MMA",
        "Cricket": "Cricket",
        "Darts": "Darts",
        "Rugby": "Rugby"
    }

    for sport_key, category in sport_mapping.items():
        if sport_key not in sports_data or not isinstance(sports_data[sport_key], list):
            continue
        
        for item in sports_data[sport_key]:
            channels = item.get("channels")
            if not channels or not isinstance(channels, list):
                continue

            home_team = item.get("homeTeam", "")
            away_team = item.get("awayTeam", "")
            name = f"{home_team} vs {away_team}".strip(" vs ")

            start_time = item.get("start")
            if not start_time:
                continue

            # CDNLiveTV provides timestamps in UTC[cite: 5]
            event_dt = Time.from_str(start_time, tz_name="UTC")

            if not start_dt <= event_dt <= end_dt:
                continue

            if not (stream_url := channels[0].get("url")):
                continue

            if f"[{category}] {name} ({TAG})" in cached_keys:
                continue

            events.append(
                CDNEvent(
                    sport=category,
                    name=name,
                    link=stream_url,
                    logo=item.get("logo"),
                    timestamp=now.timestamp(),
                )
            )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()
    valid_urls = {k: v for k, v in cached_urls.items() if v["source"]}
    valid_count = cached_count = len(valid_urls)
    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=ev.link,
                url_num=i,
            )

            source = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

            key = f"[{ev.sport}] {ev.name} ({TAG})"
            tvg_id, logo = leagues.get_tvg_info(ev.sport, ev.name)

            entry = {
                "source": source,
                "logo": ev.logo or logo,
                "refer": ev.link,
                "timestamp": ev.timestamp,
                "tvg-id": tvg_id or "Live.Event.us",
            }

            cached_urls[key] = entry

            if source:
                valid_count += 1
                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")
    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)