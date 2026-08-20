/** @type {import('tailwindcss').Config} */
// Heritage Luxe tokens — docs/12-ui-premium-design.md
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        leaf: {
          950: "var(--leaf-950)",
          900: "var(--leaf-900)",
          800: "var(--leaf-800)",
          700: "var(--leaf-700)",
          600: "var(--leaf-600)",
          500: "var(--leaf-500)",
          200: "var(--leaf-200)",
          100: "var(--leaf-100)",
        },
        brass: {
          600: "var(--brass-600)",
          500: "var(--brass-500)",
          400: "var(--brass-400)",
          300: "var(--brass-300)",
        },
        cream: {
          50: "var(--cream-50)",
          100: "var(--cream-100)",
          200: "var(--cream-200)",
          300: "var(--cream-300)",
        },
        ink: {
          900: "var(--ink-900)",
          600: "var(--ink-600)",
          400: "var(--ink-400)",
        },
        chili: {
          600: "var(--chili-600)",
          500: "var(--chili-500)",
          200: "var(--chili-200)",
        },
        veg: {
          600: "var(--veg-600)",
          500: "var(--veg-500)",
          200: "var(--veg-200)",
        },
        turmeric: {
          500: "var(--turmeric-500)",
          200: "var(--turmeric-200)",
        },
        info: {
          500: "var(--info-500)",
          200: "var(--info-200)",
        },
      },
      fontFamily: {
        display: ["Fraunces", "Noto Sans Tamil", "Georgia", "serif"],
        sans: [
          "Inter",
          "Noto Sans Tamil",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "sans-serif",
        ],
      },
      boxShadow: {
        card: "0 1px 2px rgb(31 36 33 / 0.06), 0 4px 16px rgb(31 36 33 / 0.06)",
        lift: "0 2px 4px rgb(31 36 33 / 0.08), 0 10px 28px rgb(31 36 33 / 0.12)",
        modal: "0 24px 64px rgb(11 31 26 / 0.35)",
      },
    },
  },
  plugins: [],
};
