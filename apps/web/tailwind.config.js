/** @type {import('tailwindcss').Config} */
// Tawa Noir tokens — docs/17-ui-tawa-noir-design.md
//
// NOTE: values are literal hex (not var(--x)) so Tailwind opacity modifiers
// (bg-soot-950/95, text-cream-dim/70, …) work — the Phase 10 lesson.
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        // surfaces, darkest first
        soot: { 950: "#0B0907", 900: "#14100C", 800: "#1F1913", 700: "#2E261D" },
        // primary action / live state. `ink` is the text colour ON ember.
        ember: { DEFAULT: "#FF6A1A", hot: "#FF8F4D", ink: "#1A0B02" },
        // active markers, attention, focus ring
        ghee: "#F5C451",
        // text: primary / secondary / tertiary (never below 12px for faint)
        cream: { DEFAULT: "#F6EFE2", dim: "#C9BDA9", faint: "#9C907D" },
        // semantic, tuned for dark surfaces
        basil: "#37C27E",
        veg: { DEFAULT: "#37C27E", 100: "#12301F" },
        chili: { DEFAULT: "#FF5D5D", 100: "#3A1616" },
        sky: { DEFAULT: "#6FB1E6", 100: "#132A3D" },
        warn: { DEFAULT: "#F5C451", 100: "#3A2E0E" },
      },
      fontFamily: {
        display: ["Space Grotesk", "Noto Sans Tamil", "system-ui", "sans-serif"],
        sans: ["Inter", "Noto Sans Tamil", "ui-sans-serif", "system-ui", "-apple-system", "sans-serif"],
      },
      boxShadow: {
        noir: "0 30px 80px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.06)",
        ember: "0 0 34px rgba(255,106,26,0.45)",
      },
    },
  },
  plugins: [],
};
