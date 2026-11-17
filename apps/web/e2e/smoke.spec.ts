import { expect, test } from '@playwright/test';
import {
  COMPACT_BELOW,
  ROUTES,
  SIDEBAR_FROM,
  VIEWPORTS,
  gotoViaNav,
  menuToggle,
  navLink,
  openNavIfCompact,
  primaryNav,
  signIn,
  submitSignIn,
} from './helpers';

const HEIGHT = 800;

test('signs in with username and password and lands on Home', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveURL(/\/sign-in\?next=\/$/);
  await expect(page.getByRole('heading', { level: 2, name: 'Sign in' })).toBeVisible();

  await submitSignIn(page);

  // The full URL, so `/sign-in?next=/` cannot match.
  await expect(page).toHaveURL(/^http:\/\/localhost:5173\/$/);
  await expect(page.getByRole('heading', { level: 2, name: 'Home' })).toBeVisible();
});

test('Escape in the sign-out-everywhere dialog returns focus to the account menu', async ({
  page,
}) => {
  await signIn(page);
  await openNavIfCompact(page);
  await page.getByRole('button', { name: 'Account menu' }).click();
  await page.getByRole('button', { name: 'Sign out everywhere' }).click();
  await expect(page.getByRole('dialog', { name: 'Sign out everywhere?' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(page.getByRole('button', { name: 'Account menu' })).toBeFocused();
});

test('signs out and returns to the sign-in page', async ({ page }) => {
  await signIn(page);
  await openNavIfCompact(page);
  await page.getByRole('button', { name: 'Account menu' }).click();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page).toHaveURL(/\/sign-in/);
  // The session is gone: a protected route sends us back to sign-in.
  await page.goto('/games');
  await expect(page).toHaveURL(/\/sign-in/);
});

for (const width of VIEWPORTS) {
  test.describe(`at ${String(width)}px wide`, () => {
    test.use({ viewport: { width, height: HEIGHT } });

    test.beforeEach(async ({ page }) => {
      await signIn(page);
    });

    for (const route of ROUTES) {
      test(`opens ${route.label} from the navigation with the footer notices and no horizontal scroll`, async ({
        page,
      }) => {
        await gotoViaNav(page, route.label);

        await expect(page).toHaveURL(new RegExp(`^http://localhost:5173${route.path}$`));
        // The compact menu closes after a click; reopen it, since role queries skip hidden links.
        await openNavIfCompact(page);
        await expect(navLink(page, route.label)).toHaveAttribute('aria-current', 'page');
        await expect(page.getByRole('heading', { level: 2, name: route.heading })).toBeVisible();

        // Footer notices (S-1.6.6): the Wizards Fan Content Policy notice and the Scryfall credit.
        const footer = page.getByRole('contentinfo');
        await expect(footer.getByText(/unofficial Fan Content permitted under the/)).toBeVisible();
        await expect(footer.getByRole('link', { name: 'Fan Content Policy' })).toBeVisible();
        await expect(footer.getByText(/Not approved\/endorsed by Wizards/)).toBeVisible();
        await expect(footer.getByText(/Card data and images courtesy of/)).toBeVisible();
        await expect(footer.getByRole('link', { name: 'Scryfall' })).toBeVisible();

        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow).toBeLessThanOrEqual(0);
      });
    }

    test('lays the navigation out for this width', async ({ page }) => {
      const nav = primaryNav(page);
      const toggle = menuToggle(page);

      if (width < COMPACT_BELOW) {
        // Compact menu: hidden behind the toggle until it is opened.
        await expect(toggle).toBeVisible();
        await expect(toggle).toHaveAttribute('aria-expanded', 'false');
        await expect(nav).toBeHidden();
        await expect(navLink(page, 'Games')).toBeHidden();
        await toggle.click();
        await expect(toggle).toHaveAttribute('aria-expanded', 'true');
        await expect(nav).toBeVisible();
        await expect(navLink(page, 'Games')).toBeVisible();
        return;
      }

      // 768 and up: no toggle, the navigation is always visible.
      await expect(toggle).toBeHidden();
      await expect(nav).toBeVisible();
      const navBox = await nav.boundingBox();
      const homeBox = await navLink(page, 'Home').boundingBox();
      const gamesBox = await navLink(page, 'Games').boundingBox();
      const mainBox = await page.getByRole('main').boundingBox();
      if (!navBox || !homeBox || !gamesBox || !mainBox) throw new Error('missing bounding box');

      if (width >= SIDEBAR_FROM) {
        // Sidebar: at the left, narrow, links stacked vertically, content to its right.
        expect(navBox.x).toBeLessThanOrEqual(1);
        expect(navBox.width).toBeLessThan(width / 3);
        expect(navBox.height).toBeGreaterThan(navBox.width);
        expect(gamesBox.y).toBeGreaterThan(homeBox.y + homeBox.height - 1);
        expect(Math.abs(gamesBox.x - homeBox.x)).toBeLessThanOrEqual(1);
        expect(mainBox.x).toBeGreaterThanOrEqual(navBox.x + navBox.width - 1);
      } else {
        // Top bar: full width, links in a row (same vertical position), content below it.
        expect(navBox.width).toBeGreaterThanOrEqual(width - 1);
        expect(Math.abs(gamesBox.y - homeBox.y)).toBeLessThanOrEqual(1);
        expect(gamesBox.x).toBeGreaterThan(homeBox.x);
        expect(mainBox.y).toBeGreaterThanOrEqual(navBox.y + navBox.height - 1);
      }
    });

    test('shows a visible focus ring on a navigation link reached by keyboard', async ({
      page,
    }) => {
      // The compact menu opens with a click; every move after that is the keyboard.
      await openNavIfCompact(page);
      const link = navLink(page, 'Games');

      let focused = false;
      for (let presses = 0; presses < 25 && !focused; presses += 1) {
        await page.keyboard.press('Tab');
        focused = await link.evaluate((el) => el === document.activeElement);
      }
      expect(focused).toBe(true);

      const ring = await link.evaluate((el) => {
        const style = getComputedStyle(el);
        return {
          focusVisible: el.matches(':focus-visible'),
          outlineStyle: style.outlineStyle,
          outlineWidth: parseFloat(style.outlineWidth),
          boxShadow: style.boxShadow,
        };
      });
      expect(ring.focusVisible).toBe(true);
      const hasOutline = ring.outlineStyle !== 'none' && ring.outlineWidth > 0;
      const hasShadow = ring.boxShadow !== 'none';
      expect(hasOutline || hasShadow).toBe(true);
      // S-1.6.2 styles a 3px outline; Chromium's default 1px auto ring must not pass.
      expect(ring.outlineWidth).toBeGreaterThanOrEqual(2);
    });
  });
}
