#!/usr/bin/env python3
import asyncio
import gzip
from pathlib import Path
from xml.etree import ElementTree as ET

from scrapers.utils import get_logger, network

log = get_logger(Path(__file__).stem)

OUTPUT_FILE = Path(__file__).parent / "epg-extra.xml"

EPG_URLS = [
    f"https://epgshare01.online/epgshare01/epg_ripper_{EPG_ID}.xml.gz"
    for EPG_ID in [
        "PLEX1",
        "US2",
    ]
]

CHANNELS = {
    "plex.tv.Women`s.Sports.Network.plex": None,  # Set a custom logo URL here if needed, or leave as None
    "Fox.Sports.4K.us2": None,
    "FS1.HD.us2": None,
    "FS2.HD.us2": None,
    "CBS.Sports.Golazo.Network.us2": None,
    "plex.tv.Fubo.Sports.Network.plex": "https://epg.iptvx.one/picons/fubo-sports-network-us.png",
}


async def fetch_xml(url: str) -> ET.Element | None:
    try:
        xml_data = await network.request(url, log=log)
        if not xml_data or not hasattr(xml_data, "content"):
            log.error(f'Empty or invalid response from "{url}"')
            return None

        log.info(f'Parsing XML from "{url}"')
        data = gzip.decompress(xml_data.content)
        return ET.fromstring(data)
    except Exception as e:
        log.error(f'Failed to process XML from "{url}": {e}')
        return None


async def main() -> None:
    log.info(f"{'=' * 10} Fetching Extra EPG {'=' * 10}")

    parsed_channel_ids: set[str] = set()
    root = ET.Element("tv")

    epgs = await asyncio.gather(*(fetch_xml(url) for url in EPG_URLS))

    for epg_data in (epg for epg in epgs if epg is not None):
        # Filter and append channels
        for channel in epg_data.findall("channel"):
            channel_id = channel.get("id")
            if channel_id not in CHANNELS:
                continue

            parsed_channel_ids.add(channel_id)

            # Override icon if a custom logo URL is specified in CHANNELS
            if custom_logo := CHANNELS.get(channel_id):
                icon_tag = channel.find("icon")
                if icon_tag is None:
                    icon_tag = ET.SubElement(channel, "icon")
                icon_tag.set("src", custom_logo)

            if (url_tag := channel.find("url")) is not None:
                channel.remove(url_tag)

            root.append(channel)

        # Filter and append programmes
        for program in epg_data.findall("programme"):
            prog_channel = program.get("channel")
            if prog_channel not in CHANNELS:
                continue

            title_elem = program.find("title")
            title_text = title_elem.text if (title_elem is not None and title_elem.text) else ""

            subtitle = program.find("sub-title")

            if (
                title_text in ["NHL Hockey", "Live: NFL Football"]
                and subtitle is not None
                and subtitle.text
            ):
                title_elem.text = f"{title_text} {subtitle.text}"

            root.append(program)

    # Log any requested channels that were not found in the XML sources
    if missing_ids := CHANNELS.keys() - parsed_channel_ids:
        log.warning(f"Missed {len(missing_ids)} requested channel ID(s)")
        for channel_id in missing_ids:
            log.warning(f"Missing: {channel_id}")

    tree = ET.ElementTree(root)
    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    log.info(f"EPG saved to {OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

    try:
        asyncio.run(network.client.aclose())
    except Exception:
        pass