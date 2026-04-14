import discord
import aiohttp
import asyncio
import time
import json
import os
import requests
import difflib
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime

# ===== CONFIG =====
TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
CHANGE_CHANNEL_ID = int(os.environ["CHANGE_CHANNEL_ID"])
BASE_URL = os.environ["BASE_URL"]
LOGO_URL = os.environ.get("LOGO_URL", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 60))
DATA_PATH = os.environ.get("DATA_PATH", "uptime.json")

# ===== GLOBAL =====
visited = set()
urls = set()
message_id = None
last_status = {}
last_content = {}

# ===== SMART CRAWLER =====
def normalize(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

def crawl(url):
    if url in visited:
        return
    visited.add(url)

    try:
        r = requests.get(url, timeout=5)
        soup = BeautifulSoup(r.text, "html.parser")

        for link in soup.find_all("a", href=True):
            full = urljoin(BASE_URL, link["href"])
            clean = normalize(full)

            if BASE_URL in clean and clean not in urls:
                urls.add(clean)
                crawl(clean)
    except:
        pass

# ===== MONITOR =====
async def check_url(session, url):
    start = time.time()
    try:
        async with session.get(url, timeout=5) as r:
            text = await r.text()
            return url, True, int((time.time() - start) * 1000), text
    except:
        return url, False, None, None

async def check_all():
    async with aiohttp.ClientSession() as session:
        tasks = [check_url(session, u) for u in urls]
        return await asyncio.gather(*tasks)

# ===== CHANGE DETECTION =====
def get_changes(old, new):
    if not old or not new:
        return None

    diff = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        lineterm=""
    )

    changes = "\n".join(diff)
    return changes[:1500] if changes else None

# ===== STORAGE =====
def load_history():
    try:
        with open(DATA_PATH) as f:
            return json.load(f)
    except:
        return {}

def save_history(data):
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True) if os.path.dirname(DATA_PATH) else None
    with open(DATA_PATH, "w") as f:
        json.dump(data, f, indent=2)

# ===== DISCORD UI =====
def build_embed(results, history):
    up = sum(1 for _, ok, _, _ in results if ok)
    total = len(results)

    color = 0x00ff88 if up == total else 0xff4444 if up == 0 else 0xffcc00

    embed = discord.Embed(
        title="🌐 Website Status",
        color=color
    )

    lines = []
    for url, ok, ms, _ in results[:10]:
        emoji = "🟢" if ok else "🔴"
        name = url.replace(BASE_URL, "")
        lines.append(f"{emoji} │ {name or '/'} ({ms if ms else 'timeout'}ms)")

    embed.add_field(name="Pages", value="\n".join(lines), inline=False)

    uptime = (up / total) * 100 if total else 0
    embed.add_field(
        name="Stats",
        value=f"Uptime: {uptime:.2f}%\n{up}/{total} online",
        inline=False
    )

    embed.set_footer(text=f"Last updated: {datetime.now().strftime('%H:%M:%S')}")
    if LOGO_URL:
        embed.set_thumbnail(url=LOGO_URL)

    return embed

# ===== ALERTS =====
async def send_alert(channel, down_urls):
    if down_urls:
        msg = "🚨 **Sider nede:**\n"
        for u in down_urls:
            msg += f"🔴 {u}\n"
        await channel.send(msg)

async def send_change(channel, url, changes):
    embed = discord.Embed(
        title="📝 Ændring fundet",
        description=f"**Side:** {url}",
        color=0x3498db
    )

    embed.add_field(
        name="Ændringer",
        value=f"```diff\n{changes}\n```" if changes else "Mindre ændring",
        inline=False
    )

    embed.set_footer(text="Content change detected")

    await channel.send(embed=embed)

# ===== DISCORD BOT =====
intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    global message_id, last_status, last_content

    print("Bot online")
    channel = client.get_channel(CHANNEL_ID)
    change_channel = client.get_channel(CHANGE_CHANNEL_ID)

    print("Crawler hjemmeside...")
    crawl(BASE_URL)
    print(f"Fundet {len(urls)} sider")

    history = load_history()

    while True:
        results = await check_all()

        down_now = []
        new_status = {}

        for url, ok, ms, content in results:
            new_status[url] = ok

            # downtime
            if url in last_status and last_status[url] and not ok:
                down_now.append(url)

            # ===== CHANGE DETECTION =====
            if ok and content:
                if url in last_content:
                    changes = get_changes(last_content[url], content)
                    if changes:
                        await send_change(change_channel, url, changes)

                last_content[url] = content

            # ===== UPTIME =====
            if url not in history:
                history[url] = {"up": 0, "total": 0}

            history[url]["total"] += 1
            if ok:
                history[url]["up"] += 1

        save_history(history)

        if down_now:
            await send_alert(channel, down_now)

        embed = build_embed(results, history)

        if message_id is None:
            msg = await channel.send(embed=embed)
            message_id = msg.id
        else:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed)

        last_status = new_status

        await asyncio.sleep(CHECK_INTERVAL)

client.run(TOKEN)
