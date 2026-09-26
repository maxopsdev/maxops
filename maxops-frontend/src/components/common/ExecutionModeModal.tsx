import React, { useState } from 'react';
import { AlertTriangle, Play, Eye } from 'lucide-react';
import { Modal } from './Modal';
import { Button } from './Button';

interface ExecutionModeModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (mode: 'dry-run' | 'apply') => void;
  policyName?: string;
  isMultiple?: boolean;
}

export const ExecutionModeModal: React.FC<ExecutionModeModalProps> = ({
  isOpen,
  onClose,
  onConfirm,
  policyName,
  isMultiple = false,
}) => {
  const [selectedMode, setSelectedMode] = useState<'dry-run' | 'apply'>('dry-run');

  const handleConfirm = () => {
    if (selectedMode === 'apply') {
      const confirmMessage = isMultiple
        ? 'Are you sure you want to APPLY these policies? This will make actual changes to your cloud resources!'
        : `Are you sure you want to APPLY the policy "${policyName}"? This will make actual changes to your cloud resources!`;
      
      if (window.confirm(confirmMessage)) {
        onConfirm(selectedMode);
        onClose();
      }
    } else {
      onConfirm(selectedMode);
      onClose();
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Select Execution Mode" size="md">
      <div className="space-y-6">
        <div>
          <p className="text-gray-600 dark:text-gray-400 mb-4">
            {isMultiple
              ? 'Choose how you want to execute the selected policies:'
              : `Choose how you want to execute "${policyName}":`}
          </p>

          <div className="space-y-3">
            {/* Dry Run Option */}
            <label className="flex items-start p-4 border-2 border-gray-200 dark:border-gray-700 rounded-lg cursor-pointer hover:border-primary-500 dark:hover:border-primary-500 transition-colors">
              <input
                type="radio"
                name="executionMode"
                value="dry-run"
                checked={selectedMode === 'dry-run'}
                onChange={() => setSelectedMode('dry-run')}
                className="mt-1 mr-3"
              />
              <div className="flex-1">
                <div className="flex items-center space-x-2 mb-1">
                  <Eye size={18} className="text-primary-600 dark:text-primary-400" />
                  <span className="font-semibold text-gray-900 dark:text-white">Dry Run</span>
                  <span className="px-2 py-0.5 text-xs bg-success-100 dark:bg-success-900 text-success-700 dark:text-success-300 rounded">
                    Safe
                  </span>
                </div>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  Simulate execution and show what would happen without making any changes
                </p>
              </div>
            </label>

            {/* Apply Option */}
            <label className="flex items-start p-4 border-2 border-gray-200 dark:border-gray-700 rounded-lg cursor-pointer hover:border-primary-500 dark:hover:border-primary-500 transition-colors">
              <input
                type="radio"
                name="executionMode"
                value="apply"
                checked={selectedMode === 'apply'}
                onChange={() => setSelectedMode('apply')}
                className="mt-1 mr-3"
              />
              <div className="flex-1">
                <div className="flex items-center space-x-2 mb-1">
                  <Play size={18} className="text-danger-600 dark:text-danger-400" />
                  <span className="font-semibold text-gray-900 dark:text-white">Apply</span>
                  <span className="px-2 py-0.5 text-xs bg-danger-100 dark:bg-danger-900 text-danger-700 dark:text-danger-300 rounded">
                    Live
                  </span>
                </div>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  Execute the policy and make actual changes to your cloud resources
                </p>
              </div>
            </label>
          </div>
        </div>

        {selectedMode === 'apply' && (
          <div className="bg-danger-50 dark:bg-danger-900/20 border border-danger-200 dark:border-danger-800 rounded-lg p-4">
            <div className="flex items-start space-x-3">
              <AlertTriangle size={20} className="text-danger-600 dark:text-danger-400 mt-0.5 flex-shrink-0" />
              <div>
                <h4 className="font-semibold text-danger-800 dark:text-danger-300 mb-1">
                  Warning: Live Execution
                </h4>
                <p className="text-sm text-danger-700 dark:text-danger-400">
                  This will make actual changes to your cloud resources. Make sure you understand the policy actions before proceeding.
                </p>
              </div>
            </div>
          </div>
        )}

        <div className="flex items-center justify-end space-x-3 pt-4 border-t border-gray-200 dark:border-gray-800">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleConfirm}
          >
            {selectedMode === 'apply' ? 'Apply Policy' : 'Run Dry-Run'}
          </Button>
        </div>
      </div>
    </Modal>
  );
};

