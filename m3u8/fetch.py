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
    tvf90,
    watchfooty,
    webcast,
    xyzstreams,
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

            hdl_brwsr = await network.browser(p)

            # 1. Define lists of the actual scraper modules
            pw_scraper_modules = [sportspass, watchfooty]
            httpx_scraper_modules = [
                fawa, flyembed, futbolx, istreameast, mainportal,
                reedstreams, streamcenter, streamfree, streamgate,
                tvf90, webcast, xyzstreams
            ]

            # 2. Create tasks dynamically based on the modules
            pw_tasks = [asyncio.create_task(m.scrape(hdl_brwsr)) for m in pw_scraper_modules]
            httpx_tasks = [asyncio.create_task(m.scrape()) for m in httpx_scraper_modules]

            # Wait for all tasks to complete, capturing exceptions
            pw_results = await asyncio.gather(*pw_tasks, return_exceptions=True)
            httpx_results = await asyncio.gather(*httpx_tasks, return_exceptions=True)

            # 3. Process results and build additions safely
            additions = {}
            
            for module, result in zip(pw_scraper_modules, pw_results):
                if isinstance(result, Exception):
                    log.error(f"Scraper {module.__name__} failed: {result}")
                else:
                    additions.update(module.urls)

            for module, result in zip(httpx_scraper_modules, httpx_results):
                if isinstance(result, Exception):
                    log.error(f"Scraper {module.__name__} failed: {result}")
                else:
                    additions.update(module.urls)

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
