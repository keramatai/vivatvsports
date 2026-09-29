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

# Matches the for-loop XOR obfuscation used by FlyEmbed / TimStreams:
#   var _a1 = [n,n,n], _b2 = k, _c3 = k, s = "";
#   for (var i = 0; i < _a1.length; i++) {
#       s += String.fromCharCode(((_a1[i] ^ _b2) - _c3 + 256) & 255);
#   }
LOOP_PTRN = re.compile(
    r"String\.fromCharCode\(\(\([\w\[\]]+\s*\^\s*(\w+)\)\s*-\s*(\w+)\s*\+\s*256\)\s*([%&])\s*(256|255)\)"
)

# Fallback: the .join("") variant used by some older pages.
JOIN_PTRN = re.compile(r'\(\[([^\]]+)\]\.join\(""\)')

# Extracts the final URL assignment from the decoded JS.
URL_PTRN = re.compile(r'(?:var\s+signed_)?url\s*=\s*"(.*?)";', re.I)


def cleanup(s: str) -> str:
    return re.sub(r"(\r|\n|\t)", "", s).strip()


def _decode_for_loop(html: str) -> str | None:
    """Decode the `var _x = [..], _y = k, _z = k; for(...) {...}` obfuscation."""
    arr_match = re.search(r"var\s+\w+\s*=\s*\[([\d,\s]+)\]", html)
    if not arr_match:
        return None

    loop_match = LOOP_PTRN.search(html)
    if not loop_match:
        return None

    xor_var, sub_var = loop_match.group(1), loop_match.group(2)
    op, mod = loop_match.group(3), int(loop_match.group(4))

    xor_match = re.search(rf"{xor_var}\s*=\s*(\d+)", html)
    sub_match = re.search(rf"{sub_var}\s*=\s*(\d+)", html)
    if not xor_match or not sub_match:
        return None

    x = int(xor_match.group(1))
    y = int(sub_match.group(1))

    num_list = [int(n) for n in arr_match.group(1).split(",")]

    if op == "&":
        return "".join(chr(((i ^ x) - y + 256) & mod) for i in num_list)
    return "".join(chr(((i ^ x) - y + 256) % mod) for i in num_list)


def _decode_join(html: str) -> str | None:
    """Decode the `([...].join(""))` obfuscation."""
    m = JOIN_PTRN.search(html)
    if not m:
        return None
    chars = re.findall(r'"([^"]*)"', m.group(1))
    if not chars:
        return None
    return "".join(chars).replace("\\/", "/")


async def process_event(url: str, url_num: int) -> tuple[str | None, str | None]:
    nones = None, None

    if not (html_data := await network.request(url, url_num, log=log)):
        return nones

    soup = HTMLParser(html_data.content)

    iframe = soup.css_first("iframe")

    if not iframe or not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found.")
        return nones

    if not (
        iframe_src_data := await network.request(
            iframe_src,
            url_num,
            headers={"Referer": url},
            log=log,
        )
    ):
        return nones

    iframe_html = iframe_src_data.text

    # Try the for-loop XOR form first (what FlyEmbed currently serves),
    # then fall back to the .join("") form.
    js = _decode_for_loop(iframe_html)

    if js is None:
        log.info(f"URL {url_num}) for-loop form not found, trying .join(\"\") form.")
        js = _decode_join(iframe_html)

    if js is None:
        log.warning(
            f"URL {url_num}) Unable to decipher m3u encryption. "
            f"len={len(iframe_html)} preview={iframe_html[:300]!r}"
        )
        return nones

    m3u_match = URL_PTRN.search(js)
    if not m3u_match:
        log.warning(
            f"URL {url_num}) No M3U8 source found. "
            f"decoded len={len(js)} preview={js[:300]!r}"
        )
        return nones

    raw = m3u_match.group(1)

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