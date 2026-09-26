import React from 'react';

import { PRESET_ACCENTS, PRESET_OPTIONS, type PresetKey } from '@/components/settings/PresetConfig';

export const SegmentedPreset: React.FC<{
  preset: PresetKey;
  descriptions: Record<PresetKey, string>;
  onChange: (preset: PresetKey) => void;
}> = ({ preset, descriptions, onChange }) => (
  <div className="space-y-3">
    <div className="grid grid-cols-3 gap-2 rounded-2xl bg-gray-100 p-1 dark:bg-gray-950">
      {PRESET_OPTIONS.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onChange(option)}
          className={`rounded-2xl px-3 py-3 text-sm font-semibold capitalize transition-all ${
            preset === option
              ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-900 dark:text-gray-100'
              : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'
          }`}
        >
          {option}
        </button>
      ))}
    </div>
    <div className={`rounded-2xl border px-4 py-3 text-sm ${PRESET_ACCENTS[preset]}`}>
      {descriptions[preset]}
    </div>
  </div>
);
