import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

test('AHU 低风险修改：选择模板、自然语言改名、校验、导出检查', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByRole('heading', { name: '美控（KONG）智能体工作台' })).toBeVisible();
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
  const initialVersion = await currentVersionText(page);

  await page.getByPlaceholder('例如：我要做 AHU 程序，需要直膨机、排风机和 Modbus 通讯').fill(
    '把节点 1154c92 改名为 E2E-AHU比较节点'
  );
  await page.getByRole('button', { name: /发送/ }).click();

  await expect(page.getByText('重命名节点')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/补丁已应用并校验通过/)).toBeVisible();
  await expect(page.getByText('修改 1')).toBeVisible();
  await expect(page.locator('.ant-tag').filter({ hasText: /版本：v_/ })).not.toHaveText(initialVersion, { timeout: 60_000 });

  await page.getByRole('button', { name: /校验/ }).click();
  await expect(page.getByText('校验通过，可以导出。')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: /导出/ })).toBeEnabled();

  await page.getByRole('button', { name: '版本 / Trace / 反馈' }).click();
  await page.getByRole('tab', { name: '反馈' }).click();
  await page.getByPlaceholder('记录问题、期望结果或导出后的复核意见').fill('E2E 验收反馈：AHU 低风险修改通过。');
  await page.getByRole('button', { name: /提交反馈/ }).click();

  await expect(page.getByText('反馈已提交。')).toBeVisible({ timeout: 30_000 });
});

test('机房群控中风险修改：dry-run、确认、版本 diff、回滚', async ({ page }) => {
  await page.goto('/');

  await page.locator('[title="机房群控程序"]').click();
  await page.getByRole('button', { name: /新建/ }).click();
  await expect(page.getByText(/thread /)).toBeVisible();

  await page.getByPlaceholder('例如：我要做 AHU 程序，需要直膨机、排风机和 Modbus 通讯').fill(
    '我要做一个风冷热泵机房群控程序，包含水泵、旁通阀和防冻保护。'
  );
  await page.getByRole('button', { name: /发送/ }).click();

  const confirmButton = page.getByRole('button', { name: /确\s*认/ }).first();
  await expect(confirmButton).toBeEnabled({ timeout: 60_000 });
  await confirmButton.click();

  await expect(page.locator('.ant-tag').filter({ hasText: /版本：v_/ })).toBeVisible({ timeout: 60_000 });
  const originalVersion = await currentVersionText(page);

  await page.getByPlaceholder('例如：我要做 AHU 程序，需要直膨机、排风机和 Modbus 通讯').fill(
    '断开节点 2853d21 的输入 0'
  );
  await page.getByRole('button', { name: /发送/ }).click();

  await expect(page.getByText('断开连线').first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('确认后才会创建新版本；取消不会改变当前版本。')).toBeVisible();
  await expect(page.getByText('dry-run 通过')).toBeVisible();
  await expect(page.locator('.ant-tag').filter({ hasText: /版本：v_/ })).toHaveText(originalVersion);

  await page.getByRole('button', { name: '确认应用' }).click();
  await page.locator('.ant-popconfirm').getByRole('button', { name: '确认应用' }).click();
  await expect(page.locator('.ant-tag').filter({ hasText: /版本：v_/ })).not.toHaveText(originalVersion, { timeout: 60_000 });

  await page.getByRole('button', { name: '版本 / Trace / 反馈' }).click();
  await expect(page.getByText('版本对比')).toBeVisible();
  await expect(page.getByText('摘要：由结构化补丁创建。')).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: /eye\s*查看/ }).first().click();
  await expect(page.getByText(/新增 0/)).toBeVisible({ timeout: 30_000 });

  const rollbackButtons = page.getByRole('button', { name: '回滚' });
  await expect(rollbackButtons.nth(1)).toBeEnabled();
  await rollbackButtons.nth(1).click();
  await expect(page.getByText(/准备回滚到/)).toBeVisible();
  await page.getByRole('button', { name: '确认回滚' }).click();
  await expect(page.getByText('已回滚到目标版本。')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.ant-tag').filter({ hasText: /版本：v_/ })).toHaveText(originalVersion, { timeout: 30_000 });
});

async function currentVersionText(page: Page): Promise<string> {
  return (await page.locator('.ant-tag').filter({ hasText: /版本：v_/ }).first().textContent()) ?? '';
}
