import React, { useEffect, useMemo, useState } from 'react';
import { Calendar, Clock, Play } from 'lucide-react';
import { Modal } from '@/components/common/Modal';
import { cn } from '@/utils/helpers';

type ConfirmColumn<T> = {
  header: string;
  render: (item: T) => React.ReactNode;
};

export type ResourceSnoozeState = {
  snoozed_until?: string | null;
  snooze_days?: number | null;
  reason?: string | null;
  active?: boolean;
};

type ResourceActionPanelProps<T> = {
  title: string;
  subtitle?: string;
  resourceLabel: string;
  items: T[];
  getId: (item: T) => string;
  getName: (item: T) => string;
  getActions: (item: T) => string[];
  canExecute: (item: T, action: string) => boolean;
  executeAction: (item: T, action: string) => Promise<void>;
  renderItem: (item: T) => React.ReactNode;
  confirmColumns: ConfirmColumn<T>[];
  toolbar?: React.ReactNode;
  className?: string;
  formatActionLabel?: (action: string) => string;
  getSnooze?: (item: T) => ResourceSnoozeState | null | undefined;
  onSnooze?: (items: T[], snoozedUntil: string, reason: string) => Promise<void>;
  onRemoveSnooze?: (items: T[], reason?: string) => Promise<void>;
  snoozeColumns?: ConfirmColumn<T>[];
};

const defaultFormatActionLabel = (value: string) =>
  value
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');

const formatDateInput = (date: Date) => {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, '0');
  const day = `${date.getDate()}`.padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const defaultExpiryDate = () => {
  const date = new Date();
  date.setDate(date.getDate() + 30);
  return formatDateInput(date);
};

const formatSnoozeDate = (value?: string | null) => {
  if (!value) return 'unknown date';
  return new Date(value).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
};

