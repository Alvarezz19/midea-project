import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const screenshotDir = path.join('test-results', 'visual');

test('桌面端 1440px 工作台首屏截图验收', async ({ page }) => {
  mkdirSync(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 920 });
  await page.goto('/');

  await expect(page.getByRole('heading', { name: '美控（KONG）智能体工作台' })).toBeVisible();
  await expect(page.getByText('会话与需求')).toBeVisible();
  await expect(page.getByText('模板、计划与风险')).toBeVisible();
  await expect(page.getByText('局部流程图与校验')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: path.join(screenshotDir, 'workbench-desktop-1440.png'), fullPage: true });
});

test('移动端 390px 工作台状态与确认入口截图验收', async ({ page }) => {
  mkdirSync(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');

  await expect(page.getByRole('heading', { name: '美控（KONG）智能体工作台' })).toBeVisible();
  await expect(page.getByText(/请先在顶部选择/)).toBeVisible();
  await expect(page.getByText('会话与需求')).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: path.join(screenshotDir, 'workbench-mobile-390.png'), fullPage: true });
});

async function expectNoHorizontalOverflow(page: import('@playwright/test').Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(4);
}
