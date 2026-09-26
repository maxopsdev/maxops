import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { 
  type LucideIcon,
  LayoutDashboard, 
  Gauge,
  FileText, 
  Bell, 
  Shield, 
  Coins,
  Settings 
} from 'lucide-react';

interface NavItem {
  name: string;
  path: string;
  icon: LucideIcon;
}

const navItems: NavItem[] = [
  { name: 'Dashboard', path: '/dashboard', icon: LayoutDashboard },
  { name: 'Rightsizer', path: '/rightsizer', icon: Gauge },
  { name: 'Checks Manager', path: '/policies', icon: FileText },
  { name: 'Notification', path: '/notifications', icon: Bell },
  { name: 'Security', path: '/security', icon: Shield },
  { name: 'Cost Data', path: '/settings/cost-data', icon: Coins },
  { name: 'Settings', path: '/settings', icon: Settings },
];

export const Sidebar: React.FC = () => {
  const location = useLocation();

  return (
    <aside className="w-64 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 min-h-screen fixed left-0 top-0 pt-16 z-30">
      <nav className="p-4 space-y-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive =
            location.pathname === item.path ||
            (item.path === '/dashboard' &&
              (location.pathname === '/' || location.pathname.startsWith('/dashboard')));
          
          return (
            <Link
              key={item.path}
              to={item.path}
              className={`flex items-center space-x-3 px-4 py-3 rounded-lg transition-colors ${
                isActive
                  ? 'bg-primary-50 dark:bg-primary-900/20 text-primary-600 dark:text-primary-400 font-medium'
                  : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-primary-600 dark:hover:text-primary-400'
              }`}
            >
              <Icon size={20} />
              <span>{item.name}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
};

