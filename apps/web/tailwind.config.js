/** @type {import('tailwindcss').Config} */
// Heritage Luxe tokens — docs/12-ui-premium-design.md
// NOTE: values are literal hex (not var(--x)) so Tailwind opacity modifiers
// (bg-leaf-800/95, text-brass-300/80, …) work. Keep in sync with the CSS
// variables in app/globals.css (single place each: CSS vars feed the custom
// component utilities, these feed utility classes).
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        leaf: {
          950: "#0b1f1a",
          900: "#10291f",
          800: "#14342b",
          700: "#1c4436",
          600: "#2a5a47",
          500: "#3e7258",
          200: "#bfd6c8",
          100: "#dce9df",
        },
        brass: {
          600: "#a88434",
          500: "#c8a24b",
          400: "#ddbc6e",
          300: "#ebd49a",
        },
        cream: {
          50: "#fdfbf5",
          100: "#fbf6ec",
          200: "#f3ead7",
          300: "#e7d9be",
        },
        ink: {
          900: "#1f2421",
          600: "#55605a",
          400: "#8a948d",
        },
        chili: {
          600: "#b3372b",
          500: "#d0483a",
          200: "#f3c4be",
        },
        veg: {
          600: "#256c43",
          500: "#2f8a56",
          200: "#c4e3d1",
        },
        turmeric: {
          500: "#d99a2b",
          200: "#f4dfb4",
        },
        info: {
          500: "#3e7cb1",
          200: "#c3daec",
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