export function ResourceActionPanel<T>({
  title,
  subtitle,
  resourceLabel,
  items,
  getId,
  getName,
  getActions,
  canExecute,
  executeAction,
  renderItem,
  confirmColumns,
  toolbar,
  className,
  formatActionLabel = defaultFormatActionLabel,
  getSnooze,
  onSnooze,
  onRemoveSnooze,
  snoozeColumns,
}: ResourceActionPanelProps<T>) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState('');
  const [isConfirmOpen, setIsConfirmOpen] = useState(false);
  const [snoozeItems, setSnoozeItems] = useState<T[]>([]);
  const [snoozeFilter, setSnoozeFilter] = useState<'all' | 'snoozed' | 'not-snoozed'>('all');
  const [snoozeDate, setSnoozeDate] = useState(defaultExpiryDate);
  const [snoozeReason, setSnoozeReason] = useState('');
  const [snoozeError, setSnoozeError] = useState<string | null>(null);
  const [snoozeReasonError, setSnoozeReasonError] = useState<string | null>(null);
  const [isSnoozing, setIsSnoozing] = useState(false);
  const [isRemovingSnooze, setIsRemovingSnooze] = useState(false);

  const visibleItems = useMemo(() => {
    if (!getSnooze || snoozeFilter === 'all') {
      return items;
    }
    return items.filter((item) => {
      const isSnoozed = Boolean(getSnooze(item)?.active);
      return snoozeFilter === 'snoozed' ? isSnoozed : !isSnoozed;
    });
  }, [getSnooze, items, snoozeFilter]);

  const selectedItems = useMemo(
    () => visibleItems.filter((item) => selectedIds.has(getId(item))),
    [getId, selectedIds, visibleItems]
  );

  const selectableItems = useMemo(
    () => visibleItems.filter((item) => Boolean(onSnooze || onRemoveSnooze) || getActions(item).some((action) => canExecute(item, action))),
    [canExecute, getActions, onRemoveSnooze, onSnooze, visibleItems]
  );

  const activeDialogSnoozeItems = useMemo(
    () => snoozeItems.filter((item) => Boolean(getSnooze?.(item)?.active)),
    [getSnooze, snoozeItems]
  );

  const commonActions = useMemo(() => {
    if (selectedItems.length === 0) {
      return [];
    }

    const actionSets = selectedItems.map((item) =>
      new Set(getActions(item).filter((action) => canExecute(item, action)))
    );
    const [firstSet, ...restSets] = actionSets;
    return Array.from(firstSet).filter((action) => restSets.every((set) => set.has(action)));
  }, [canExecute, getActions, selectedItems]);

  useEffect(() => {
    if (commonActions.length === 0) {
      setBulkAction('');
      return;
    }

    if (!bulkAction || !commonActions.includes(bulkAction)) {
      setBulkAction(commonActions[0]);
    }
  }, [bulkAction, commonActions]);

  useEffect(() => {
    const visibleIds = new Set(items.map(getId));
    setSelectedIds((current) => {
      const next = new Set(Array.from(current).filter((id) => visibleIds.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [getId, items]);

  const allVisibleSelected =
    selectableItems.length > 0 && selectableItems.every((item) => selectedIds.has(getId(item)));
  const someVisibleSelected = selectableItems.some((item) => selectedIds.has(getId(item)));

  const toggleItem = (item: T) => {
    const id = getId(item);
    const actions = getActions(item);
    if (!onSnooze && !onRemoveSnooze && !actions.some((action) => canExecute(item, action))) {
      return;
    }

    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleAllVisible = () => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (allVisibleSelected) {
        selectableItems.forEach((item) => next.delete(getId(item)));
      } else {
        selectableItems.forEach((item) => next.add(getId(item)));
      }
      return next;
    });
  };

  const openSnoozeDialog = (targetItems: T[]) => {
    if (!onSnooze || targetItems.length === 0) {
      return;
    }
    setSnoozeItems(targetItems);
    setSnoozeDate(defaultExpiryDate());
    setSnoozeReason('');
    setSnoozeError(null);
    setSnoozeReasonError(null);
  };

  const closeSnoozeDialog = () => {
    if (isSnoozing || isRemovingSnooze) {
      return;
    }
    setSnoozeItems([]);
    setSnoozeError(null);
    setSnoozeReasonError(null);
  };

  const submitRemoveSnooze = async () => {
    if (!onRemoveSnooze || activeDialogSnoozeItems.length === 0) {
      return;
    }
    const reason = snoozeReason.trim();
    if (!reason) {
      setSnoozeReasonError('Enter an audit comment before removing the snooze.');
      return;
    }
    setIsRemovingSnooze(true);
    setSnoozeError(null);
    setSnoozeReasonError(null);
    try {
      await onRemoveSnooze(activeDialogSnoozeItems, reason);
      setSelectedIds((current) => {
        const next = new Set(current);
        activeDialogSnoozeItems.forEach((item) => next.delete(getId(item)));
        return next;
      });
      setSnoozeItems([]);
      setSnoozeReason('');
    } catch (error: any) {
      setSnoozeError(error?.response?.data?.detail || error?.message || 'Unable to remove snooze.');
    } finally {
      setIsRemovingSnooze(false);
    }
  };

  const submitSnooze = async () => {
    if (!onSnooze || snoozeItems.length === 0) {
      return;
    }
    if (!snoozeDate) {
      setSnoozeError('Select an expiration date.');
      return;
    }
    const reason = snoozeReason.trim();
    if (!reason) {
      setSnoozeReasonError('Enter a reason or comment.');
      return;
    }
    const expiresAt = new Date(`${snoozeDate}T23:59:59`);
    if (Number.isNaN(expiresAt.getTime()) || expiresAt <= new Date()) {
      setSnoozeError('Select a future expiration date.');
      return;
    }

    setIsSnoozing(true);
    setSnoozeError(null);
    setSnoozeReasonError(null);
    try {
      await onSnooze(snoozeItems, expiresAt.toISOString(), reason);
      setSelectedIds((current) => {
        const next = new Set(current);
        snoozeItems.forEach((item) => next.delete(getId(item)));
        return next;
      });
      setSnoozeItems([]);
      setSnoozeReason('');
    } catch (error: any) {
      setSnoozeError(error?.response?.data?.detail || error?.message || 'Unable to snooze selected resources.');
    } finally {
      setIsSnoozing(false);
    }
  };

  const executeSelected = async () => {
    if (!bulkAction || selectedItems.length === 0) {
      return;
    }

    setIsConfirmOpen(false);
    await Promise.all(selectedItems.map((item) => executeAction(item, bulkAction)));
    setSelectedIds(new Set());
  };

  return (
    <>
      <Modal
        isOpen={isConfirmOpen}
        onClose={() => setIsConfirmOpen(false)}
        title={`Confirm ${resourceLabel} Action`}
        size="xl"
        closeOnOverlayClick={false}
      >
        <div className="space-y-5">
          <div className="rounded-xl border border-warning-200 bg-warning-50 px-4 py-3 text-sm text-warning-900 dark:border-warning-800 dark:bg-warning-950/30 dark:text-warning-100">
            You are about to run <span className="font-semibold">{formatActionLabel(bulkAction || 'selected action')}</span> on{' '}
            <span className="font-semibold">{selectedItems.length}</span> selected {resourceLabel}
            {selectedItems.length === 1 ? '' : 's'}.
          </div>

          <div className="max-h-96 overflow-y-auto rounded-xl border border-gray-200 dark:border-gray-700">
            <table className="min-w-full text-sm">
              <thead className="sticky top-0 bg-gray-50 text-left text-xs uppercase tracking-[0.14em] text-gray-500 dark:bg-gray-900 dark:text-gray-400">
                <tr>
                  {confirmColumns.map((column) => (
                    <th key={column.header} className="px-4 py-3">
                      {column.header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {selectedItems.map((item) => (
                  <tr key={getId(item)} className="text-gray-700 dark:text-gray-200">
                    {confirmColumns.map((column) => (
                      <td key={column.header} className="px-4 py-3">
                        {column.render(item)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={() => setIsConfirmOpen(false)}
              className="rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:border-gray-300 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:hover:bg-gray-800"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={executeSelected}
              disabled={!bulkAction || selectedItems.length === 0}
              className="inline-flex items-center gap-2 rounded-xl bg-warning-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-warning-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Play size={14} />
              Confirm and Execute
            </button>
          </div>
        </div>
      </Modal>

      <Modal
        isOpen={snoozeItems.length > 0}
        onClose={closeSnoozeDialog}
        title={`Snooze ${resourceLabel}${snoozeItems.length === 1 ? '' : 's'}`}
        size="xl"
        closeOnOverlayClick={false}
      >
        <div className="space-y-5">
          <div className="rounded-xl border border-primary-200 bg-primary-50 px-4 py-3 text-sm text-primary-900 dark:border-primary-800 dark:bg-primary-950/30 dark:text-primary-100">
            <span className="font-semibold">{snoozeItems.length}</span> selected {resourceLabel}
            {snoozeItems.length === 1 ? '' : 's'} {activeDialogSnoozeItems.length > 0 ? 'can be updated or removed.' : 'will be excluded from future checks until the expiration date.'}
          </div>

          <div className="max-h-80 overflow-y-auto rounded-xl border border-gray-200 dark:border-gray-700">
            <table className="min-w-full text-sm">
              <thead className="sticky top-0 bg-gray-50 text-left text-xs uppercase tracking-[0.14em] text-gray-500 dark:bg-gray-900 dark:text-gray-400">
                <tr>
                  {(snoozeColumns || confirmColumns).map((column) => (
                    <th key={column.header} className="px-4 py-3">
                      {column.header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {snoozeItems.map((item) => (
                  <tr key={getId(item)} className="text-gray-700 dark:text-gray-200">
                    {(snoozeColumns || confirmColumns).map((column) => (
                      <td key={column.header} className="px-4 py-3">
                        {column.render(item)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="grid gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
            <label className="block">
              <span className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
                <Calendar size={14} />
                Update Expiration
              </span>
              <input
                type="date"
                value={snoozeDate}
                min={formatDateInput(new Date())}
                onChange={(event) => setSnoozeDate(event.target.value)}
                className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-xs font-semibold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
                Audit Comment {activeDialogSnoozeItems.length > 0 ? '(Update / Remove)' : ''}
              </span>
              <textarea
                value={snoozeReason}
                onChange={(event) => {
                  setSnoozeReason(event.target.value);
                  if (snoozeReasonError && event.target.value.trim()) {
                    setSnoozeReasonError(null);
                  }
                }}
                rows={4}
                className={cn(
                  'w-full resize-none rounded-xl border bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:ring-2 dark:bg-gray-900/80 dark:text-gray-100',
                  snoozeReasonError
                    ? 'border-danger-300 focus:border-danger-400 focus:ring-danger-100 dark:border-danger-800 dark:focus:border-danger-600 dark:focus:ring-danger-950/40'
                    : 'border-gray-200 focus:border-warning-300 focus:ring-warning-100 dark:border-gray-700 dark:focus:border-warning-500 dark:focus:ring-warning-900/40'
                )}
              />
              {snoozeReasonError ? <div className="mt-2 text-sm font-medium text-danger-600 dark:text-danger-300">{snoozeReasonError}</div> : null}
            </label>
          </div>

          {snoozeError ? <div className="text-sm font-medium text-danger-600 dark:text-danger-300">{snoozeError}</div> : null}

          <div className="flex items-center justify-end gap-3">
            {onRemoveSnooze && activeDialogSnoozeItems.length > 0 ? (
              <button
                type="button"
                onClick={submitRemoveSnooze}
                disabled={isSnoozing || isRemovingSnooze}
                className="mr-auto inline-flex items-center gap-2 rounded-xl border border-danger-200 bg-white px-4 py-2 text-sm font-semibold text-danger-700 transition hover:border-danger-300 hover:bg-danger-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-danger-800 dark:bg-gray-900 dark:text-danger-200 dark:hover:bg-danger-950/30"
              >
                <Clock size={14} />
                {isRemovingSnooze ? 'Removing...' : 'Remove Snooze'}
              </button>
            ) : null}
            <button
              type="button"
              onClick={closeSnoozeDialog}
              disabled={isSnoozing || isRemovingSnooze}
              className="rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:border-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:hover:bg-gray-800"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={submitSnooze}
              disabled={isSnoozing || isRemovingSnooze || snoozeItems.length === 0}
              className="inline-flex items-center gap-2 rounded-xl bg-primary-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Clock size={14} />
              {isSnoozing ? 'Snoozing...' : 'Snooze'}
            </button>
          </div>
        </div>
      </Modal>

      <div className={cn('rounded-lg border border-gray-200 bg-white/95 p-6 dark:border-gray-700 dark:bg-gray-900/95', className)}>
        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">{title}</div>
            {subtitle ? <div className="mt-1 text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{subtitle}</div> : null}
          </div>
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center">
            <label className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100">
              <input
                type="checkbox"
                checked={allVisibleSelected}
                onChange={toggleAllVisible}
                disabled={selectableItems.length === 0}
                className="h-4 w-4 rounded border-gray-300 text-warning-500 focus:ring-warning-400 disabled:cursor-not-allowed disabled:opacity-50"
              />
              {someVisibleSelected ? `${selectedItems.length} selected` : 'Select visible'}
            </label>
            <select
              value={bulkAction}
              onChange={(event) => setBulkAction(event.target.value)}
              disabled={selectedItems.length === 0 || commonActions.length === 0}
              className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
            >
              {commonActions.length === 0 ? (
                <option value="">No shared action</option>
              ) : (
                commonActions.map((action) => (
                  <option key={action} value={action}>
                    {formatActionLabel(action)}
                  </option>
                ))
              )}
            </select>
            <button
              type="button"
              onClick={() => setIsConfirmOpen(true)}
              disabled={selectedItems.length === 0 || !bulkAction || commonActions.length === 0}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-warning-500 px-3 py-2 text-sm font-semibold text-white transition hover:bg-warning-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Play size={14} />
              Execute Selected
            </button>
            {onSnooze ? (
              <button
                type="button"
                onClick={() => openSnoozeDialog(selectedItems)}
                disabled={selectedItems.length === 0}
                className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Clock size={14} />
                Snooze Selected
              </button>
            ) : null}
            {getSnooze ? (
              <select
                value={snoozeFilter}
                onChange={(event) => setSnoozeFilter(event.target.value as 'all' | 'snoozed' | 'not-snoozed')}
                className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
              >
                <option value="all">All snooze states</option>
                <option value="snoozed">Snoozed only</option>
                <option value="not-snoozed">Not snoozed</option>
              </select>
            ) : null}
            {toolbar}
          </div>
        </div>

        <div className="h-[34rem] space-y-3 overflow-y-auto pr-2">
          {visibleItems.length === 0 ? (
            <div className="flex h-full items-center justify-center rounded-2xl border border-dashed border-gray-200 text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
              No visible {resourceLabel}s match the current filters.
            </div>
          ) : (
            visibleItems.map((item) => {
              const id = getId(item);
              const isSelected = selectedIds.has(id);
              const isSelectable = Boolean(onSnooze || onRemoveSnooze) || getActions(item).some((action) => canExecute(item, action));
              const snooze = getSnooze?.(item);
              const isSnoozed = Boolean(snooze?.active);
              return (
                <div
                  key={id}
                  className="relative"
                >
                  <div
                    className={cn(
                    onSnooze || onRemoveSnooze ? '[&>*]:pl-14 [&>*]:pr-32' : '[&>*]:pl-14',
                      isSelected && 'rounded-2xl ring-2 ring-warning-200 dark:ring-warning-900/60'
                    )}
                  >
                    {renderItem(item)}
                  </div>
                  <div className="absolute left-4 top-5 z-10 flex items-center justify-center">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      disabled={!isSelectable}
                      onChange={() => toggleItem(item)}
                      onClick={(event) => event.stopPropagation()}
                      aria-label={`Select ${getName(item)}`}
                      className="h-4 w-4 rounded border-gray-300 text-warning-500 focus:ring-warning-400 disabled:cursor-not-allowed disabled:opacity-40"
                    />
                  </div>
                  {onSnooze || onRemoveSnooze ? (
                    <div className="absolute right-4 top-4 z-10 flex w-24 flex-col items-stretch gap-2">
                      {isSnoozed ? (
                        <div className="rounded-lg border border-primary-200 bg-primary-50 px-2 py-1 text-center text-[11px] font-semibold text-primary-700 dark:border-primary-800 dark:bg-primary-950/30 dark:text-primary-200">
                          Until {formatSnoozeDate(snooze?.snoozed_until)}
                        </div>
                      ) : null}
                      {onSnooze ? (
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            openSnoozeDialog([item]);
                          }}
                          className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-primary-200 bg-white px-2.5 py-2 text-xs font-semibold text-primary-700 transition hover:border-primary-300 hover:bg-primary-50 dark:border-primary-800 dark:bg-gray-900 dark:text-primary-200 dark:hover:bg-primary-950/40"
                        >
                          <Clock size={14} />
                          {isSnoozed ? 'Update' : 'Snooze'}
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
        </div>
      </div>
    </>
  );
}
