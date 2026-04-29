import { expect, test } from '@playwright/test';

test('AHU 工程师主流程：新建会话、确认模板、校验、提交反馈', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByRole('heading', { name: '美的工程智能体工作台' })).toBeVisible();
  await expect(page.getByText(/请先在顶部选择/)).toBeVisible();

  await page.locator('[title="AHU 程序"]').click();
  await page.getByRole('button', { name: /新建/ }).click();
  await expect(page.getByText(/thread /)).toBeVisible();

  await page.getByPlaceholder('例如：我要做 AHU 程序，需要直膨机、排风机和 Modbus 通讯').fill(
    '我要做 AHU 程序，需要直膨机、排风机和 Modbus 通讯，并保留防冻保护。'
  );
  await page.getByRole('button', { name: /发送/ }).click();

  const confirmButton = page.getByRole('button', { name: /确\s*认/ }).first();
  await expect(confirmButton).toBeEnabled({ timeout: 60_000 });
  await expect(page.getByText('模板候选')).toBeVisible();
  await confirmButton.click();

  await expect(page.locator('.ant-tag').filter({ hasText: /版本：v_/ })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole('button', { name: /导出/ })).toBeEnabled();

  await page.getByRole('button', { name: /校验/ }).click();
  await expect(page.getByText('校验通过，可以导出。')).toBeVisible({ timeout: 30_000 });

  await page.getByRole('button', { name: '版本 / Trace / 反馈' }).click();
  await page.getByRole('tab', { name: '反馈' }).click();
  await page.getByPlaceholder('记录问题、期望结果或导出后的复核意见').fill('E2E 验收反馈：AHU 主流程通过。');
  await page.getByRole('button', { name: /提交反馈/ }).click();

  await expect(page.getByText('反馈已提交。')).toBeVisible({ timeout: 30_000 });
});
