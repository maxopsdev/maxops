import React from 'react';
import * as Switch from '@radix-ui/react-switch';

export const ToggleControl: React.FC<{
  value: boolean;
  onChange: (value: boolean) => void;
}> = ({ value, onChange }) => (
  <div
    className={`flex w-full cursor-pointer items-center justify-between rounded-2xl border px-4 py-3 transition-all ${
      value
        ? 'border-primary-500 bg-primary-600 text-white dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100'
        : 'border-gray-300 bg-white text-gray-700 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200'
    }`}
  >
    <span className="text-sm font-medium">{value ? 'Enabled' : 'Disabled'}</span>
    <Switch.Root
      checked={value}
      onCheckedChange={onChange}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border border-transparent transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 ${
        value ? 'bg-white/25 dark:bg-gray-700' : 'bg-gray-300 dark:bg-gray-800'
      }`}
    >
      <Switch.Thumb
        className={`block h-5 w-5 rounded-full bg-white shadow-sm transition-transform data-[state=checked]:translate-x-5 data-[state=unchecked]:translate-x-0 ${
          value ? 'translate-x-5' : 'translate-x-0'
        }`}
      />
    </Switch.Root>
  </div>
);
