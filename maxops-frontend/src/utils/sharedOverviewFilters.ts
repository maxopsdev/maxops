export const normalizeSharedFilterValue = (value: string | null | undefined) =>
  (value || '').trim().toLowerCase();

export type SharedTagMatchMode = 'exact' | 'starts_with' | 'ends_with' | 'contains';
export type SharedTagLogic = 'and' | 'or';

export interface SharedTagSelection {
  mode: SharedTagMatchMode;
  value: string;
}

const SHARED_TAG_PREFIXES: Record<Exclude<SharedTagMatchMode, 'exact'>, string> = {
  starts_with: '__starts_with__:',
  ends_with: '__ends_with__:',
  contains: '__contains__:',
};

export const buildSharedTagOptions = (
  items: Array<{ tags?: Record<string, string> | null | undefined }>
): string[] => {
  const tags = new Set<string>();

  items.forEach((item) => {
    Object.entries(item.tags || {}).forEach(([key, value]) => {
      if (!key || value === undefined || value === null || value === '') {
        return;
      }

      tags.add(`${key}:${String(value)}`);
    });
  });

  return Array.from(tags).sort((left, right) => left.localeCompare(right));
};

export const parseSharedTagQuery = (tagQuery: string): string[] =>
  tagQuery
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean);

export const parseSharedTagSelection = (selection: string): SharedTagSelection => {
  const trimmed = selection.trim();

  const startsWithPrefix = SHARED_TAG_PREFIXES.starts_with;
  if (trimmed.startsWith(startsWithPrefix)) {
    return { mode: 'starts_with', value: trimmed.slice(startsWithPrefix.length).trim() };
  }

  const endsWithPrefix = SHARED_TAG_PREFIXES.ends_with;
  if (trimmed.startsWith(endsWithPrefix)) {
    return { mode: 'ends_with', value: trimmed.slice(endsWithPrefix.length).trim() };
  }

  const containsPrefix = SHARED_TAG_PREFIXES.contains;
  if (trimmed.startsWith(containsPrefix)) {
    return { mode: 'contains', value: trimmed.slice(containsPrefix.length).trim() };
  }

  return { mode: 'exact', value: trimmed };
};

export const serializeSharedTagSelection = (selection: SharedTagSelection): string => {
  const value = selection.value.trim();
  if (!value) {
    return '';
  }

  if (selection.mode === 'exact') {
    return value;
  }

  return `${SHARED_TAG_PREFIXES[selection.mode]}${value}`;
};

export const formatSharedTagSelectionLabel = (selection: string | SharedTagSelection): string => {
  const parsed = typeof selection === 'string' ? parseSharedTagSelection(selection) : selection;

  if (parsed.mode === 'exact') {
    return parsed.value;
  }

  if (parsed.mode === 'starts_with') {
    return `Starts with: ${parsed.value}`;
  }

  if (parsed.mode === 'ends_with') {
    return `Ends with: ${parsed.value}`;
  }

  return `Contains: ${parsed.value}`;
};

export const matchesSharedTagSelections = (
  tags: Record<string, string> | null | undefined,
  selections: string[],
  logic: SharedTagLogic = 'and'
): boolean => {
  if (selections.length === 0) {
    return true;
  }

  const source = tags || {};
  const matcher = (rawSelection: string) => {
    const selection = parseSharedTagSelection(rawSelection);

    if (selection.mode === 'exact') {
      const [tagKey, ...valueParts] = selection.value.split(':');
      const normalizedKey = tagKey.trim();
      if (!normalizedKey) {
        return false;
      }

      const expectedValue = valueParts.join(':').trim();
      const actualValue = String(source[normalizedKey] || '');

      if (!expectedValue) {
        return Object.prototype.hasOwnProperty.call(source, normalizedKey);
      }

      return actualValue === expectedValue;
    }

    const normalizedNeedle = normalizeSharedFilterValue(selection.value);
    if (!normalizedNeedle) {
      return false;
    }

    return Object.entries(source).some(([key, value]) => {
      const candidate = normalizeSharedFilterValue(`${key}:${String(value)}`);
      if (selection.mode === 'starts_with') {
        return candidate.startsWith(normalizedNeedle);
      }
      if (selection.mode === 'ends_with') {
        return candidate.endsWith(normalizedNeedle);
      }
      return candidate.includes(normalizedNeedle);
    });
  };

  return logic === 'or' ? selections.some(matcher) : selections.every(matcher);
};

export const matchesSharedTagQuery = (
  tags: Record<string, string> | null | undefined,
  tagQuery: string,
  logic: SharedTagLogic = 'and'
): boolean => {
  return matchesSharedTagSelections(tags, parseSharedTagQuery(tagQuery), logic);
};
