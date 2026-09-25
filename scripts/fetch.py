#!/usr/bin/env python3
import asyncio
import re
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright
from scrapers import (
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

files = [
    Path(__file__).parent / f"{file}.m3u8"
    for file in (
        "base",
        "matches",
        "vivatvsports"
    )
]

BASE_FILE, EVENTS_FILE, COMBINED_FILE = files

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

            xtrnl_brwsr = await network.browser(p, external=True)

            pw_tasks = [
                asyncio.create_task(sportspass.scrape(hdl_brwsr)),
                asyncio.create_task(watchfooty.scrape(hdl_brwsr)),
            ]

            httpx_tasks = [
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

            await xtrnl_brwsr.close()

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
        | timstreams.urls
        | tvf90.urls
        | watchfooty.urls
        | webcast.urls
        | xyzstreams.urls
    )

    live_events: list[str] = []

    combined_channels: list[str] = []

    for i, (event_name, event_info) in enumerate(
        sorted(additions.items()),
        start=1,
    ):
        tvg_id, logo, refer, source = (
            event_info[x]
            for x in (
                "tvg-id",
                "logo",
                "refer",
                "source",
            )
        )

        ua: str = event_info.get("user-agent", network.UA)

        extinf_all = (
            f'#EXTINF:-1 tvg-chno="{tvg_chno + i}" tvg-id="{tvg_id}" '
            f'tvg-name="{event_name}" tvg-logo="{logo}" group-title="Live Events",{event_name}'
        )

        extinf_live = (
            f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{tvg_id}" '
            f'tvg-name="{event_name}" tvg-logo="{logo}" group-title="Live Events",{event_name}'
        )

        vlc_block: list[str] = [
            f"#EXTVLCOPT:http-referrer={refer}",
            f"#EXTVLCOPT:http-user-agent={ua}",
            source,
        ]

        combined_channels.extend(["\n" + extinf_all, *vlc_block])

        live_events.extend(["\n" + extinf_live, *vlc_block])

    COMBINED_FILE.write_text(
        "\n".join(base_m3u8 + combined_channels),
        encoding="utf-8",
    )

    log.info(f"Base + Events saved to {COMBINED_FILE.resolve()}")

    EVENTS_FILE.write_text(
        '#EXTM3U url-tvg="https://raw.githubusercontent.com/keramatai/vivatvsports/refs/heads/default/scripts/epg.xml"\n'
        + "\n".join(live_events),
        encoding="utf-8",
    )

    log.info(f"Events saved to {EVENTS_FILE.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

    for hndlr in log.handlers:
        hndlr.flush()
        hndlr.stream.write("\n")
