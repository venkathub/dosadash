/** @type {import('tailwindcss').Config} */
// Madras Pop tokens — docs/13-ui-madras-pop-design.md
// (normative source: design/madras-pop/tokens.css)
//
// NOTE: values are literal hex (not var(--x)) so Tailwind opacity modifiers
// (bg-indigo-900/95, text-turmeric-400/80, …) work — the Phase 10 lesson.
// Keep in sync with the CSS variables in app/globals.css.
//
// TRANSITIONAL: the Heritage Luxe scales (leaf/brass/cream/ink-N/info) are kept
// as ALIASES re-pointed at Madras Pop hexes so un-migrated call sites render the
// new palette. PRs 2–4 migrate call sites; the polish PR deletes the aliases.
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        // ---------- Madras Pop canonical ----------
        indigo: {
          950: "#12122B",
          900: "#1B1B3A",
          800: "#232347",
          700: "#2E2E5C",
          600: "#3D3D73",
          300: "#8B8BC0",
          200: "#B9B6D9",
          100: "#EDEAF6",
        },
        magenta: {
          700: "#A81848",
          600: "#C21F58",
          500: "#D6336C",
          400: "#E85D8A",
          100: "#FBE3ED",
        },
        turmeric: {
          600: "#D9A404",
          500: "#F2B705",
          400: "#FFCB2E",
          100: "#FCEEC5",
          // legacy alias (Heritage Luxe turmeric-200)
          200: "#FCEEC5",
        },
        offwhite: "#FAF7F0",
        paper: "#FFFFFF",
        sand: {
          200: "#F1EAD8",
          300: "#E5DCC8",
        },
        ink: {
          DEFAULT: "#1B1B3A",
          // legacy aliases (ink-900/600/400 → ink/muted/faint)
          900: "#1B1B3A",
          600: "#5A5A78",
          400: "#8E8EA8",
        },
        muted: "#5A5A78",
        faint: "#8E8EA8",
        veg: {
          DEFAULT: "#1E8A5A",
          100: "#D7F0E3",
          // legacy aliases
          600: "#1E8A5A",
          500: "#1E8A5A",
          200: "#D7F0E3",
        },
        chili: {
          DEFAULT: "#D64545",
          100: "#FADEDE",
          // legacy aliases
          600: "#D64545",
          500: "#D64545",
          200: "#FADEDE",
        },
        sky: {
          DEFAULT: "#3E7CB1",
          100: "#DCEAF5",
        },
        warn: {
          DEFAULT: "#D9A404",
          100: "#FCEEC5",
        },
        // ---------- Heritage Luxe aliases (deprecated — delete in polish PR) ----------
        leaf: {
          950: "#12122B",
          900: "#1B1B3A",
          800: "#232347",
          700: "#2E2E5C",
          600: "#3D3D73",
          500: "#8B8BC0",
          200: "#B9B6D9",
          100: "#EDEAF6",
        },
        brass: {
          600: "#D9A404",
          500: "#F2B705",
          400: "#FFCB2E",
          300: "#FFCB2E",
        },
        cream: {
          50: "#FFFFFF",
          100: "#FAF7F0",
          200: "#F1EAD8",
          300: "#E5DCC8",
        },
        info: {
          500: "#3E7CB1",
          200: "#DCEAF5",
        },
      },
      fontFamily: {
        display: ["Space Grotesk", "Noto Sans Tamil", "system-ui", "sans-serif"],
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
        // Madras Pop signature: hard offset shadows, never blurred
        pop: "4px 4px 0 #1B1B3A",
        "pop-sm": "3px 3px 0 #1B1B3A",
        "pop-xs": "2px 2px 0 #1B1B3A",
        "pop-magenta": "4px 4px 0 #C21F58",
        "pop-magenta-sm": "3px 3px 0 #D6336C",
        "pop-turmeric": "4px 4px 0 #F2B705",
        "pop-dark": "4px 4px 0 #0C0C1F",
        "pop-dark-sm": "3px 3px 0 #0C0C1F",
        // legacy aliases (deprecated — delete in polish PR)
        card: "3px 3px 0 #1B1B3A",
        lift: "4px 4px 0 #1B1B3A",
        modal: "8px 8px 0 rgb(12 12 31 / 0.45)",
      },
    },
  },
  plugins: [],
};
