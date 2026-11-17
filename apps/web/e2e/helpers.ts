import { expect, type Locator, type Page } from '@playwright/test';

/** Top-level routes, in navigation order: link label and the page's level-2 heading. */
export const ROUTES = [
  { label: 'Home', path: '/', heading: 'Home' },
  { label: 'Games', path: '/games', heading: 'Games' },
  { label: 'Decks', path: '/decks', heading: 'Decks' },
  { label: 'Stats', path: '/stats', heading: 'Stats' },
  { label: 'Collection', path: '/collection', heading: 'Collection' },
  { label: 'Leagues', path: '/leagues', heading: 'Leagues' },
  { label: 'Account', path: '/account', heading: 'Account' },
] as const;

/** Viewport widths from S-1.8.5 AC5. */
export const VIEWPORTS = [360, 768, 1024] as const;

/** Below this width the navigation is a compact menu behind a toggle button. */
export const COMPACT_BELOW = 768;
/** At or above this width the navigation is a sidebar. */
export const SIDEBAR_FROM = 1024;

export function credentials(): { username: string; password: string } {
  const username = process.env.E2E_USERNAME;
  const password = process.env.E2E_PASSWORD;
  if (!username || !password) {
    throw new Error('E2E_USERNAME and E2E_PASSWORD must be set (see apps/web/e2e/README.md).');
  }
  return { username, password };
}

export function primaryNav(page: Page): Locator {
  return page.getByRole('navigation', { name: 'Primary' });
}

export function menuToggle(page: Page): Locator {
  return page.getByRole('button', { name: 'Menu', exact: true });
}

export function navLink(page: Page, label: string): Locator {
  return primaryNav(page).getByRole('link', { name: label, exact: true });
}

/** Whether the viewport is narrow enough for the compact (toggle) menu. */
export function isCompact(page: Page): boolean {
  const width = page.viewportSize()?.width ?? 0;
  return width < COMPACT_BELOW;
}

/** Fills the sign-in form and submits it. Assumes the sign-in page is showing. */
export async function submitSignIn(page: Page): Promise<void> {
  const { username, password } = credentials();
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

/**
 * Signs in through the UI and waits for Home. Every test gets a fresh browser context, so refresh
 * cookies never get reused across tests (refresh tokens rotate).
 */
export async function signIn(page: Page): Promise<void> {
  await page.goto('/');
  // The app deliberately keeps the next path's `/` literal.
  await expect(page).toHaveURL(/\/sign-in\?next=\/$/);
  await submitSignIn(page);
  await expect(page.getByRole('heading', { level: 2, name: 'Home' })).toBeVisible();
}

/** Opens the compact menu when the viewport needs it. */
export async function openNavIfCompact(page: Page): Promise<void> {
  if (!isCompact(page)) return;
  const toggle = menuToggle(page);
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
  await expect(primaryNav(page)).toBeVisible();
}

/** Goes to a top-level route by clicking its navigation link (opening the compact menu first). */
export async function gotoViaNav(page: Page, label: string): Promise<void> {
  await openNavIfCompact(page);
  await navLink(page, label).click();
}
