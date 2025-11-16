# app/solver.py

import time
from urllib.parse import urlparse, urlunparse, urljoin
import io
import re

import httpx
import pandas as pd
from fastapi.concurrency import run_in_threadpool
from bs4 import BeautifulSoup

from .browser import fetch_rendered_html_sync

MAX_DURATION_SEC = 180  # 3 minutes per spec


# ------------------------------
# HTML Parsing Helpers
# ------------------------------

def detect_quiz_type(html: str) -> str:
    """Return quiz type label based on page content."""
    html_l = html.lower()
    if "demo-scrape-data" in html_l or "scrape" in html_l:
        return "scrape"
    if ".csv" in html_l or "audio" in html_l:
        return "audio"
    return "generic"


def build_submit_url(html: str, current_url: str) -> str:
    """
    Determine submit URL by attempting to read `.origin` span,
    otherwise revert to current URL origin.
    """
    soup = BeautifulSoup(html, "html.parser")
    origin_el = soup.select_one(".origin")

    if origin_el and origin_el.text.strip():
        origin = origin_el.text.strip()
    else:
        parsed = urlparse(current_url)
        origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))

    return origin.rstrip("/") + "/submit"


# ------------------------------
# Solver Modules
# ------------------------------

async def solve_scrape_question(html: str, current_url: str, client) -> str:
    """
    Solve scrape-based quiz:
    - follow /demo-scrape-data?email=...
    - extract the numeric secret code
    - return just the number as a string
    """
    print("   [scrape] Solving scrape-based question...")
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one("#question a")

    if not link or not link.get("href"):
        raise ValueError("Scrape target link not found")

    scrape_url = urljoin(current_url, link["href"])
    print(f"   [scrape] Data page URL: {scrape_url}")

    data_html = await run_in_threadpool(fetch_rendered_html_sync, scrape_url)
    data_text = BeautifulSoup(data_html, "html.parser").get_text()

    # Extract the first integer found in any non-empty line
    for line in data_text.splitlines():
        if not line.strip():
            continue
        m = re.search(r"\d+", line)
        if m:
            secret_code = m.group(0)  # e.g. "32000"
            print(f"   [scrape] Extracted secret code: {secret_code}")
            return secret_code

    raise ValueError("Unable to extract secret code")


async def solve_audio_csv_question(html: str, current_url: str, client) -> int:
    """
    Solve audio/CSV quiz:
    - find CSV link
    - download CSV
    - read cutoff from page
    - compute sum of values >= cutoff in the single data column
    """
    print("   [audio] Solving audio/CSV question (automatic with cutoff >=)...")
    soup = BeautifulSoup(html, "html.parser")

    # Find <a href="...csv"> by href
    csv_tag = soup.find("a", href=lambda h: h and h.endswith(".csv"))
    if not csv_tag:
        raise ValueError("CSV link not found")

    csv_url = urljoin(current_url, csv_tag["href"])
    print(f"   [audio] CSV URL: {csv_url}")

    # Download CSV
    resp = await client.get(csv_url)
    resp.raise_for_status()

    # IMPORTANT: no header row in file → header=None
    df = pd.read_csv(io.BytesIO(resp.content), header=None)
    print(f"   [audio] Data shape: {df.shape}")

    # Cutoff from page
    cutoff_el = soup.select_one("#cutoff")
    cutoff = int(cutoff_el.text.strip()) if cutoff_el else 0
    print(f"   [audio] Cutoff from page: {cutoff}")

    # Use the first (and only) column as numeric series
    series = pd.to_numeric(df.iloc[:, 0], errors="coerce")

    # Rule: sum of values >= cutoff
    mask = series >= cutoff
    result = series[mask].sum()

    print(f"   [audio] Sum of values >= {cutoff}: {result}")
    return int(result)




# ------------------------------
# Main Quiz Chain Engine
# ------------------------------

async def solve_quiz_chain(start_url: str, email: str, secret: str):
    """
    Main loop:
    - fetch page
    - detect quiz type
    - solve
    - submit
    - follow next URL if provided
    """
    start_time = time.time()
    current_url = start_url

    async with httpx.AsyncClient(timeout=30) as client:

        while current_url and time.time() - start_time < MAX_DURATION_SEC:
            print(f"\n🔎 Fetching quiz page: {current_url}")

            # Fetch question page HTML
            html = await run_in_threadpool(fetch_rendered_html_sync, current_url)

            # Short snippet for debugging
            snippet = html[:600].replace("\n", " ")
            print(f"   [html] Snippet: {snippet[:200]}{'...' if len(snippet) > 200 else ''}")

            # Build submit target
            submit_url = build_submit_url(html, current_url)
            print(f"   [meta] Submit URL: {submit_url}")

            # Determine solver
            quiz_type = detect_quiz_type(html)
            print(f"   [meta] Detected quiz type: {quiz_type}")

            # Helper for payload
            def make_payload(answer_value):
                return {
                    "email": email,
                    "secret": secret,
                    "url": current_url,
                    "answer": answer_value,
                }

            # SCRAPE TYPE
            if quiz_type == "scrape":
                answer = await solve_scrape_question(html, current_url, client)
                print(f"   [answer] Final answer (scrape): {answer}")

            # AUDIO/CSV TYPE
            elif quiz_type == "audio":
                answer = await solve_audio_csv_question(html, current_url, client)
                print(f"   [answer] Final answer (audio): {answer}")

            # GENERIC TYPE
            else:
                answer = "hello-from-agent"
                print("   [generic] Using default answer")
                print(f"   [answer] Final answer (generic): {answer}")

            # Submit response
            payload = make_payload(answer)
            print(f"   [submit] Payload: {payload}")
            response = await client.post(submit_url, json=payload)
            print(f"   [submit] Raw response: {response.text}")

            # Parse server reply
            try:
                result = response.json()
            except Exception as exc:
                print("   [error] Invalid JSON from submit")
                raise RuntimeError(f"Invalid JSON from submit: {response.text}") from exc

            print(f"   [result] Parsed response JSON: {result}")

            # Decide next URL
            next_url = result.get("url")
            if not next_url:
                print("🏁 No more URLs returned. Quiz chain finished.")
                return result

            print(f"➡️ Moving to next URL: {next_url}")
            current_url = next_url

        print("⏰ Time limit reached or URL missing. Ending quiz chain.")
        return {"correct": False, "reason": "timeout or missing url"}
