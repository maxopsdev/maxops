type CsvPrimitive = string | number | boolean | null | undefined;
type CsvValue = CsvPrimitive | Record<string, unknown> | Array<unknown>;

export type CsvRow = Record<string, CsvValue>;

const stableJsonStringify = (value: unknown): string => {
  if (value === null || value === undefined) {
    return '';
  }

  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJsonStringify(item)).join(',')}]`;
  }

  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right));
    return `{${entries.map(([key, entryValue]) => `${JSON.stringify(key)}:${stableJsonStringify(entryValue)}`).join(',')}}`;
  }

  return JSON.stringify(value);
};

const serializeCsvValue = (value: CsvValue): string => {
  if (value === null || value === undefined) {
    return '';
  }

  if (typeof value === 'object') {
    return stableJsonStringify(value);
  }

  return String(value);
};

const escapeCsvValue = (value: string): string => `"${value.replace(/"/g, '""')}"`;

export const downloadCsv = (filename: string, rows: CsvRow[]) => {
  if (rows.length === 0) {
    return;
  }

  const headers = Object.keys(rows[0]);
  const csvContent = [
    headers.map(escapeCsvValue).join(','),
    ...rows.map((row) => headers.map((header) => escapeCsvValue(serializeCsvValue(row[header]))).join(',')),
  ].join('\n');

  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
};
