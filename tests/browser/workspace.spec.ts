import { test, expect } from '@playwright/test';

test('prepare footage, curate selects, build and revise a cut, render and export', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'A little raw. A lot of possibility.' })).toBeVisible();
  await page.screenshot({ path: 'test-results/workspace-empty.png', fullPage: true });
  await page.getByRole('button', { name: 'Try a sample' }).click();
  await expect(page.locator('.clip-card')).toHaveCount(3);
  await expect(page.locator('.clip-card img').first()).toHaveJSProperty('naturalWidth', 640);

  await page.locator('.favorite-button').first().click();
  await expect(page.locator('.favorite-button.is-favorite')).toHaveCount(1);
  await page.locator('.clip-open').first().click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByLabel('Title', { exact: true }).fill('Morning desk details');
  await page.getByLabel('Tags', { exact: true }).fill('desk, morning, Zähne');
  await page.getByRole('button', { name: 'Save select' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByLabel('Search clips').fill('Zähne');
  await expect(page.locator('.clip-card')).toHaveCount(1);
  await page.getByRole('button', { name: 'Clear search' }).click();
  await expect(page.locator('.clip-card')).toHaveCount(3);
  await page.screenshot({ path: 'test-results/workspace-library.png', fullPage: true });

  for (const checkbox of await page.locator('.select-check').all()) await checkbox.click();
  await page.getByRole('button', { name: 'Build a rough cut' }).click();
  await page.getByLabel('Edit name', { exact: true }).fill('Morning study');
  await page.getByLabel('Creative direction', { exact: true }).fill('A calm visual study with warm tones.');
  await page.getByLabel('Target length · seconds').fill('6');
  await page.getByRole('combobox', { name: 'Pacing', exact: true }).selectOption('energetic');
  await page.getByLabel('Starting look').selectOption('warm');
  await page.getByRole('button', { name: 'Build rough cut', exact: true }).click();
  await expect(page.locator('.timeline-workspace')).toBeVisible();
  await expect(page.locator('.timeline-shot')).toHaveCount(3);
  await page.getByRole('button', { name: 'Move shot later' }).click();
  await page.getByLabel('Suggested look').selectOption('monochrome');
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect(page.locator('.timeline-heading')).toContainText('V2');
  await page.getByRole('button', { name: 'Render review' }).click();
  await expect(page.locator('.artifact').filter({ hasText: 'PREVIEW · v2' })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.locator('.monitor-bar')).toContainText('Rendered review');
  const video = page.locator('.review-monitor video');
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(1);
  const duration = await video.evaluate((v: HTMLVideoElement) => v.duration);
  expect(duration).toBeCloseTo(6, 1);
  await page.getByLabel('Export format').selectOption('resolve');
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  await expect(page.locator('.artifact').filter({ hasText: 'RESOLVE · v2' })).toBeVisible();
  await page.screenshot({ path: 'test-results/workspace-edit.png', fullPage: true });

  await page.getByText('Editorial notes & version history', { exact: true }).click();
  await page.getByLabel('Version history').selectOption('1');
  await page.getByRole('button', { name: 'Load as draft' }).click();
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect(page.locator('.timeline-heading')).toContainText('V3');
  await page.reload();
  await page
    .locator('nav')
    .getByRole('button', { name: /Rough cuts/ })
    .click();
  await expect(page.getByLabel('Edit name', { exact: true })).toHaveValue('Morning study');
  await expect(page.locator('.timeline-heading')).toContainText('V3');
  await page.locator('.sidebar-bottom').getByRole('button', { name: 'Settings' }).click();
  await page.getByLabel('Maximum select · seconds').fill('5');
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect(page.getByRole('status')).toContainText('Workspace settings saved');
  expect(errors).toEqual([]);
});

test('small viewport remains usable', async ({ page }) => {
  // Make the scenario independently runnable; duplicate sample imports are idempotent.
  const response = await page.request.post('/api/demo', { headers: { 'X-Vidac-Client': 'workspace' } });
  expect(response.ok()).toBeTruthy();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('.clip-card')).toHaveCount(3);
  const widths = await page.evaluate(() => ({
    content: document.documentElement.scrollWidth,
    screen: innerWidth,
  }));
  expect(widths.content).toBeLessThanOrEqual(widths.screen);
  await page.getByRole('button', { name: 'Import footage', exact: true }).first().click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Close dialog' }).click();
  await page.screenshot({ path: 'test-results/workspace-mobile.png', fullPage: true });
});

test('recover a moved original by searching a folder and its subfolders', async ({ page }) => {
  const { mkdir, rename } = await import('node:fs/promises');
  const { join, dirname } = await import('node:path');
  const headers = { 'X-Vidac-Client': 'workspace' };
  const demo = await (await page.request.post('/api/demo', { headers })).json();
  await expect
    .poll(async () => (await (await page.request.get(`/api/jobs/${demo.id}`)).json()).status)
    .toBe('succeeded');
  await page.goto('/');
  await expect(page.locator('.clip-card')).toHaveCount(3);
  const assets = await (await page.request.get('/api/assets')).json();
  const asset = assets[0];
  const clips = await (await page.request.get('/api/clips')).json();
  const edits = await (await page.request.get('/api/edits')).json();
  const root = join(dirname(asset.path), 'new-drive');
  const nested = join(root, 'shoot', 'day-one');
  await mkdir(nested, { recursive: true });
  const moved = join(nested, 'renamed-original.mp4');
  await rename(asset.path, moved);
  await page.reload();
  await page.getByRole('button', { name: 'Relink media · 1 missing', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('1 original missing');
  await page.getByLabel('Folder to search').fill(root);
  await page.screenshot({ path: 'test-results/workspace-relink.png', fullPage: true });
  await page.getByRole('button', { name: 'Search and relink', exact: true }).click();
  await expect(page.getByText('1 relinked · 0 still missing', { exact: true })).toBeVisible();
  const recovered = await (await page.request.get(`/api/assets/${asset.id}`)).json();
  expect(recovered.online).toBe(true);
  expect(recovered.path).toBe(moved);
  expect(await (await page.request.get('/api/clips')).json()).toEqual(clips);
  expect(await (await page.request.get('/api/edits')).json()).toEqual(edits);
  await page.getByText('Relink results', { exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
