export const formatControlValue = (value: any, unit?: string | null) => {
  if (typeof value === 'boolean') {
    return value ? 'On' : 'Off';
  }
  if (Array.isArray(value)) {
    return value.join(', ');
  }
  if (value === null || value === undefined || value === '') {
    return 'Not set';
  }
  if (unit === '%' && typeof value === 'number') {
    return `${value}%`;
  }
  if (unit && unit !== 'ratio') {
    return `${value} ${unit}`;
  }
  return String(value);
};

export const clampControlValue = (value: number, min?: number | null, max?: number | null) => {
  let next = value;
  if (typeof min === 'number') {
    next = Math.max(min, next);
  }
  if (typeof max === 'number') {
    next = Math.min(max, next);
  }
  return next;
};

export const normalizeBooleanValue = (value: any): boolean => {
  if (typeof value === 'boolean') {
    return value;
  }
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (normalized === 'true' || normalized === '1' || normalized === 'yes' || normalized === 'on') {
      return true;
    }
    if (normalized === 'false' || normalized === '0' || normalized === 'no' || normalized === 'off' || normalized === '') {
      return false;
    }
  }
  if (typeof value === 'number') {
    return value !== 0;
  }
  return Boolean(value);
};
