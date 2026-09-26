import { describe, expect, it } from 'vitest';
import {
  RIGHTSIZING_CLASSIFICATION_ORDER,
  rightsizingClassificationClass,
} from '../classification';

describe('shared rightsizing classification presentation', () => {
  it('keeps fleet ordering and badge styles in one contract', () => {
    expect(RIGHTSIZING_CLASSIFICATION_ORDER).toEqual({
      ACTIONABLE: 0,
      CONDITIONAL: 1,
      INSUFFICIENT_DATA: 2,
      DEFERRED: 3,
      NONE: 4,
    });
    expect(rightsizingClassificationClass('INSUFFICIENT_DATA')).toContain('bg-danger-100');
    expect(rightsizingClassificationClass(null)).toContain('bg-gray-100');
  });
});
