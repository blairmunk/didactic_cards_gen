"""Capture documentation screenshots from a running local application."""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path

from pyppeteer import launch


async def capture(
    base_url: str,
    deck_id: str,
    output_dir: Path,
    advanced_deck_id: str | None = None,
) -> None:
    executable = shutil.which("chromium") or shutil.which("chromium-browser")
    if not executable:
        raise RuntimeError("Chromium executable was not found")

    output_dir.mkdir(parents=True, exist_ok=True)
    browser = await launch(
        executablePath=executable,
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    try:
        page = await browser.newPage()
        await page.setViewport({"width": 1440, "height": 1000, "deviceScaleFactor": 1})

        await page.goto(f"{base_url}/", {"waitUntil": "domcontentloaded"})
        await page.screenshot({"path": str(output_dir / "decks.png"), "fullPage": True})

        await page.goto(f"{base_url}/deck/{deck_id}", {"waitUntil": "domcontentloaded"})
        await page.evaluate(
            """() => {
                const sections = document.querySelectorAll(
                    'details.typography-settings'
                );
                if (sections[1]) sections[1].open = true;
                if (sections[2]) sections[2].open = true;
            }"""
        )
        await page.screenshot({"path": str(output_dir / "deck-editor.png"), "fullPage": True})

        if advanced_deck_id is not None:
            await page.goto(
                f"{base_url}/deck/{advanced_deck_id}",
                {"waitUntil": "domcontentloaded"},
            )
            await page.screenshot({
                "path": str(output_dir / "advanced-deck-editor.png"),
                "fullPage": True,
            })
            await page.goto(
                f"{base_url}/deck/{advanced_deck_id}/advanced",
                {"waitUntil": "domcontentloaded"},
            )
            await page.screenshot({
                "path": str(output_dir / "trusted-latex.png"),
                "fullPage": True,
            })
            await page.goto(
                f"{base_url}/deck/{deck_id}",
                {"waitUntil": "domcontentloaded"},
            )

        first_card_id = await page.Jeval(
            "#cards-tbody tr:first-child",
            "element => element.dataset.cardId",
        )

        await page.click("#btn-view-preview")
        await asyncio.sleep(1)
        preview = await page.querySelector("#view-preview")
        await preview.screenshot({"path": str(output_dir / "card-preview.png")})

        await page.goto(
            f"{base_url}/deck/{deck_id}/edit_card/{first_card_id}",
            {"waitUntil": "domcontentloaded"},
        )
        await asyncio.sleep(1)
        await page.screenshot({"path": str(output_dir / "edit-card.png"), "fullPage": True})
        await page.goto(
            f"{base_url}/printer_profiles", {"waitUntil": "domcontentloaded"}
        )
        await page.select("#calculation-profile", "standard-short-edge")
        await page.type("#measured-x", "1.2")
        await page.type("#measured-y", "-0.4")
        await asyncio.gather(
            page.waitForNavigation({"waitUntil": "domcontentloaded"}),
            page.click('.calibration-calculator button[type="submit"]'),
        )
        await page.screenshot({
            "path": str(output_dir / "printer-profiles.png"), "fullPage": True
        })
    finally:
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    parser.add_argument("--deck-id", required=True)
    parser.add_argument("--advanced-deck-id")
    parser.add_argument("--output-dir", type=Path, default=Path("docs/images"))
    args = parser.parse_args()
    asyncio.run(capture(
        args.base_url.rstrip("/"),
        args.deck_id,
        args.output_dir,
        args.advanced_deck_id,
    ))


if __name__ == "__main__":
    main()
