#!/usr/bin/env python3
import asyncio
import re
from pathlib import Path

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
    watchfooty,
    webcast,
)
from scrapers.utils import get_logger, network

log = get_logger(Path(__file__).stem)

FILES_DIR = Path(__file__).parent
BASE_FILE = FILES_DIR / "vivatvsports.m3u8"
EVENTS_FILE = FILES_DIR / "events.m3u8"
COMBINED_FILE = FILES_DIR / "androidtv.m3u8"

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
        hdl_brwsr = None

        try:
            await network.setup_adblock()

            hdl_brwsr = await network.browser(p, "firefox")


            pw_tasks = [
                asyncio.create_task(sportspass.scrape(hdl_brwsr)),
                asyncio.create_task(watchfooty.scrape(hdl_brwsr)),
            ]

            httpx_tasks = [
                # asyncio.create_task(dami.scrape()),
                asyncio.create_task(embedsport.scrape()),
                asyncio.create_task(fawa.scrape()),
                asyncio.create_task(flyembed.scrape()),
                asyncio.create_task(futbolx.scrape()),
                asyncio.create_task(istreameast.scrape()),
                asyncio.create_task(mainportal.scrape()),
                # asyncio.create_task(ovostream.scrape()),
                # asyncio.create_task(pelotalibre.scrape()),
                asyncio.create_task(reedstreams.scrape()),
                asyncio.create_task(streamcenter.scrape()),
                asyncio.create_task(streamfree.scrape()),
                asyncio.create_task(streamgate.scrape()),
                # asyncio.create_task(streamtp.scrape()),
                # asyncio.create_task(streamxhd.scrape()),
                # asyncio.create_task(timstreams.scrape()),
                asyncio.create_task(webcast.scrape()),
            ]

            # Wait for all tasks to complete, capturing exceptions
            pw_results = await asyncio.gather(*pw_tasks, return_exceptions=True)
            httpx_results = await asyncio.gather(*httpx_tasks, return_exceptions=True)

            # Process results and log exceptions
            success = {}
            for (name, _), result in zip(pw_scrapers, pw_results):
                success[name] = not isinstance(result, Exception)
                if isinstance(result, Exception):
                    log.error(f"Scraper {name} failed: {result}")

            for (name, _), result in zip(httpx_scrapers, httpx_results):
                success[name] = not isinstance(result, Exception)
                if isinstance(result, Exception):
                    log.error(f"Scraper {name} failed: {result}")

            # Build additions from scrapers that succeeded
            additions = {}
            for name, scraper in pw_scrapers + httpx_scrapers:
                if success.get(name, False):
                    # Merge the urls
                    additions.update(scraper.urls)

            # Process the combined additions
            # Process the combined additions
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
                '#EXTM3U url-tvg="https://raw.githubusercontent.com/keramatai/vivatvsports/refs/heads/default/m3u8/tv_epg.xml"\n'
                + "\n".join(live_events),
                encoding="utf-8",
            )

            log.info(f"Events saved to {EVENTS_FILE.resolve()}")

        finally:
            if hdl_brwsr:
                await hdl_brwsr.close()
            await network.client.aclose()

if __name__ == "__main__":
    asyncio.run(main())

    for hndlr in log.handlers:
        hndlr.flush()
