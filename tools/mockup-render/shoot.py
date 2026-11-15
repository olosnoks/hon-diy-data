import sys, asyncio, pathlib
from playwright.async_api import async_playwright
PAGES = pathlib.Path("pages"); OUT = pathlib.Path("out")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome",
            args=["--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader","--ignore-gpu-blocklist"])
        pg = await b.new_page(viewport={"width":1366,"height":768})
        errs=[]
        pg.on("pageerror", lambda e: errs.append(str(e)))
        for f in sorted(PAGES.glob("*.dc.html")):
            errs.clear()
            await pg.goto(f.resolve().as_uri())
            await pg.wait_for_timeout(4000)
            await pg.screenshot(path=str(OUT/(f.stem.replace(".dc","")+".png")))
            print(f.name, "errors:", errs[:2])
        await b.close()
asyncio.run(main())
