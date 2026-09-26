/** Utility functions for formatting data */

export const formatDate = (dateString: string): string => {
  const date = new Date(dateString);
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

export const formatRelativeTime = (dateString: string): string => {
  const date = new Date(dateString);
  const now = new Date();
  const diffInSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);

  if (diffInSeconds < 60) return 'just now';
  if (diffInSeconds < 3600) return `${Math.floor(diffInSeconds / 60)}m ago`;
  if (diffInSeconds < 86400) return `${Math.floor(diffInSeconds / 3600)}h ago`;
  if (diffInSeconds < 604800) return `${Math.floor(diffInSeconds / 86400)}d ago`;
  
  return formatDate(dateString);
};

export const formatStatus = (status: string): string => {
  return status.charAt(0).toUpperCase() + status.slice(1).replace('-', ' ');
};

export const getStatusColor = (status: string): string => {
  switch (status.toLowerCase()) {
    case 'active':
    case 'completed':
      return 'text-success-700 bg-success-100 dark:text-success-300 dark:bg-success-900/30';
    case 'inactive':
    case 'failed':
      return 'text-danger-700 bg-danger-100 dark:text-danger-300 dark:bg-danger-900/30';
    case 'running':
      return 'text-warning-700 bg-warning-100 dark:text-warning-300 dark:bg-warning-900/30';
    default:
      return 'text-gray-700 bg-gray-100 dark:text-gray-300 dark:bg-gray-800';
  }
};

export const formatCurrency = (amount: number | undefined | null): string => {
  if (amount === undefined || amount === null || isNaN(amount)) {
    return '-';
  }
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
};

