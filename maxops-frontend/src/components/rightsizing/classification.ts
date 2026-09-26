import type { ResourceRecommendationClassification } from '@/services/recommendations';

export type RightsizingDisplayClassification = ResourceRecommendationClassification | 'NONE';

export const RIGHTSIZING_CLASSIFICATION_ORDER: Record<RightsizingDisplayClassification, number> = {
  ACTIONABLE: 0,
  CONDITIONAL: 1,
  INSUFFICIENT_DATA: 2,
  DEFERRED: 3,
  NONE: 4,
};

const CLASSIFICATION_CLASSES: Record<RightsizingDisplayClassification, string> = {
  ACTIONABLE: 'bg-success-100 text-success-800 dark:bg-success-950/60 dark:text-success-200',
  CONDITIONAL: 'bg-warning-100 text-warning-800 dark:bg-warning-950/60 dark:text-warning-200',
  DEFERRED: 'bg-gray-200 text-gray-700 dark:bg-gray-800 dark:text-gray-200',
  INSUFFICIENT_DATA: 'bg-danger-100 text-danger-800 dark:bg-danger-950/60 dark:text-danger-200',
  NONE: 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-300',
};

export const rightsizingClassificationClass = (
  classification: ResourceRecommendationClassification | null | undefined
): string => CLASSIFICATION_CLASSES[classification || 'NONE'];
