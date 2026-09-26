import React, { useState } from 'react';
import { ChevronDown, Check } from 'lucide-react';
import type { PolicyTemplate } from '@/types/api';

interface TemplateSelectorProps {
  templates: PolicyTemplate[];
  onSelect: (template: PolicyTemplate) => void;
}

export const TemplateSelector: React.FC<TemplateSelectorProps> = ({
  templates,
  onSelect,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [selected, setSelected] = useState<PolicyTemplate | null>(null);

  const handleSelect = (template: PolicyTemplate) => {
    setSelected(template);
    onSelect(template);
    setIsOpen(false);
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="input flex items-center justify-between w-full"
      >
        <span className={selected ? 'text-gray-900' : 'text-gray-500'}>
          {selected ? selected.name : 'Select a template...'}
        </span>
        <ChevronDown
          size={20}
          className={`text-gray-400 transition-transform ${isOpen ? 'rotate-180' : ''}`}
        />
      </button>

      {isOpen && (
        <>
          <div
            className="fixed inset-0 z-10"
            onClick={() => setIsOpen(false)}
          />
          <div className="absolute z-20 w-full mt-1 bg-white border border-gray-300 rounded-lg shadow-lg max-h-60 overflow-auto">
            {templates.map((template) => (
              <button
                key={template.id}
                type="button"
                onClick={() => handleSelect(template)}
                className="w-full text-left px-4 py-3 hover:bg-gray-50 flex items-center justify-between border-b border-gray-100 last:border-0"
              >
                <div className="flex-1">
                  <div className="font-medium text-gray-900">{template.name}</div>
                  <div className="text-sm text-gray-500 mt-1">{template.description}</div>
                  <div className="text-xs text-gray-400 mt-1">
                    {template.category} • {template.resource_type}
                  </div>
                </div>
                {selected?.id === template.id && (
                  <Check size={20} className="text-primary-600 ml-2" />
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
};

