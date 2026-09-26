import React, { useState } from 'react';
import { Modal } from '@/components/common/Modal';
import { Button } from '@/components/common/Button';
import { Clock } from 'lucide-react';

interface SnoozeDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (days: number) => void;
  resourceId: string;
}

export const SnoozeDialog: React.FC<SnoozeDialogProps> = ({
  isOpen,
  onClose,
  onConfirm,
  resourceId,
}) => {
  const [days, setDays] = useState<number>(7);
  const [error, setError] = useState<string>('');

  const handleConfirm = () => {
    if (days < 1 || days > 365) {
      setError('Please enter a number between 1 and 365 days');
      return;
    }
    setError('');
    onConfirm(days);
    onClose();
  };

  const quickOptions = [7, 14, 30, 60, 90];

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Snooze Resource"
      size="md"
    >
      <div className="space-y-4">
        <div>
          <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
            Snooze <strong>{resourceId}</strong> to exclude it from check results for a specified number of days.
          </p>
          
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Number of Days
            </label>
            <div className="flex items-center space-x-2">
              <input
                type="number"
                min="1"
                max="365"
                value={days}
                onChange={(e) => {
                  const value = parseInt(e.target.value) || 0;
                  setDays(value);
                  setError('');
                }}
                className="flex-1 px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <span className="text-sm text-gray-600 dark:text-gray-400">days</span>
            </div>
            {error && (
              <p className="mt-1 text-sm text-danger-600 dark:text-danger-400">{error}</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Quick Options
            </label>
            <div className="flex flex-wrap gap-2">
              {quickOptions.map((option) => (
                <button
                  key={option}
                  onClick={() => {
                    setDays(option);
                    setError('');
                  }}
                  className={`px-3 py-1 text-sm rounded-lg border transition-colors ${
                    days === option
                      ? 'bg-primary-100 dark:bg-primary-900/30 border-primary-500 text-primary-700 dark:text-primary-300'
                      : 'bg-gray-100 dark:bg-gray-700 border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'
                  }`}
                >
                  {option} days
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="flex justify-end space-x-3 pt-4 border-t border-gray-200 dark:border-gray-700">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={handleConfirm}>
            <Clock size={16} className="mr-2" />
            Snooze Resource
          </Button>
        </div>
      </div>
    </Modal>
  );
};
