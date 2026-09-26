/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Neutral chrome — navy-tinted, replaces Tailwind's stock gray so every
        // existing `gray-*` class (body/card/border/text) picks it up for free.
        gray: {
          50: '#f5f7fb',
          100: '#e9edf6',
          200: '#d3daeb',
          300: '#aab6d1',
          400: '#7e8db3',
          500: '#59678f',
          600: '#414d72',
          700: '#2c3555',
          800: '#182140',
          900: '#0e1830',
          950: '#070c16',
        },
        // Brand teal — replaces the stock blue `primary` scale.
        primary: {
          50: '#ecfdf9',
          100: '#d1faf0',
          200: '#a3f3e1',
          300: '#6ce8ce',
          400: '#34e0c4',
          500: '#16c1a8',
          600: '#0d9d89',
          700: '#0f7d6f',
          800: '#12645a',
          900: '#12524a',
          950: '#0a2f2a',
        },
        success: {
          50: '#f0fdf4',
          100: '#dcfce7',
          200: '#bbf7d0',
          300: '#86efac',
          400: '#4ade80',
          500: '#22c55e',
          600: '#16a34a',
          700: '#15803d',
          800: '#166534',
          900: '#14532d',
          950: '#052e16',
        },
        // Warning — nudged from stock amber to the brand's gold.
        warning: {
          50: '#fffbeb',
          100: '#fef3c7',
          200: '#fde8a3',
          300: '#fbd66b',
          400: '#f8bf3d',
          500: '#f5a623',
          600: '#d1860f',
          700: '#a8670c',
          800: '#7d4d0f',
          900: '#5c3a0f',
          950: '#3d2609',
        },
        // Danger — nudged from stock red to the brand's rose.
        danger: {
          50: '#fff1f3',
          100: '#ffe0e5',
          200: '#ffc2cd',
          300: '#ff96a8',
          400: '#fa6b85',
          500: '#ef4460',
          600: '#d42a4b',
          700: '#ad1f3c',
          800: '#841a34',
          900: '#6b1a2f',
          950: '#451022',
        },
      },
    },
  },
  plugins: [],
}

