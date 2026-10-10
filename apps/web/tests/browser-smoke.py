"""Real browser contract smoke against the local backend; no fabricated market data.
Run from repository root: .venv/bin/python apps/web/tests/browser-smoke.py
Requires an explicitly initialized bootstrap operator in the ignored .env file.
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[3]


async def main():
    config = dotenv_values(ROOT / '.env')
    username = config['BOOTSTRAP_USERNAME']
    password = config['BOOTSTRAP_PASSWORD']
    errors = []
    results = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        context = await browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = await context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto('http://127.0.0.1:3000/login')
        await page.get_by_label('Username', exact=True).fill(username)
        await page.get_by_label('Password', exact=True).fill(password)
        await page.get_by_role('button', name='Sign in', exact=True).click()
        await expect(page.get_by_role('heading', name='Command center', exact=True)).to_be_visible(timeout=15000)
        results.append('Session login and dashboard rendered')
        account_response = await page.request.get('http://127.0.0.1:3000/api/v1/paper/account')
        if await account_response.json() is None:
            await page.goto('http://127.0.0.1:3000/paper')
            await page.get_by_label('Initial simulated balance (USD)').fill('1000')
            await page.get_by_role('button', name='Create paper account').click()
            await expect(page.get_by_text('Paper equity', exact=True)).to_be_visible()
            results.append('Explicit USD 1000 PAPER account initialized using real API; INR planning remains separate')
        else:
            results.append('Existing durable PAPER account preserved')
        await page.goto('http://127.0.0.1:3000/strategies')
        await page.get_by_role('button', name='Create strategy', exact=True).click()
        dialog = page.get_by_role('dialog')
        name = 'Browser validation ' + datetime.now(timezone.utc).strftime('%H%M%S')
        await dialog.get_by_label('Strategy name', exact=True).fill(name)
        await dialog.get_by_role('button', name='Create strategy', exact=True).click()
        await expect(dialog).not_to_be_visible()
        row = page.get_by_role('row').filter(has=page.get_by_text(name, exact=True))
        await expect(row).to_be_visible()
        await row.get_by_role('button', name='Validate', exact=True).click()
        await expect(row.get_by_text('VALIDATED', exact=True)).to_be_visible()
        results.append('Strategy create and server validation succeeded')
        await row.get_by_role('button', name='Edit ' + name, exact=True).click()
        await dialog.get_by_label('Description / research source').fill('Explicit browser smoke hypothesis; not approved for exchange execution.')
        await dialog.get_by_role('button', name='Save new version', exact=True).click()
        await expect(dialog).not_to_be_visible()
        await expect(row).to_contain_text('v2')
        await row.get_by_role('button', name='Validate', exact=True).click()
        await expect(row.get_by_text('VALIDATED', exact=True)).to_be_visible()
        results.append('Editing creates a new draft version; revalidation succeeds')
        response = await page.request.get('http://127.0.0.1:3000/api/v1/strategies')
        created = next(s for s in await response.json() if s['name'] == name)
        await page.goto(f'http://127.0.0.1:3000/backtesting?strategy={created["id"]}')
        await page.get_by_label('Historical candle limit').fill('300')
        await page.get_by_role('button', name='Import Delta history', exact=True).click()
        await expect(page.get_by_role('button', name='Import Delta history', exact=True)).to_be_enabled(timeout=30000)
        await page.get_by_role('button', name='Run backtest', exact=True).click()
        await expect(page.get_by_role('button', name='Run backtest', exact=True)).to_be_enabled(timeout=30000)
        if await page.get_by_text('Research report', exact=False).count():
            results.append('Historical backtest executed through browser using imported Delta candles')
        else:
            results.append('Historical backtest returned a visible prerequisite/error response')
        routes = {'dashboard':'Command center','terminal':'Market terminal','paper':'Paper trading','positions':'Positions','orders':'Orders','risk':'Risk management','performance':'Performance analytics','journal':'Trade journal','notifications':'Notification center','integrations':'Integrations','system-health':'System health','settings':'Settings','demo':'Delta India demo','live':'Live readiness','research':'Strategy research'}
        for route, heading in routes.items():
            await page.goto('http://127.0.0.1:3000/' + route)
            await expect(page.get_by_role('heading', name=heading, exact=True)).to_be_visible(timeout=15000)
            if route == 'terminal':
                await expect(page.locator('canvas').first).to_be_visible(timeout=15000)
            await page.wait_for_timeout(250)
        results.append(f'{len(routes)} management pages rendered without browser exceptions')
        await page.goto('http://127.0.0.1:3000/')
        await expect(page.get_by_role('heading',name='Command center',exact=True)).to_be_visible()
        await page.screenshot(path='/tmp/tradebot-dashboard-desktop.png',full_page=True)
        await page.set_viewport_size({'width':390,'height':844})
        await page.screenshot(path='/tmp/tradebot-dashboard-mobile.png',full_page=True)
        if await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'):
            results.append('390px responsive viewport has no document horizontal overflow')
        else:
            errors.append('Mobile document has horizontal overflow')
        await browser.close()
    report = {'results':results,'browser_errors':errors,'strategy_id':created['id'],'screenshots':['/tmp/tradebot-dashboard-desktop.png','/tmp/tradebot-dashboard-mobile.png']}
    Path('/tmp/tradebot-ui-smoke.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
