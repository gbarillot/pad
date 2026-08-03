import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        clinic: {
          50: "#f6fbff",
          100: "#eef7ff",
          200: "#d9edff",
          500: "#3388d8",
          600: "#1f6fb8",
          700: "#185a96",
        },
      },
      boxShadow: {
        neo: "12px 12px 28px rgba(151, 165, 185, 0.28), -12px -12px 28px rgba(255, 255, 255, 0.95)",
        "neo-inset": "inset 7px 7px 16px rgba(151, 165, 185, 0.2), inset -7px -7px 16px rgba(255, 255, 255, 0.9)",
        "soft-blue": "0 18px 45px rgba(51, 136, 216, 0.16)",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
      },
    },
  },
  plugins: [],
} satisfies Config;
