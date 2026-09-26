import React, { useState } from 'react';
import { BookmarkPlus, ChevronDown, RotateCcw, Search } from 'lucide-react';
import { Card } from './Card';

export interface ResourceFilterOption {
  label: string;
  value: string;
}

export interface ResourceFilterSelect {
  id: string;
  label: string;
  value: string;
  options: ResourceFilterOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
}

export interface ResourceFilterToggle {
  id: string;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

interface ResourcePageFiltersProps {
  searchValue?: string;
  searchPlaceholder?: string;
  onSearchChange?: (value: string) => void;
  selects?: ResourceFilterSelect[];
  toggles?: ResourceFilterToggle[];
  savedFilterSelect?: ResourceFilterSelect;
  onSaveCurrentFilter?: () => void;
  saveCurrentFilterDisabled?: boolean;
  onReset?: () => void;
  resetDisabled?: boolean;
  children?: React.ReactNode;
}

export const ResourcePageFilters: React.FC<ResourcePageFiltersProps> = ({
  searchValue = '',
  searchPlaceholder = 'Search resources, checks, or identifiers',
  onSearchChange,
  selects = [],
  toggles = [],
  savedFilterSelect,
  onSaveCurrentFilter,
  saveCurrentFilterDisabled = false,
  onReset,
  resetDisabled = false,
  children,
}) => {
  const [isOpen, setIsOpen] = useState(true);
  const hasControls =
    Boolean(onSearchChange) ||
    Boolean(savedFilterSelect) ||
    selects.length > 0 ||
    toggles.length > 0 ||
    Boolean(children);
  const hasSavedFilterControls = Boolean(savedFilterSelect) || Boolean(onSaveCurrentFilter);

  return (
    <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
      <div className="flex items-center justify-between gap-4">
        <button
          type="button"
          onClick={() => setIsOpen((current) => !current)}
          className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-gray-600 transition hover:text-gray-900 dark:text-gray-300 dark:hover:text-white"
        >
          <ChevronDown size={14} className={isOpen ? 'transition' : '-rotate-90 transition'} />
          Filters
        </button>
        {onReset ? (
          <button
            type="button"
            onClick={onReset}
            disabled={resetDisabled}
            className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
          >
            <RotateCcw size={14} />
            Reset
          </button>
        ) : null}
      </div>

      {isOpen && hasControls ? (
        <>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {onSearchChange ? (
              <label className="block">
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Search</div>
                <div className="relative">
                  <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -trangray-y-1/2 text-gray-400" />
                  <input
                    type="text"
                    value={searchValue}
                    onChange={(event) => onSearchChange(event.target.value)}
                    placeholder={searchPlaceholder}
                    className="w-full rounded-xl border border-gray-200 bg-gray-50 py-2.5 pl-9 pr-3 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-800/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                  />
                </div>
              </label>
            ) : null}

            {selects.map((select) => (
              <label key={select.id} className="block">
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{select.label}</div>
                <select
                  value={select.value}
                  onChange={(event) => select.onChange(event.target.value)}
                  disabled={select.disabled}
                  className="w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 disabled:cursor-not-allowed disabled:opacity-70 dark:border-gray-700 dark:bg-gray-800/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                >
                  {select.options.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            ))}

            {toggles.map((toggle) => (
              <button
                key={toggle.id}
                type="button"
                role="switch"
                aria-checked={toggle.checked}
                onClick={() => toggle.onChange(!toggle.checked)}
                className={`inline-flex h-fit items-center justify-between gap-2.5 self-end rounded-xl px-3 py-2 text-xs font-medium ring-1 transition ${
                  toggle.checked
                    ? 'bg-warning-50 text-warning-800 ring-warning-300 dark:bg-warning-950/40 dark:text-warning-200 dark:ring-warning-800'
                    : 'bg-white text-gray-700 ring-gray-200 hover:bg-gray-50 dark:bg-gray-900/80 dark:text-gray-200 dark:ring-gray-700 dark:hover:bg-gray-800'
                }`}
              >
                <span>{toggle.label}</span>
                <span
                  className={`relative h-5 w-9 rounded-full transition ${
                    toggle.checked ? 'bg-warning-500' : 'bg-gray-300 dark:bg-gray-600'
                  }`}
                >
                  <span
                    className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition ${
                      toggle.checked ? 'left-[18px]' : 'left-0.5'
                    }`}
                  />
                </span>
              </button>
            ))}

            {children}
        </div>
        {hasSavedFilterControls ? (
          <div className="mt-3 flex flex-wrap items-end justify-end gap-3">
            {savedFilterSelect ? (
              <label className="block min-w-[180px] max-w-[260px]">
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{savedFilterSelect.label}</div>
                <select
                  value={savedFilterSelect.value}
                  onChange={(event) => savedFilterSelect.onChange(event.target.value)}
                  disabled={savedFilterSelect.disabled}
                  className="h-[38px] w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 disabled:cursor-not-allowed disabled:opacity-70 dark:border-gray-700 dark:bg-gray-800/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                >
                  {savedFilterSelect.options.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}

            {onSaveCurrentFilter ? (
              <div className="block">
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Saved filters</div>
                <button
                  type="button"
                  onClick={onSaveCurrentFilter}
                  disabled={saveCurrentFilterDisabled}
                  className="inline-flex h-[38px] items-center justify-center gap-2 rounded-xl border border-gray-200 bg-white px-3 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
                >
                  <BookmarkPlus size={14} />
                  Save current
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
        </>
      ) : null}
    </Card>
  );
};
