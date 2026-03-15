from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, date, time, timedelta
import re

import httpx
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

PCC_URL = "https://princecharlescinema.com/next-7-days/"
PCC_BASE = "https://princecharlescinema.com"


def load_token() -> str:
    if token := os.environ.get("TELEGRAM_BOT_TOKEN"):
        return token
    return Path(".token").read_text().strip()


async def hello(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Hello World")


def _parse_date(text: str) -> date | None:
    """Extract a date from text like 'Wednesday 11 Mar 2026'."""
    m = re.search(r"(\d{1,2})\s+(\w{3})\s+(\d{4})", text)
    if m:
        try:
            return datetime.strptime(m.group(0), "%d %b %Y").date()
        except ValueError:
            pass
    return None


def _parse_time(text: str) -> time | None:
    """Parse '11:30 am' / '8:45 pm' style strings."""
    text = text.strip().lower()
    text = re.sub(r"(?<=\d)(am|pm)$", r" \1", text)
    try:
        return datetime.strptime(text, "%I:%M %p").time()
    except ValueError:
        return None


async def fetch_screenings() -> list[dict]:
    """Scrape PCC next-7-days page and return a list of screening dicts."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        resp = await client.get(
            PCC_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; bot)"}
        )
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    screenings = []

    for day_div in soup.select("div.next-7-days-list div.day"):
        h4 = day_div.find("h4")
        if not h4:
            continue
        heading = h4.get_text(strip=True)
        if heading.lower() == "today":
            screening_date = date.today()
        else:
            screening_date = _parse_date(heading)
        if not screening_date:
            continue

        for perf in day_div.select("div.performance-dayslist"):
            title_link = perf.select_one("div.leftsideperf a")
            book_link = perf.select_one("a.film_book_button")
            time_span = perf.select_one("a.film_book_button span.time")

            if not (book_link and time_span):
                continue

            time_obj = _parse_time(time_span.get_text(strip=True))
            if time_obj is None:
                continue

            href = book_link.get("href", "")
            booking_url = href if href.startswith("http") else PCC_BASE + href
            film_title = title_link.get_text(strip=True) if title_link else "Unknown"

            screenings.append(
                {
                    "date": screening_date,
                    "time": time_obj,
                    "title": film_title,
                    "booking_url": booking_url,
                }
            )

    return screenings


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5  # Mon–Fri


def _weekend_dates() -> list[date]:
    """Return the Saturday and Sunday of the upcoming (or current) weekend.

    Mon–Fri  → upcoming Sat + Sun
    Saturday → today + tomorrow
    Sunday   → yesterday (Sat) + today; yesterday won't appear in scraped data
               but is included so the heading makes sense
    """
    today = date.today()
    weekday = today.weekday()  # Mon=0 … Sat=5, Sun=6
    if weekday <= 4:
        saturday = today + timedelta(days=5 - weekday)
    elif weekday == 5:
        saturday = today
    else:  # Sunday
        saturday = today - timedelta(days=1)
    return [saturday, saturday + timedelta(days=1)]


def _format_message(screenings: list[dict], heading: str) -> str:
    if not screenings:
        return f"<b>{heading}</b>\n\nNo screenings found."

    by_date: dict[date, list] = {}
    for s in screenings:
        by_date.setdefault(s["date"], []).append(s)

    lines = [f"<b>{heading}</b>"]
    for d in sorted(by_date):
        lines.append(f"\n<b>{d.strftime('%A %-d %b')}</b>")
        for s in sorted(by_date[d], key=lambda x: x["time"]):
            time_str = datetime.combine(d, s["time"]).strftime("%-I:%M %p")
            lines.append(
                f'  {time_str} — <a href="{s["booking_url"]}">{s["title"]}</a>'
            )

    return "\n".join(lines)


async def _send_reply(update: Update, text: str) -> None:
    """Send a potentially long HTML message, splitting at Telegram's 4096-char limit."""
    kwargs = {"parse_mode": "HTML", "disable_web_page_preview": True}
    if len(text) <= 4096:
        await update.message.reply_text(text, **kwargs)
        return
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > 4096:
            await update.message.reply_text(chunk, **kwargs)
            chunk = line + "\n"
        else:
            chunk += line + "\n"
    if chunk.strip():
        await update.message.reply_text(chunk, **kwargs)


async def _kino_single_day(update: Update, d: date) -> None:
    all_screenings = await fetch_screenings()
    screenings = [
        s
        for s in all_screenings
        if s["date"] == d and (not _is_weekday(d) or s["time"].hour >= 18)
    ]
    msg = _format_message(screenings, f"PCC — {d.strftime('%A %-d %b')}")
    await _send_reply(update, msg)


async def kino_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _kino_single_day(update, date.today())


async def kino_next_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _kino_single_day(update, date.today() + timedelta(days=1))


async def kino_next_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = date.today()
    week_end = today + timedelta(days=7)
    all_screenings = await fetch_screenings()
    screenings = [
        s
        for s in all_screenings
        if today < s["date"] <= week_end
        and (not _is_weekday(s["date"]) or s["time"].hour >= 18)
    ]
    msg = _format_message(screenings, "PCC — Next 7 Days")
    await _send_reply(update, msg)


async def kino_next_weekend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    weekend = _weekend_dates()
    saturday, sunday = weekend[0], weekend[1]
    all_screenings = await fetch_screenings()
    screenings = [s for s in all_screenings if s["date"] in (saturday, sunday)]
    heading = f"PCC — Weekend {saturday.strftime('%-d %b')}–{sunday.strftime('%-d %b')}"
    msg = _format_message(screenings, heading)
    await _send_reply(update, msg)


def main() -> None:
    app = ApplicationBuilder().token(load_token()).build()
    app.add_handler(CommandHandler("hello", hello))
    app.add_handler(CommandHandler("kino_today", kino_today))
    app.add_handler(CommandHandler("kino_next_day", kino_next_day))
    app.add_handler(CommandHandler("kino_next_week", kino_next_week))
    app.add_handler(CommandHandler("kino_next_weekend", kino_next_weekend))
    app.run_polling()


if __name__ == "__main__":
    main()
