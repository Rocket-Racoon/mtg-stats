import { describe, expect, it } from 'vitest';
import { apiOrigin } from './origin';

describe('apiOrigin', () => {
  it.each([
    ['http://localhost:8000/api/v1', 'http://localhost:8000'],
    ['https://api.example.com/api/v1/', 'https://api.example.com'],
    ['https://api.example.com', 'https://api.example.com'],
  ])('turns %s into %s', (input, expected) => {
    expect(apiOrigin(input)).toBe(expected);
  });
});
