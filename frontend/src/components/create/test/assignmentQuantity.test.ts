import { describe, expect, it } from 'vitest';

import {
  nextAssignmentCount,
  parseAssignmentCount,
} from '../assignmentQuantity';

describe('nextAssignmentCount', () => {
  it('adds the requested step when remaining seats are enough', () => {
    expect(nextAssignmentCount(2, 5, 4, 12)).toBe(7);
    expect(nextAssignmentCount(0, 10, 0, 12)).toBe(10);
  });

  it('clamps an increase to remaining seats', () => {
    expect(nextAssignmentCount(1, 5, 8, 9)).toBe(2);
    expect(nextAssignmentCount(0, 10, 9, 9)).toBe(0);
  });

  it('does not increase when increases are blocked', () => {
    expect(nextAssignmentCount(3, 5, 3, 12, false)).toBe(3);
  });

  it('subtracts the requested step and never goes below zero', () => {
    expect(nextAssignmentCount(9, -5, 9, 9)).toBe(4);
    expect(nextAssignmentCount(4, -10, 4, 9)).toBe(0);
  });
});

describe('parseAssignmentCount', () => {
  it('parses a whole number and clamps it to remaining seats', () => {
    expect(parseAssignmentCount('4', 0, 5, 9)).toBe(4);
    expect(parseAssignmentCount('20', 1, 8, 9)).toBe(2);
  });

  it('allows decreasing an over-allocated row but not increasing it', () => {
    expect(parseAssignmentCount('0', 5, 14, 9)).toBe(0);
    expect(parseAssignmentCount('8', 5, 14, 9)).toBe(5);
  });

  it('rejects empty or non-integer input so the field can revert', () => {
    expect(parseAssignmentCount('', 3, 3, 9)).toBeNull();
    expect(parseAssignmentCount('  ', 3, 3, 9)).toBeNull();
    expect(parseAssignmentCount('1.5', 3, 3, 9)).toBeNull();
    expect(parseAssignmentCount('-1', 3, 3, 9)).toBeNull();
  });

  it('does not raise the count when increases are blocked', () => {
    expect(parseAssignmentCount('8', 2, 2, 9, false)).toBe(2);
    expect(parseAssignmentCount('0', 2, 2, 9, false)).toBe(0);
  });
});
