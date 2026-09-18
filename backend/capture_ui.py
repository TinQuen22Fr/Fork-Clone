import asyncio
import os
import sys
from datetime import datetime
from playwright.async_api import async_playwright

BASE_URL = os.getenv("FORGE_URL", "https://forge.quentin-astro.fr")
OUTPUT_DIR = "/var/www/forge/screenshots"

async def capture(target_url: str = BASE_URL, filename: str = None):
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.png"
    
    filepath = os.path.join(OUTPUT_DIR, filename)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        # Émule un écran standard 1920x1080
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        
        print(f"Chargement de {target_url}...")
        try:
            await page.goto(target_url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            print(f"Avertissement (timeout/réseau) : {e}")
            await page.goto(target_url, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            
        await page.screenshot(path=filepath, full_page=True)
        await browser.close()
        
    print(f"Capture enregistrée : {filepath}")
    return filepath

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else BASE_URL
    asyncio.run(capture(url))
