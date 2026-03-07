import type { NextConfig } from "next";

const securityHeaders = [
  // Prevent clickjacking
  { key: "X-Frame-Options",        value: "DENY" },
  // Stop MIME-type sniffing
  { key: "X-Content-Type-Options", value: "nosniff" },
  // Only send referrer on same origin
  { key: "Referrer-Policy",        value: "strict-origin-when-cross-origin" },
  // Disable browser features not needed by a trading terminal
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=()",
  },
  // Content Security Policy
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self'",
      "connect-src 'self' https://paper-api.alpaca.markets https://api.alpaca.markets wss://paper-api.alpaca.markets wss://stream.data.alpaca.markets",
      "frame-ancestors 'none'",
    ].join("; "),
  },
  { key: "X-XSS-Protection", value: "1; mode=block" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  typescript: { ignoreBuildErrors: true },
  serverExternalPackages: ["pg"],
  env: {
    APEX_API_URL:    process.env.APEX_API_URL    ?? "http://localhost:8000",
    MLFLOW_API_URL:  process.env.MLFLOW_API_URL  ?? "http://localhost:5000",
    ALPACA_BASE_URL: process.env.ALPACA_BASE_URL ?? "https://paper-api.alpaca.markets",
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
