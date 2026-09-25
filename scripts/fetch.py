#!/usr/bin/env python3
import asyncio
import re
from pathlib import Path

from playwright.async_api import async_playwright
from scrapers import (
    cdnlive,
    dami,
    embedsport,
    fawa,
    flyembed,
    futbolx,
    istreameast,
    mainportal,
    ovostream,
    pelotalibre,
    reedstreams,
    sportspass,
    streamcenter,
    streamfree,
    streamgate,
    streamtp,
    streamxhd,
    timstreams,
    tvf90,
    watchfooty,
    webcast,
    xyzstreams,
)
from scrapers.utils import get_logger, network

log = get_logger(Path(__file__).stem)

# Point to repo root (one directory up from scripts/)
REPO_ROOT = Path(__file__).parent.parent

files = [
    REPO_ROOT / "playlists" / f"{file}.m3u8"
    for file in (
        "base",
        "games",
        "vivatvsports"
    )
]

BASE_FILE, GAMES_FILE, COMBINED_FILE = files

EXCLUDED_SPORTS = {
    "MLB", "MMA", "NBA", "NFL", "NHL",
    "Baseball", "Basketball", "American Football", "Ice Hockey", "Hockey", "Racing", "Golf",
    "Tennis", "Rugby", "NRL"
}

# Compile case-insensitive word-boundary pattern
EXCLUDE_REGEX = re.compile(
    r"\b(?:" + "|".join(re.escape(s) for s in EXCLUDED_SPORTS) + r")\b",
    re.IGNORECASE,
)

def load_base() -> tuple[list[str], int]:
    data = BASE_FILE.read_text(encoding="utf-8")

    pattern = re.compile(r'tvg-chno="(\d+)"')

    last_chnl_num = max(map(int, pattern.findall(data)), default=0)

    return data.splitlines(), last_chnl_num


async def main() -> None:
    log.info(f"{'=' * 10} Scraper Started {'=' * 10}")

    log.info("Fetching base M3U8")

    base_m3u8, tvg_chno = load_base()

    async with async_playwright() as p:
        try:
            await network.setup_adblock()

            hdl_brwsr = await network.browser(p)

            pw_tasks = [
                asyncio.create_task(sportspass.scrape(hdl_brwsr)),
                asyncio.create_task(watchfooty.scrape(hdl_brwsr)),
            ]

            httpx_tasks = [
                asyncio.create_task(cdnlive.scrape()),
                asyncio.create_task(dami.scrape()),
                # asyncio.create_task(embedsport.scrape()),
                asyncio.create_task(fawa.scrape()),
                asyncio.create_task(flyembed.scrape()),
                asyncio.create_task(futbolx.scrape()),
                asyncio.create_task(istreameast.scrape()),
                asyncio.create_task(mainportal.scrape()),
                asyncio.create_task(ovostream.scrape()),
                # asyncio.create_task(pelotalibre.scrape()),
                asyncio.create_task(reedstreams.scrape()),
                asyncio.create_task(streamcenter.scrape()),
                asyncio.create_task(streamfree.scrape()),
                asyncio.create_task(streamgate.scrape()),
                asyncio.create_task(streamtp.scrape()),
                asyncio.create_task(streamxhd.scrape()),
                asyncio.create_task(timstreams.scrape()),
                asyncio.create_task(tvf90.scrape()),
                asyncio.create_task(webcast.scrape()),
                asyncio.create_task(xyzstreams.scrape()),
            ]

            await asyncio.gather(*(pw_tasks + httpx_tasks))

        finally:
            await hdl_brwsr.close()
            await network.client.aclose()

    additions = (
        dami.urls
        | embedsport.urls
        | fawa.urls
        | flyembed.urls
        | futbolx.urls
        | istreameast.urls
        | mainportal.urls
        | ovostream.urls
        | pelotalibre.urls
        | reedstreams.urls
        | sportspass.urls
        | streamcenter.urls
        | streamfree.urls
        | streamgate.urls
        | streamtp.urls
        | streamxhd.urls
        | tvf90.urls
        | watchfooty.urls
        | webcast.urls
        | xyzstreams.urls
        | cdnlive.urls
        | timstreams.urls
    )

    live_games: list[str] = []
    combined_channels: list[str] = []

    channel_idx = 1
    for event_name, event_info in sorted(additions.items()):
        tvg_id, logo, refer, source = (
            event_info[x]
            for x in (
                "tvg-id",
                "logo",
                "refer",
                "source",
            )
        )

        # Global Exclusion Check (matches event title or tvg-id/league string)
        if EXCLUDE_REGEX.search(event_name) or EXCLUDE_REGEX.search(tvg_id):
            log.debug(f"Skipping excluded event: {event_name}")
            continue

        ua: str = event_info.get("user-agent", network.UA)

        extinf_all = (
            f'#EXTINF:-1 tvg-chno="{tvg_chno + channel_idx}" tvg-id="{tvg_id}" '
            f'tvg-name="{event_name}" tvg-logo="{logo}" group-title="▷ Sukan 2",{event_name}'
        )

        extinf_live = (
            f'#EXTINF:-1 tvg-chno="{channel_idx}" tvg-id="{tvg_id}" '
            f'tvg-name="{event_name}" tvg-logo="{logo}" group-title="▷ Sukan 2",{event_name}'
        )

        vlc_block: list[str] = [
            f"#EXTVLCOPT:http-referrer={refer}",
            f"#EXTVLCOPT:http-user-agent={ua}",
            source,
        ]

        combined_channels.extend([extinf_all, *vlc_block])
        live_games.extend([extinf_live, *vlc_block])

        channel_idx += 1

    COMBINED_FILE.write_text(
        "\n".join(base_m3u8 + combined_channels) + "\n",
        encoding="utf-8",
    )

    log.info(f"Base + Games saved to {COMBINED_FILE.resolve()}")

    GAMES_FILE.write_text(
        '#EXTM3U url-tvg="https://raw.githubusercontent.com/keramatai/vivatvsports/refs/heads/default/scripts/epg.xml"\n'
        + "\n".join(live_games)
        + "\n",
        encoding="utf-8",
    )

    log.info(f"Games saved to {GAMES_FILE.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

    for hndlr in log.handlers:
        hndlr.flush()
        hndlr.stream.write("\n")
