import { describe, expect, it } from 'vitest';
import {
  elasticacheDisplayedPotential,
  elasticacheStatusTone,
  type ElastiCacheRightsizingResponse,
} from '../recommendations';

const recommendation = (classification: ElastiCacheRightsizingResponse['classification']) => ({
  classification,
  tiers: { balanced: null },
  replica_recommendation: null,
} as ElastiCacheRightsizingResponse);

describe('elasticacheDisplayedPotential', () => {
  it('uses legacy savings only when no rightsizer response exists', () => {
    expect(elasticacheDisplayedPotential(undefined, 4_000)).toBe(4_000);
    expect(elasticacheDisplayedPotential(recommendation('DEFERRED'), 4_000)).toBe(0);
    expect(elasticacheDisplayedPotential(recommendation('NO_CANDIDATE'), 4_000)).toBe(0);
  });

  it('keeps legacy actionable findings critical and rightsizer actionable positive', () => {
    expect(elasticacheStatusTone('actionable')).toBe('critical');
    expect(elasticacheStatusTone('ACTIONABLE')).toBe('positive');
    expect(elasticacheStatusTone('CONDITIONAL')).toBe('warning');
  });
});
