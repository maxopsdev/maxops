import React, { useMemo, useState } from 'react';
import { ChevronDown, Search, X } from 'lucide-react';
import {
  formatSharedTagSelectionLabel,
  parseSharedTagSelection,
  serializeSharedTagSelection,
  type SharedTagLogic,
  type SharedTagMatchMode,
} from '@/utils/sharedOverviewFilters';

interface SharedTagFilterFieldProps {
  selectedTags: string[];
  availableTags: string[];
  tagLogic: SharedTagLogic;
  onChange: (tags: string[]) => void;
  onTagLogicChange: (logic: SharedTagLogic) => void;
  label?: string;
  emptyLabel?: string;
  searchPlaceholder?: string;
}

export const SharedTagFilterField: React.FC<SharedTagFilterFieldProps> = ({
  selectedTags,
  availableTags,
  tagLogic,
  onChange,
  onTagLogicChange,
  label = 'Tags',
  emptyLabel = 'All tags',
  searchPlaceholder = 'Search key:value tags',
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [searchValue, setSearchValue] = useState('');
  const [showSelectedOnly, setShowSelectedOnly] = useState(false);
  const [customMode, setCustomMode] = useState<Exclude<SharedTagMatchMode, 'exact'>>('contains');
  const [customValue, setCustomValue] = useState('');

  const exactSelectedTags = useMemo(
    () =>
      selectedTags.filter((selection) => parseSharedTagSelection(selection).mode === 'exact'),
    [selectedTags]
  );

  const filteredTagOptions = useMemo(() => {
    const source = showSelectedOnly ? exactSelectedTags : availableTags;
    const query = searchValue.trim().toLowerCase();
    if (!query) {
      return source;
    }

    return source.filter((tag) => tag.toLowerCase().includes(query));
  }, [availableTags, exactSelectedTags, searchValue, showSelectedOnly]);

  const buttonLabel =
    selectedTags.length === 0
      ? emptyLabel
      : selectedTags.length === 1
        ? formatSharedTagSelectionLabel(selectedTags[0])
        : `${selectedTags.length} tags`;

  const toggleTag = (tag: string) => {
    onChange(
      selectedTags.includes(tag)
        ? selectedTags.filter((item) => item !== tag)
        : [...selectedTags, tag]
    );
  };

  const addCustomSelection = () => {
    const serialized = serializeSharedTagSelection({
      mode: customMode,
      value: customValue,
    });

    if (!serialized || selectedTags.includes(serialized)) {
      return;
    }

    onChange([...selectedTags, serialized]);
    setCustomValue('');
  };

  return (
    <div className="relative">
      <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{label}</div>
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        className="inline-flex w-full items-center justify-between gap-3 rounded-2xl bg-gray-50 px-4 py-3 text-sm font-medium text-gray-700 ring-1 ring-gray-200 transition hover:bg-gray-100 dark:bg-gray-800/80 dark:text-gray-100 dark:ring-gray-700 dark:hover:bg-gray-800"
      >
        <span className="truncate">{buttonLabel}</span>
        <ChevronDown size={16} className={isOpen ? 'rotate-180 transition' : 'transition'} />
      </button>

      {isOpen ? (
        <div className="absolute left-0 top-full z-20 mt-2 w-full rounded-2xl border border-gray-200 bg-white p-3 shadow-xl dark:border-gray-700 dark:bg-gray-900">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">Select tags</div>
            <button
              type="button"
              onClick={() => setIsOpen(false)}
              className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
            >
              Close
            </button>
          </div>

          <div className="relative mb-3">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -trangray-y-1/2 text-gray-400" />
            <input
              type="text"
              value={searchValue}
              onChange={(event) => setSearchValue(event.target.value)}
              placeholder={searchPlaceholder}
              className="w-full rounded-xl border border-gray-200 bg-white py-2 pl-9 pr-3 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-800/80 dark:text-gray-100"
            />
          </div>

          <div className="mb-3 flex gap-2">
            <button
              type="button"
              onClick={() => {
                setSearchValue('');
                setShowSelectedOnly(false);
                onChange([]);
              }}
              className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-700 transition hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-200"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={() => setShowSelectedOnly((current) => !current)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                showSelectedOnly
                  ? 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-200'
              }`}
            >
              {showSelectedOnly ? 'Show all' : 'Show selected'}
            </button>
          </div>

          <div className="mb-3">
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Tag logic</div>
            <div className="inline-flex rounded-xl border border-gray-200 bg-white p-1 dark:border-gray-700 dark:bg-gray-900">
              {[
                { value: 'and' as const, label: 'Match all' },
                { value: 'or' as const, label: 'Match any' },
              ].map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => onTagLogicChange(option.value)}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                    tagLogic === option.value
                      ? 'bg-warning-100 text-warning-900 dark:bg-warning-900/50 dark:text-warning-100'
                      : 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800'
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mb-3 rounded-xl border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-800/60">
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Pattern match</div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <select
                value={customMode}
                onChange={(event) => setCustomMode(event.target.value as Exclude<SharedTagMatchMode, 'exact'>)}
                className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
              >
                <option value="contains">Contains</option>
                <option value="starts_with">Starts with</option>
                <option value="ends_with">Ends with</option>
              </select>
              <input
                type="text"
                value={customValue}
                onChange={(event) => setCustomValue(event.target.value)}
                placeholder="team:, :prod, platform"
                className="min-w-0 flex-1 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
              />
              <button
                type="button"
                onClick={addCustomSelection}
                disabled={!customValue.trim()}
                className="inline-flex items-center justify-center rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
              >
                Add
              </button>
            </div>
          </div>

          {selectedTags.length > 0 ? (
            <div className="mb-3 flex flex-wrap gap-2">
              {selectedTags.map((selection) => {
                const parsed = parseSharedTagSelection(selection);
                return (
                  <span
                    key={selection}
                    className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
                  >
                    <span className="max-w-[220px] truncate">{formatSharedTagSelectionLabel(parsed)}</span>
                    <button
                      type="button"
                      onClick={() => onChange(selectedTags.filter((item) => item !== selection))}
                      className="text-gray-400 transition hover:text-gray-700 dark:hover:text-gray-200"
                      aria-label={`Remove ${formatSharedTagSelectionLabel(parsed)}`}
                    >
                      <X size={12} />
                    </button>
                  </span>
                );
              })}
            </div>
          ) : null}

          <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
            {filteredTagOptions.length === 0 ? (
              <div className="rounded-xl bg-gray-50 px-3 py-4 text-sm text-gray-500 dark:bg-gray-800 dark:text-gray-400">No tags match your search.</div>
            ) : (
              filteredTagOptions.map((tag) => {
                const checked = selectedTags.includes(tag);
                return (
                  <label
                    key={tag}
                    className="flex cursor-pointer items-center justify-between gap-3 rounded-xl px-3 py-2 text-sm text-gray-700 transition hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800"
                  >
                    <span className="truncate">{tag}</span>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleTag(tag)}
                      className="h-4 w-4 rounded border-gray-300 text-warning-500 focus:ring-warning-400"
                    />
                  </label>
                );
              })
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
};
