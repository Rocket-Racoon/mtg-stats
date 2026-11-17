import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import prettier from 'eslint-config-prettier';

export default tseslint.config(
  {
    ignores: [
      '**/node_modules/',
      '**/dist/',
      '**/build/',
      '**/coverage/',
      '**/.expo/',
      '**/generated/',
      '**/playwright-report/',
      '**/test-results/',
      '**/blob-report/',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks },
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    rules: { ...reactHooks.configs.recommended.rules },
  },
  {
    files: ['apps/web/**/*.{ts,tsx}', 'apps/mobile/**/*.{ts,tsx}'],
    plugins: { react },
    settings: { react: { version: '19.3' } },
    rules: {
      ...react.configs.recommended.rules,
      ...react.configs['jsx-runtime'].rules,
    },
  },
  {
    // The client is bundled by Vite and Metro: no Node-only APIs (S-1.5.2).
    files: ['packages/api-client/src/**/*.ts'],
    ignores: ['packages/api-client/src/**/*.test.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              regex:
                '^(node:|(assert|buffer|child_process|cluster|crypto|dgram|dns|events|fs|http|http2|https|module|net|os|path|perf_hooks|process|querystring|readline|stream|string_decoder|timers|tls|url|util|vm|worker_threads|zlib)(/|$))',
              message: 'Node built-ins are not available in Vite or Metro bundles.',
            },
          ],
        },
      ],
      'no-restricted-globals': [
        'error',
        { name: 'process', message: 'Node-only global.' },
        { name: 'Buffer', message: 'Node-only global.' },
        { name: '__dirname', message: 'Node-only global.' },
        { name: '__filename', message: 'Node-only global.' },
        { name: 'require', message: 'Node-only global.' },
      ],
    },
  },
  {
    // The e2e suite targets elements by role and label, never by CSS or XPath (S-1.8.5 AC4).
    files: ['apps/web/e2e/**/*.ts'],
    rules: {
      'no-restricted-syntax': [
        'error',
        ...['locator', '$', '$$', '$eval', '$$eval', 'waitForSelector'].map((name) => ({
          selector: `CallExpression[callee.property.name='${name}']`,
          message: `S-1.8.5: no CSS/XPath selectors in e2e; use getByRole, getByLabel, getByText or getByTestId (not .${name}()).`,
        })),
        {
          // page.click('#x'), page.fill('.y', ...) and friends take a selector string.
          selector:
            "CallExpression[callee.object.name='page'][callee.property.name=/^(click|dblclick|fill|type|press|check|uncheck|hover|focus|tap|selectOption|setInputFiles|textContent|innerText|innerHTML|inputValue|getAttribute|isVisible|isHidden|isEnabled|isDisabled|isChecked|isEditable|dispatchEvent|dragAndDrop)$/]",
          message:
            'S-1.8.5: Page-level actions take a CSS selector; use a getByRole/getByLabel locator instead.',
        },
        {
          selector:
            'MemberExpression[computed=true][property.value=/^(locator|\\$|\\$\\$|frameLocator)$/]',
          message: 'S-1.8.5: no computed access to locator/$/$$/frameLocator in e2e.',
        },
        {
          selector:
            'CallExpression[callee.property.name=/^(querySelector|querySelectorAll|getElementById|getElementsByClassName|getElementsByTagName|frameLocator)$/]',
          message:
            'S-1.8.5: no DOM queries or frameLocator in e2e; find elements by role or label.',
        },
      ],
    },
  },
  {
    files: ['**/*.{js,cjs,mjs}'],
    // Browser scripts served as-is get browser globals only (next block), never Node's.
    ignores: ['apps/web/public/**'],
    languageOptions: { globals: { ...globals.node, ...globals.commonjs } },
  },
  {
    files: ['apps/web/public/**/*.js'],
    languageOptions: { sourceType: 'script', globals: { ...globals.browser } },
  },
  {
    files: ['**/*.config.{js,cjs}'],
    rules: { '@typescript-eslint/no-require-imports': 'off' },
  },
  prettier,
);
