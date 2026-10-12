"""Activate observed native choices and verify them, including covered inputs."""
import json
from playwright.async_api import Error as PlaywrightError, expect
from app.models.flow import UserAction


async def ensure_checked(locator, *, focus=None):
    try:
        await expect(locator).to_be_enabled(timeout=15000)
        if await locator.is_checked():return
        # Apple styles the label over the native input. Clicking that visible
        # label works without relying on keyboard focus in a hidden Qt view.
        identifier = await locator.get_attribute('id')
        label = locator.page.locator('label[for='+json.dumps(identifier)+']') if identifier else locator.locator('xpath=ancestor::label[1]')
        if not await label.count():label = locator.locator('xpath=ancestor::label[1]')
        try:
            if await label.count() == 1 and await label.is_visible():
                await label.click(timeout=3000)
            else:await locator.check(timeout=3000)
            await expect(locator).to_be_checked(timeout=750)
            return
        except (AssertionError, PlaywrightError):
            if await locator.is_checked():return
        if focus:await focus(locator.page)
        await locator.press('Space', timeout=3000)
        await expect(locator).to_be_checked(timeout=3000)
    except (AssertionError, PlaywrightError):
        raise UserAction('官网选项尚未选中或暂不可用，请在浏览器核对后继续；已保留当前页面') from None
