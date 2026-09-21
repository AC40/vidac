import { test, expect } from '@playwright/test';

test('choose independent AI providers and models, persist settings, and clear incompatible models', async ({
  page,
}) => {
  page.setDefaultTimeout(20_000);
  const headers = { 'X-Vidac-Client': 'workspace' };
  const original = await (await page.request.get('/api/settings')).json();
  await page.route('**/api/models?*', async (route) => {
    const provider = new URL(route.request().url()).searchParams.get('provider');
    await route.fulfill({
      json: {
        provider,
        connected: true,
        models: [`${provider}/example`],
        detail: `${provider} found. Login and model access are checked when a job runs.`,
      },
    });
  });
  try {
    await page.goto('/');
    await page.locator('.sidebar-bottom').getByRole('button', { name: 'Settings' }).click();
    await page.getByRole('combobox', { name: 'Planning provider', exact: true }).selectOption('codex');
    await page.getByLabel('Planning model', { exact: true }).fill('chosen-codex-model');
    await page.getByRole('combobox', { name: 'Vision provider', exact: true }).selectOption('opencode');
    await page.getByLabel('Vision model', { exact: true }).fill('provider/vision-model');
    await page.getByRole('checkbox', { name: /Allow CLI provider data sharing/ }).check();
    await page.getByLabel('Model timeout · seconds').fill('120');
    await page.getByRole('button', { name: 'Save settings' }).click();
    await expect(page.getByRole('status')).toContainText('Workspace settings saved');
    await page.reload();
    await page.locator('.sidebar-bottom').getByRole('button', { name: 'Settings' }).click();
    await expect(page.getByRole('combobox', { name: 'Planning provider', exact: true })).toHaveValue('codex');
    await expect(page.getByLabel('Planning model', { exact: true })).toHaveValue('chosen-codex-model');
    await expect(page.getByLabel('Vision model', { exact: true })).toHaveValue('provider/vision-model');
    await expect(page.getByLabel('Model timeout · seconds')).toHaveValue('120');
    await expect(page.getByText(/Using T3 Code/)).toBeVisible();
    await page.screenshot({ path: 'test-results/settings-providers.png', fullPage: true });
    await page.getByRole('combobox', { name: 'Planning provider', exact: true }).selectOption('claude');
    await expect(page.getByLabel('Planning model', { exact: true })).toHaveValue('');
    await expect(page.getByLabel('Vision model', { exact: true })).toHaveValue('provider/vision-model');
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole('combobox', { name: 'Planning provider', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.screenshot({ path: 'test-results/settings-providers-mobile.png', fullPage: true });
  } finally {
    await page.request.put('/api/settings', { headers, data: original });
  }
});
