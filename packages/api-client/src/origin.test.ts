import { describe, expect, it } from 'vitest';
import { apiOrigin } from './origin';

describe('apiOrigin', () => {
  it.each([
    ['http://localhost:8000/api/v1', 'http://localhost:8000'],
    ['https://api.example.com/api/v1/', 'https://api.example.com'],
    ['https://api.example.com', 'https://api.example.com'],
    ['  https://api.example.com/api/v1  ', 'https://api.example.com'],
  ])('turns %s into %s', (input, expected) => {
    expect(apiOrigin(input)).toBe(expected);
  });

  it.each([
    ['http://10.0.2.2:8000/api/v1', 'http://10.0.2.2:8000'],
    ['https://example.com/prefix/api/v1', 'https://example.com/prefix'],
    ['https://example.com/api/v10', 'https://example.com/api/v10'],
  ])('handles the edge case %s', (input, expected) => {
    expect(apiOrigin(input)).toBe(expected);
  });
});
