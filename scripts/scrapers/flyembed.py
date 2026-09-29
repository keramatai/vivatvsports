import json
import re
from collections.abc import KeysView
from functools import partial
from urllib.parse import urlparse

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "FLYEMBD"

CACHE_FILE = Cache(TAG, exp=7_200)

API_FILE = Cache(f"{TAG}-api", exp=19_800)

API_URL = "https://ovogoal.cyou/api/v2/flyembed2.json"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)


def cleanup(s: str) -> str:
    return re.sub(r"(\r|\n|\t)", "", s).strip()


async def process_event(url: str, url_num: int) -> tuple[str | None, str | None]:
    nones = None, None

    if not (html_data := await network.request(url, url_num, log=log)):
        return nones

    soup = HTMLParser(html_data.content)

    iframe = soup.css_first("iframe")

    if not iframe or not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found.")
        return nones

    elif not (
        iframe_src_data := await network.request(
            iframe_src,
            url_num,
            headers={"Referer": url},
            log=log,
        )
    ):
        return nones

    num_list_ptrn = re.compile(r'\[([^\]]*)\]\.join\(""\)')

    m3u_ptrn = re.compile(r'(var\s?signed_)?url\s?=\s?"(.*)";', re.I)

    # Extract the XOR/subtract variable names straight from the decoding loop,
    # rather than guessing which `_[a-z]+\d+ = \d+` assignments are the keys.
    loop_ptrn = re.compile(
        r'String\.fromCharCode\(\(\((\w+)\[(\w+)\]\s*\^\s*(\w+)\)\s*-\s*(\w+)\s*\+\s*256\)\s*([%&])\s*(256|255)\)'
    )

    if not (num_list_mtch := num_list_ptrn.findall(iframe_src_data.text)):
        log.warning(f"URL {url_num}) Unable to decipher m3u encryption.")
        return nones

    if not (loop_mtch := loop_ptrn.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) Unable to decipher m3u encryption.")
        return nones

    xor_var, sub_var, op, mod = (
        loop_mtch.group(3),
        loop_mtch.group(4),
        loop_mtch.group(5),
        int(loop_mtch.group(6)),
    )

    xor_match = re.search(rf"{xor_var}\s*=\s*(\d+)", iframe_src_data.text)
    sub_match = re.search(rf"{sub_var}\s*=\s*(\d+)", iframe_src_data.text)

    if not xor_match or not sub_match:
        log.warning(f"URL {url_num}) Unable to decipher m3u encryption keys.")
        return nones

    x = int(xor_match.group(1))
    y = int(sub_match.group(1))

    num_list = (int(n.strip()) for n in num_list_mtch[-1].split(","))

    if op == "&":
        js = "".join(chr(((i ^ x) - y + 256) & mod) for i in num_list)
    else:
        js = "".join(chr(((i ^ x) - y + 256) % mod) for i in num_list)

    if not (m3u_mtch := m3u_ptrn.search(js)):
        log.warning(f"URL {url_num}) No M3U8 source found.")
        return nones

    raw = m3u_mtch.group(2)

    try:
        stream_url = json.loads(f'"{raw}"')
    except Exception:
        stream_url = raw.replace("\\/", "/")

    stream_url = re.sub(r"(?<!:)/{2,}", "/", stream_url)

    log.info(f"URL {url_num}) Captured M3U8")

    p = urlparse(iframe_src)
    origin = f"{p.scheme}://{p.netloc}"

    return (
        f"{stream_url}|User-Agent={UA}&Referer={origin}/&Origin={origin}",
        iframe_src,
    )


async def get_events(cached_keys: KeysView[str]) -> list[Event]:
    now = Time.rn()

    events: list[Event] = []

    if not (api_data := API_FILE.load(per_entry=False, ts_index=-1)):
        log.info("Refreshing API cache")

        api_data = [{"timestamp": now.timestamp()}]

        if r := await network.request(API_URL, log=log):
            api_data: list[dict[str, str]] = r.json()

            api_data[-1]["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    start_dt = now.delta(hours=-3)
    end_dt = now.delta(minutes=30)

    for event_group in api_data:
        if not all(
            values := [
                event_group.get(x)
                for x in (
                    "League",
                    "Team1",
                    "Team2",
                    "MatchDate",
                    "MatchStartTime",
                    "IframeURL",
                )
            ]
        ):
            continue

        sport, away, home, date, time, link = values

        event_dt = Time.from_str(f"{date} {time}", tz_name="ALMT")

        if not start_dt <= event_dt <= end_dt:
            continue

        name = f"{away.strip()} vs {home.strip()}"

        sport, name = cleanup(sport), cleanup(name)

        if f"[{sport}] {name} ({TAG})" in cached_keys:
            continue

        events.append(
            Event(
                sport=sport,
                name=name,
                link=link,
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

    log.info('Scraping from "https://flyembed.xyz"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=ev.link,
                url_num=i,
            )

            source, iframe = await network.safe_process(
                handler,
                url_num=i,
                timeout_return=(None, None),
                semaphore=network.HTTP_S,
                log=log,
            )

            key = f"[{ev.sport}] {ev.name} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(ev.sport, ev.name)

            entry = {
                "source": source,
                "logo": logo,
                "refer": iframe,
                "timestamp": ev.timestamp,
                "tvg-id": tvg_id or "Live.Event.us",
                "link": ev.link,
            }

            cached_urls[key] = entry

            if source:
                valid_count += 1

                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)