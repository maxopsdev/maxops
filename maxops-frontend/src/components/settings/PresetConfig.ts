export const PRESET_OPTIONS = ['conservative', 'normal', 'aggressive'] as const;
export type PresetKey = (typeof PRESET_OPTIONS)[number];

export const PRESET_ACCENTS: Record<PresetKey, string> = {
  conservative: 'bg-gray-100 text-gray-700 border-gray-300 dark:bg-gray-900 dark:text-gray-200 dark:border-gray-700',
  normal: 'bg-gray-100 text-gray-700 border-gray-300 dark:bg-gray-900 dark:text-gray-200 dark:border-gray-700',
  aggressive: 'bg-gray-100 text-gray-700 border-gray-300 dark:bg-gray-900 dark:text-gray-200 dark:border-gray-700',
};
