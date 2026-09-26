import React from 'react';
import { cn } from '@/utils/helpers';

interface CardProps {
  children: React.ReactNode;
  className?: string;
  title?: string;
  actions?: React.ReactNode;
}

export const Card: React.FC<CardProps> = ({ children, className, title, actions }) => {
  return (
    <div className={cn('card', className)}>
      {(title || actions) && (
        <div className="flex items-center justify-between mb-4">
          {title && <h3 className="text-lg font-semibold text-gray-900 dark:text-white">{title}</h3>}
          {actions && <div>{actions}</div>}
        </div>
      )}
      {children}
    </div>
  );
};

