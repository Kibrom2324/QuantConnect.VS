import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        apex: {
          bg:      "#050810",
          surface: "#0a0f1e",
          panel:   "#0d1624",
          border:  "#1a2a40",
          muted:   "#1f3050",
          text:    "#e2f0ff",
          subtext: "#4a6a8a",
          cyan:    "#00D4FF",
          red:     "#FF2D55",
          green:   "#00FF88",
          amber:   "#FFB800",
          purple:  "#8B5CF6",
          blue:    "#3b82f6",
        },
      },
      fontFamily: {
        mono:    ["'JetBrains Mono'", "ui-monospace", "monospace"],
        heading: ["'Syne'", "sans-serif"],
        sans:    ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
