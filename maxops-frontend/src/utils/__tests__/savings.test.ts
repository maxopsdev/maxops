import { describe, expect, it } from 'vitest';
import { dedupedYearlySavings, resolveAsgSavings, summariseYearlySavings } from '../savings';
import type { CheckState } from '@/services/checks';

const state = (overrides: Partial<CheckState>): CheckState => ({
  check_id: 'c',
  name: 'c',
  description: '',
  resource_type: 'asg',
  resources_found: 1,
  ...overrides,
});

describe('dedupedYearlySavings', () => {
  it('counts one resource once when several checks flag it', () => {
    const states = [
      state({ check_id: 'asg_low_cpu', savings_by_resource: { 'my-asg': 3600 } }),
      state({ check_id: 'asg_idle', savings_by_resource: { 'my-asg': 3600 } }),
    ];
    expect(dedupedYearlySavings(states)).toBe(3600);
  });

  it('keeps the largest estimate for a shared resource', () => {
    const states = [
      state({ check_id: 'a', savings_by_resource: { 'my-asg': 1200 } }),
      state({ check_id: 'b', savings_by_resource: { 'my-asg': 4800 } }),
      state({ check_id: 'c', savings_by_resource: { 'my-asg': 2400 } }),
    ];
    expect(dedupedYearlySavings(states)).toBe(4800);
  });

  it('adds distinct resources together', () => {
    const states = [
      state({ check_id: 'a', savings_by_resource: { 'asg-1': 100, 'asg-2': 200 } }),
      state({ check_id: 'b', savings_by_resource: { 'asg-3': 300 } }),
    ];
    expect(dedupedYearlySavings(states)).toBe(600);
  });

  it('mixes shared and distinct resources correctly', () => {
    const states = [
      state({ check_id: 'a', savings_by_resource: { shared: 500, 'only-a': 100 } }),
      state({ check_id: 'b', savings_by_resource: { shared: 900, 'only-b': 200 } }),
    ];
    expect(dedupedYearlySavings(states)).toBe(900 + 100 + 200);
  });

  it('falls back to the check total when there is no breakdown', () => {
    const states = [state({ check_id: 'imported', potential_savings_yearly: 750 })];
    expect(dedupedYearlySavings(states)).toBe(750);
  });

  it('does not double count a check that has both a breakdown and a total', () => {
    const states = [
      state({
        check_id: 'a',
        potential_savings_yearly: 3600,
        savings_by_resource: { 'my-asg': 3600 },
      }),
    ];
    expect(dedupedYearlySavings(states)).toBe(3600);
  });

  it('adds breakdown and non-breakdown checks together', () => {
    const states = [
      state({ check_id: 'a', savings_by_resource: { 'asg-1': 1000 } }),
      state({ check_id: 'imported', potential_savings_yearly: 250 }),
    ];
    expect(dedupedYearlySavings(states)).toBe(1250);
  });

  it('ignores checks with no result yet', () => {
    expect(dedupedYearlySavings([undefined, undefined])).toBe(0);
  });

  it('treats an empty breakdown as no attributable savings', () => {
    const states = [state({ check_id: 'a', savings_by_resource: {}, potential_savings_yearly: 0 })];
    expect(dedupedYearlySavings(states)).toBe(0);
  });

  it('returns zero for no checks', () => {
    expect(dedupedYearlySavings([])).toBe(0);
  });
});

describe('summariseYearlySavings reporting', () => {
  it('counts how many resources more than one check flagged', () => {
    const states = [
      state({ check_id: 'a', savings_by_resource: { shared: 500, 'only-a': 100 } }),
      state({ check_id: 'b', savings_by_resource: { shared: 900 } }),
    ];
    const summary = summariseYearlySavings(states);
    expect(summary.sharedResourceCount).toBe(1);
  });

  it('reports nothing shared when every resource appears once', () => {
    const states = [
      state({ check_id: 'a', savings_by_resource: { 'asg-1': 100 } }),
      state({ check_id: 'b', savings_by_resource: { 'asg-2': 200 } }),
    ];
    expect(summariseYearlySavings(states).sharedResourceCount).toBe(0);
  });

  it('exposes the undeduplicated total so the gap can be explained', () => {
    const states = [
      state({ check_id: 'a', potential_savings_yearly: 3600, savings_by_resource: { x: 3600 } }),
      state({ check_id: 'b', potential_savings_yearly: 3600, savings_by_resource: { x: 3600 } }),
    ];
    const summary = summariseYearlySavings(states);
    expect(summary.total).toBe(3600);
    expect(summary.rawTotal).toBe(7200);
  });
});

describe('resolveAsgSavings', () => {
  it('prefers the rightsizer figure and names its source', () => {
    const result = resolveAsgSavings(4200, 3600);
    expect(result).toEqual({ value: 4200, source: 'rightsizer', alternative: 3600 });
  });

  it('reports the heuristic figure so the difference can be shown', () => {
    expect(resolveAsgSavings(4200, 3600).alternative).toBe(3600);
  });

  it('offers no alternative when the two models agree', () => {
    expect(resolveAsgSavings(3600, 3600).alternative).toBeNull();
  });

  it('falls back to the heuristic when there is no recommendation', () => {
    expect(resolveAsgSavings(null, 3600)).toEqual({
      value: 3600,
      source: 'heuristic',
      alternative: null,
    });
  });

  it('reports zero with no source when neither model has a figure', () => {
    expect(resolveAsgSavings(null, null)).toEqual({ value: 0, source: 'none', alternative: null });
  });

  it('treats a zero rightsizer figure as a real answer, not a missing one', () => {
    const result = resolveAsgSavings(0, 3600);
    expect(result.value).toBe(0);
    expect(result.source).toBe('rightsizer');
  });
});
