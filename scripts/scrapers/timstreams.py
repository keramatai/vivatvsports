import json
import re
from collections.abc import KeysView
from dataclasses import dataclass
from functools import partial
from typing import Any
from urllib.parse import urljoin

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}
TAG = "TIMST"
CACHE_FILE = Cache(TAG, exp=7_200)
API_FILE = Cache(f"{TAG}-api", exp=19_800)
BASE_URL = "https://timst.top"

@dataclass(kw_only=True, slots=True)
class TIMEvent(Event):
    logo: str | None = None

async def process_event(url: str, url_num: int) -> str | None:
    if not (html_data := await network.request(url, url_num, log=log)):
        return

    # 1. Find the obfuscated array
    arr_match = re.search(r"var\s+\w+\s*=\s*\[([\d,]+)\]", html_data.text)
    if not arr_match:
        log.warning(f"URL {url_num}) Unable to find array for m3u encryption.")
        return
    
    num_list = [int(n) for n in arr_match.group(1).split(',')]

    # 2. Find the character decoding loop formula to extract the variable names dynamically
    loop_match = re.search(r"String\.fromCharCode\(\(\([\w\[\]]+\s*\^\s*(\w+)\)\s*-\s*(\w+)\s*\+\s*256\)\s*(?:%|&)\s*(?:256|255)\)", html_data.text)
    if not loop_match:
        log.warning(f"URL {url_num}) Unable to find decoding loop variables.")
        return
    
    xor_var_name = loop_match.group(1)
    sub_var_name = loop_match.group(2)

    # 3. Find the integer values assigned to those specific variables
    xor_match = re.search(rf"{xor_var_name}\s*=\s*(\d+)", html_data.text)
    sub_match = re.search(rf"{sub_var_name}\s*=\s*(\d+)", html_data.text)

    if not xor_match or not sub_match:
        log.warning(f"URL {url_num}) Unable to decipher m3u encryption keys.")
        return

    x = int(xor_match.group(1))
    y = int(sub_match.group(1))

    # 4. Decrypt natively
    js = "".join(chr(((i ^ x) - y + 256) % 256) for i in num_list)

    # 5. Extract the direct M3U8 URL from the decoded text
    m3u_mtch = re.search(r"https?:\/\/[^\x22\x27<>\s]+\.m3u8", js)
    if not m3u_mtch:
        log.warning(f"URL {url_num}) No M3U8 source found.")
        return

    log.info(f"URL {url_num}) Captured M3U8")
    return m3u_mtch.group(0)

async def get_events(cached_keys: KeysView[str]) -> list[TIMEvent]:
    now = Time.rn()
    events: list[TIMEvent] = []

    if not (api_data := API_FILE.load(per_entry=False)):
        log.info("Refreshing API cache")
        api_data = {"timestamp": now.timestamp()}
        if r := await network.request(urljoin(BASE_URL, "api/live-upcoming"), log=log):
            api_data: dict[str, list[dict[str, Any]]] = r.json()
            api_data["timestamp"] = now.timestamp()
        API_FILE.write(api_data)

    start_dt = now.delta(hours=-3)
    end_dt = now.delta(minutes=30)
    sport_genres = api_data.get("genres", {})

    for event in api_data.get("events") or []:
        name = event.get("name")
        raw_genre = event.get("genre")
        event_time = event.get("time")
        streams = event.get("streams", [])

        if not all([name, raw_genre, event_time, streams]):
            continue

        # Filter out VIP streams as they won't decrypt correctly
        valid_streams = [st for st in streams if not st.get("vip")]
        if not valid_streams:
            continue

        event_dt = Time.from_str(event_time, tz_name="EST")

        # Handle updated genre mapping structure
        sport = "Live Event"
        if isinstance(sport_genres, dict) and str(raw_genre) in sport_genres:
            genre_val = sport_genres[str(raw_genre)]
            sport = genre_val.get("name", genre_val) if isinstance(genre_val, dict) else genre_val

        if not start_dt <= event_dt <= end_dt:
            continue

        if not (stream_url := valid_streams[0].get("url")):
            continue

        if f"[{sport}] {name} ({TAG})" in cached_keys:
            continue

        events.append(
            TIMEvent(
                sport=sport,
                name=name,
                link=stream_url,
                logo=event.get("logo"),
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
